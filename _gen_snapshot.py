"""
_gen_snapshot.py — Full Phase 14 snapshot generator.

Produces all missing artifacts, then builds project_snapshot.zip.
Run from project root: python _gen_snapshot.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
ARTIFACTS = ROOT / "artifacts"
ARTIFACTS.mkdir(exist_ok=True)
(ARTIFACTS / "replay_examples").mkdir(exist_ok=True)
(ARTIFACTS / "decision_examples").mkdir(exist_ok=True)

BASE = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)

# ─── imports from real pipeline ───────────────────────────────────────────────
sys.path.insert(0, str(ROOT))

from calibration.probability_mapper import map_to_event_probability
from calibration.types import (
    CalibrationMethod, CalibrationQuality,
    LIVE_CAL_CONFIG, PAPER_LOOSE_CAL_CONFIG, PAPER_STRICT_CAL_CONFIG,
    MarketPricingSnapshot, RawSignalOutput,
)
from shadow_runner.runner import ShadowRunner, reconstruct_calibrated_signal, reconstruct_pricing_snapshot
from shadow_runner.journal import JournalWriter
from shadow_runner.types import ShadowDecisionRecord
from shadow_runner.summary_metrics import compute_summary_metrics
from shadow_runner.validation import check_evidence_sufficiency, EvidenceRequirements
from shadow_runner.readiness import assess_readiness, format_readiness_report, LIVE_PILOT_CONSTRAINTS
from shadow_runner.reporting import generate_comparison_report, format_report
from monitoring.regime_review import compute_regime_review


# ─── helpers ─────────────────────────────────────────────────────────────────

def _signal(confidence: float, quality=CalibrationQuality.STRONG, asset="BTC", horizon=15):
    method = CalibrationMethod.PLATT if quality == CalibrationQuality.STRONG else CalibrationMethod.IDENTITY
    raw = RawSignalOutput(
        asset=asset, horizon_minutes=horizon, timestamp_utc=BASE,
        predicted_class="UP", raw_confidence=confidence,
        class_probabilities={
            "UP": confidence,
            "DOWN": round((1 - confidence) * 0.6, 6),
            "NO_TRADE": round((1 - confidence) * 0.4, 6),
        },
    )
    cal, err = map_to_event_probability(
        raw=raw,
        calibrated_up_prob=confidence,
        calibrated_down_prob=round((1 - confidence) * 0.6, 6),
        calibrated_no_trade_prob=round((1 - confidence) * 0.4, 6),
        polarity="NORMAL",
        calibration_method=method,
        calibration_quality=quality,
    )
    assert err is None, err
    return cal


def _snap(i=0, age_seconds=0.0, liquidity=5000.0, ask_yes=0.44,
          ask_no=0.57, asset="BTC"):
    ts = BASE - timedelta(seconds=age_seconds)
    return MarketPricingSnapshot(
        market_id=f"mkt-{asset.lower()}-{i:04d}",
        ask_yes=ask_yes, bid_yes=round(ask_yes - 0.02, 4),
        ask_no=ask_no, bid_no=round(ask_no - 0.02, 4),
        liquidity=liquidity, timestamp_utc=ts,
    )


def _underround_snap(i=0):
    """ask_yes + ask_no = 0.96 → suspicious underround"""
    return MarketPricingSnapshot(
        market_id=f"mkt-ur-{i:04d}",
        ask_yes=0.46, bid_yes=0.44,
        ask_no=0.50, bid_no=0.48,
        liquidity=5000.0, timestamp_utc=BASE,
    )


def serialize_record(r: ShadowDecisionRecord) -> dict:
    return {
        "schema_version": "1",
        "record_id": r.record_id,
        "run_id": r.run_id,
        "ts_recorded_utc": r.ts_recorded_utc.isoformat(),
        "policy_profile": r.policy_profile,
        "intended_size_usdc": r.intended_size_usdc,
        "signal": {
            "asset": r.signal.asset,
            "horizon_minutes": r.signal.horizon_minutes,
            "signal_timestamp_utc": r.signal.signal_timestamp_utc.isoformat(),
            "predicted_class": r.signal.predicted_class,
            "raw_confidence": r.signal.raw_confidence,
            "class_probabilities": r.signal.class_probabilities,
            "model_version": r.signal.model_version or "v0",
            "calibrated_up_prob": r.signal.calibrated_up_prob,
            "calibrated_down_prob": r.signal.calibrated_down_prob,
            "calibrated_no_trade_prob": r.signal.calibrated_no_trade_prob,
            "calibration_method": r.signal.calibration_method,
            "calibration_quality": r.signal.calibration_quality,
            "effective_yes_prob": r.signal.effective_yes_prob,
            "effective_no_prob": r.signal.effective_no_prob,
            "mapping_context": r.signal.mapping_context,
            "bridge_intent_side": r.signal.bridge_intent_side,
            "brier_score": r.signal.brier_score,
            "ece": r.signal.ece,
        },
        "pricing": {
            "market_id": r.pricing.market_id,
            "ask_yes": r.pricing.ask_yes,
            "bid_yes": r.pricing.bid_yes,
            "ask_no": r.pricing.ask_no,
            "bid_no": r.pricing.bid_no,
            "liquidity": r.pricing.liquidity,
            "pricing_timestamp_utc": r.pricing.pricing_timestamp_utc.isoformat(),
            "snapshot_age_seconds": r.pricing.snapshot_age_seconds,
        },
        "decision": {
            "decision": r.decision_summary.decision,
            "rejection_reason": r.decision_summary.rejection_reason,
            "policy_mode": r.decision_summary.policy_mode,
            "passes_final_gate": r.decision_summary.passes_final_gate,
            "intended_size_usdc_used": r.decision_summary.intended_size_usdc_used,
            "pricing_sanity_notes": r.decision_summary.pricing_sanity_notes,
            "gross_ev": r.decision_summary.gross_ev,
            "net_ev_after_fee": r.decision_summary.net_ev_after_fee,
            "execution_adjusted_ev": r.decision_summary.execution_adjusted_ev,
            "required_edge_threshold": r.decision_summary.required_edge_threshold,
            "fill_fraction": r.decision_summary.fill_fraction,
        },
    }


# ─── Step 1: generate rich shadow corpus ─────────────────────────────────────

print("Step 1: Generating shadow corpus...")

runner_all = ShadowRunner([LIVE_CAL_CONFIG, PAPER_STRICT_CAL_CONFIG, PAPER_LOOSE_CAL_CONFIG])
runner_live = ShadowRunner([LIVE_CAL_CONFIG])
runner_strict = ShadowRunner([PAPER_STRICT_CAL_CONFIG])
runner_loose = ShadowRunner([PAPER_LOOSE_CAL_CONFIG])

ALL_RECORDS: list[ShadowDecisionRecord] = []

# -- healthy candidates over 5 days (for evidence sufficiency) --
# pricing_timestamp = now so snapshot_age_seconds = 0 (fresh)
for i in range(80):
    now = BASE + timedelta(hours=i * 1.5)
    fresh_snap = MarketPricingSnapshot(
        market_id=f"mkt-btc-{i:04d}",
        ask_yes=0.44, bid_yes=0.42, ask_no=0.57, bid_no=0.55,
        liquidity=5000.0, timestamp_utc=now,      # timestamp = now → age=0
    )
    recs = runner_all.evaluate(
        _signal(0.82), fresh_snap, intended_size_usdc=20.0, now_utc=now
    )
    ALL_RECORDS.extend(recs)

# -- stale pricing candidates (explicitly old snapshots) --
for i in range(10):
    now = BASE + timedelta(hours=80 + i)
    stale_snap = MarketPricingSnapshot(
        market_id=f"mkt-stale-{i:04d}",
        ask_yes=0.44, bid_yes=0.42, ask_no=0.57, bid_no=0.55,
        liquidity=5000.0, timestamp_utc=now - timedelta(seconds=200),  # 200s old
    )
    recs = runner_all.evaluate(
        _signal(0.82), stale_snap, intended_size_usdc=20.0, now_utc=now
    )
    ALL_RECORDS.extend(recs)

# -- weak calibration candidates --
for i in range(15):
    now = BASE + timedelta(hours=90 + i)
    fresh_snap = MarketPricingSnapshot(
        market_id=f"mkt-wk-{i:04d}",
        ask_yes=0.44, bid_yes=0.42, ask_no=0.57, bid_no=0.55,
        liquidity=5000.0, timestamp_utc=now,
    )
    recs = runner_all.evaluate(
        _signal(0.80, quality=CalibrationQuality.WEAK),
        fresh_snap,
        intended_size_usdc=20.0, now_utc=now,
    )
    ALL_RECORDS.extend(recs)

# -- suspicious underround candidates --
for i in range(8):
    now = BASE + timedelta(hours=110 + i)
    ur_snap = MarketPricingSnapshot(
        market_id=f"mkt-ur-{i:04d}",
        ask_yes=0.46, bid_yes=0.44, ask_no=0.50, bid_no=0.48,
        liquidity=5000.0, timestamp_utc=now,
    )
    recs = runner_all.evaluate(
        _signal(0.82), ur_snap, intended_size_usdc=20.0, now_utc=now
    )
    ALL_RECORDS.extend(recs)

# -- low liquidity (partial fill) candidates --
for i in range(8):
    now = BASE + timedelta(hours=120 + i)
    low_liq_snap = MarketPricingSnapshot(
        market_id=f"mkt-lowliq-{i:04d}",
        ask_yes=0.44, bid_yes=0.42, ask_no=0.57, bid_no=0.55,
        liquidity=50.0, timestamp_utc=now,
    )
    recs = runner_all.evaluate(
        _signal(0.82), low_liq_snap, intended_size_usdc=20.0, now_utc=now
    )
    ALL_RECORDS.extend(recs)

print(f"  Generated {len(ALL_RECORDS)} total records across 3 profiles.")


# ─── Step 2: profile-specific journal files ───────────────────────────────────

print("Step 2: Writing profile-specific journal files...")

for profile, fname in [
    ("live",         "shadow_journal_live_like_sample.jsonl"),
    ("paper_strict", "shadow_journal_paper_strict_sample.jsonl"),
    ("paper_loose",  "shadow_journal_paper_loose_sample.jsonl"),
]:
    recs = [r for r in ALL_RECORDS if r.policy_profile == profile]
    path = ARTIFACTS / fname
    with open(path, "w", encoding="utf-8") as f:
        for r in recs[:60]:  # cap at 60 per file for readability
            f.write(json.dumps(serialize_record(r)) + "\n")
    print(f"  {fname}: {min(len(recs), 60)} records")


# ─── Step 3: readiness report sample ─────────────────────────────────────────

print("Step 3: Generating readiness report sample...")

live_metrics  = compute_summary_metrics(ALL_RECORDS, "live")
loose_metrics = compute_summary_metrics(ALL_RECORDS, "paper_loose")
strict_metrics= compute_summary_metrics(ALL_RECORDS, "paper_strict")

req = EvidenceRequirements(
    min_live_like_evaluated=50,
    min_live_like_executes=5,
    min_live_like_rejects=10,
    min_assets_covered=1,
    min_horizons_covered=1,
    min_observation_days=3.0,
)
ev_result = check_evidence_sufficiency(ALL_RECORDS, req)
report = assess_readiness(live_metrics, ev_result, loose_metrics)

readiness_dict = {
    "generated_utc": datetime.now(timezone.utc).isoformat(),
    "verdict": report.verdict.value,
    "evidence_sufficient": report.evidence_sufficient,
    "verdict_reason": report.verdict_reason,
    "evidence_summary": {
        "live_like_evaluated": ev_result.live_like_evaluated,
        "live_like_executes": ev_result.live_like_executes,
        "live_like_rejects": ev_result.live_like_rejects,
        "assets_covered": ev_result.assets_covered,
        "horizons_covered": ev_result.horizons_covered,
        "observation_days": round(ev_result.observation_days, 2),
        "gaps": [{"dimension": g.dimension, "required": g.required, "actual": g.actual, "message": g.message}
                 for g in ev_result.gaps],
    },
    "live_metrics": {
        "profile": live_metrics.profile,
        "total_evaluated": live_metrics.total_evaluated,
        "execute_count": live_metrics.execute_count,
        "reject_count": live_metrics.reject_count,
        "execution_rate": round(live_metrics.execution_rate, 4),
        "rejection_counts": live_metrics.rejection_counts,
        "rejection_rates": {k: round(v, 4) for k, v in live_metrics.rejection_rates.items()},
        "mean_gross_ev": round(live_metrics.mean_gross_ev, 4) if live_metrics.mean_gross_ev else None,
        "mean_executable_ev": round(live_metrics.mean_executable_ev, 4) if live_metrics.mean_executable_ev else None,
        "mean_ev_haircut_pct": round(live_metrics.mean_ev_haircut_pct, 4) if live_metrics.mean_ev_haircut_pct else None,
        "ev_haircut_sample_size": live_metrics.ev_haircut_sample_size,
        "suspicious_underround_count": live_metrics.suspicious_underround_count,
        "suspicious_underround_rate": round(live_metrics.suspicious_underround_rate, 4),
        "stale_pricing_count": live_metrics.stale_pricing_count,
        "stale_pricing_rate": round(live_metrics.stale_pricing_rate, 4),
        "partial_fill_rejected_count": live_metrics.partial_fill_rejected_count,
        "partial_fill_rejection_rate": round(live_metrics.partial_fill_rejection_rate, 4),
        "full_fill_execute_count": live_metrics.full_fill_execute_count,
        "observation_days": round(live_metrics.observation_days, 2),
    },
    "paper_strict_metrics": {
        "total_evaluated": strict_metrics.total_evaluated,
        "execution_rate": round(strict_metrics.execution_rate, 4),
        "reject_count": strict_metrics.reject_count,
    },
    "paper_loose_metrics": {
        "total_evaluated": loose_metrics.total_evaluated,
        "execution_rate": round(loose_metrics.execution_rate, 4),
        "reject_count": loose_metrics.reject_count,
        "annotated_execute_count": loose_metrics.annotated_execute_count,
    },
    "profile_divergence_pp": round(
        (loose_metrics.execution_rate - live_metrics.execution_rate) * 100, 1
    ),
    "checks": [
        {
            "name": c.name,
            "level": c.level.value,
            "metric_value": round(c.metric_value, 4) if c.metric_value is not None else None,
            "message": c.message,
        }
        for c in report.checks
    ],
    "blockers": [c.name for c in report.blockers],
    "fails":    [c.name for c in report.fails],
    "warns":    [c.name for c in report.warns],
    "pilot_constraints": {
        "pilot_asset": report.pilot_constraints.pilot_asset,
        "pilot_horizon_minutes": report.pilot_constraints.pilot_horizon_minutes,
        "max_open_positions": report.pilot_constraints.max_open_positions,
        "max_nominal_usdc": report.pilot_constraints.max_nominal_usdc,
        "daily_max_loss_usdc": report.pilot_constraints.daily_max_loss_usdc,
        "single_loss_kill_usdc": report.pilot_constraints.single_loss_kill_usdc,
        "mandatory_review_hours": report.pilot_constraints.mandatory_review_hours,
        "pilot_evaluation_days": report.pilot_constraints.pilot_evaluation_days,
        "notes": report.pilot_constraints.notes,
    },
    "formatted_report": format_readiness_report(report),
}

with open(ARTIFACTS / "readiness_report_sample.json", "w", encoding="utf-8") as f:
    json.dump(readiness_dict, f, indent=2)
print(f"  readiness_report_sample.json: verdict={report.verdict.value}")


# ─── Step 4: rejection analytics sample ─────────────────────────────────────

print("Step 4: Generating rejection analytics sample...")

regime_live = compute_regime_review(ALL_RECORDS, profile="live", window_size=10, step=5)

rej_analytics = {
    "generated_utc": datetime.now(timezone.utc).isoformat(),
    "profile": "live",
    "total_evaluated": live_metrics.total_evaluated,
    "execute_count": live_metrics.execute_count,
    "reject_count": live_metrics.reject_count,
    "execution_rate": round(live_metrics.execution_rate, 4),
    "rejection_composition": {
        reason: {
            "count": count,
            "rate_of_total": round(live_metrics.rejection_rates.get(reason, 0), 4),
        }
        for reason, count in sorted(
            live_metrics.rejection_counts.items(), key=lambda x: -x[1]
        )
    },
    "dominant_rejection_reason": (
        max(live_metrics.rejection_rates, key=lambda k: live_metrics.rejection_rates[k])
        if live_metrics.rejection_rates else None
    ),
    "dominant_rejection_rate": round(
        max(live_metrics.rejection_rates.values())
        if live_metrics.rejection_rates else 0.0, 4
    ),
    "pricing_sanity": {
        "suspicious_underround_count": live_metrics.suspicious_underround_count,
        "suspicious_underround_rate": round(live_metrics.suspicious_underround_rate, 4),
        "stale_pricing_count": live_metrics.stale_pricing_count,
        "stale_pricing_rate": round(live_metrics.stale_pricing_rate, 4),
    },
    "fillability": {
        "full_fill_execute_count": live_metrics.full_fill_execute_count,
        "partial_fill_execute_count": live_metrics.partial_fill_execute_count,
        "partial_fill_rejected_count": live_metrics.partial_fill_rejected_count,
        "partial_fill_rejection_rate": round(live_metrics.partial_fill_rejection_rate, 4),
    },
    "ev_realism": {
        "mean_gross_ev": round(live_metrics.mean_gross_ev, 4) if live_metrics.mean_gross_ev else None,
        "mean_executable_ev": round(live_metrics.mean_executable_ev, 4) if live_metrics.mean_executable_ev else None,
        "mean_ev_haircut_abs": round(live_metrics.mean_ev_haircut_abs, 4) if live_metrics.mean_ev_haircut_abs else None,
        "mean_ev_haircut_pct": round(live_metrics.mean_ev_haircut_pct, 4) if live_metrics.mean_ev_haircut_pct else None,
        "ev_haircut_sample_size": live_metrics.ev_haircut_sample_size,
    },
    "regime_stability": {
        "is_stable": regime_live.is_stable,
        "rolling_windows_computed": len(regime_live.rolling_snapshots),
        "window_size": regime_live.window_size,
        "flags": {
            "rejection_rate_spike": regime_live.flags.rejection_rate_spike,
            "ev_trend_negative": regime_live.flags.ev_trend_negative,
            "fillability_degrading": regime_live.flags.fillability_degrading,
            "underround_rising": regime_live.flags.underround_rising,
        },
        "notes": regime_live.flags.notes,
        "summary": regime_live.summary,
    },
    "profile_comparison": {
        "live_exec_rate": round(live_metrics.execution_rate, 4),
        "paper_strict_exec_rate": round(strict_metrics.execution_rate, 4),
        "paper_loose_exec_rate": round(loose_metrics.execution_rate, 4),
        "loose_vs_live_divergence_pp": round(
            (loose_metrics.execution_rate - live_metrics.execution_rate) * 100, 1
        ),
        "strict_vs_live_divergence_pp": round(
            (strict_metrics.execution_rate - live_metrics.execution_rate) * 100, 1
        ),
        "paper_loose_annotated_executes": loose_metrics.annotated_execute_count,
        "warning": (
            "paper_loose execution rate is significantly above live — "
            "paper_loose results MUST NOT be used as evidence of live readiness."
            if (loose_metrics.execution_rate - live_metrics.execution_rate) > 0.05
            else "profiles are reasonably aligned"
        ),
    },
}

with open(ARTIFACTS / "rejection_analytics_sample.json", "w", encoding="utf-8") as f:
    json.dump(rej_analytics, f, indent=2)
print(f"  rejection_analytics_sample.json: dominant={rej_analytics['dominant_rejection_reason']}")


# ─── Step 5: replay examples ─────────────────────────────────────────────────

print("Step 5: Generating replay examples...")

# --- replay_same_policy_same_result.json ---
# Take one record, replay it under the same policy, confirm identical result
sample_live_recs = [r for r in ALL_RECORDS if r.policy_profile == "live" and
                    r.decision_summary.decision != "REJECT"]
if sample_live_recs:
    orig = sample_live_recs[0]
    from shadow_runner.runner import reconstruct_calibrated_signal, reconstruct_pricing_snapshot
    from calibration.decision_policy import decide

    cal_sig_orig = reconstruct_calibrated_signal(orig.signal)
    pricing_orig = reconstruct_pricing_snapshot(orig.pricing)
    replayed = decide(cal_sig_orig, pricing_orig, config=LIVE_CAL_CONFIG,
                      now_utc=orig.ts_recorded_utc, intended_size_usdc=orig.intended_size_usdc)

    same_result = {
        "description": "Deterministic replay: same policy, same inputs → same decision",
        "profile": "live",
        "original_record_id": orig.record_id,
        "original_decision": orig.decision_summary.decision,
        "original_rejection_reason": orig.decision_summary.rejection_reason,
        "original_gross_ev": orig.decision_summary.gross_ev,
        "original_execution_adjusted_ev": orig.decision_summary.execution_adjusted_ev,
        "replayed_decision": replayed.decision.value,
        "replayed_rejection_reason": (replayed.rejection_reason.value
                                       if replayed.rejection_reason else None),
        "replayed_gross_ev": (round(replayed.edge_estimate.gross_expected_value, 6)
                               if replayed.edge_estimate else None),
        "replayed_execution_adjusted_ev": (round(replayed.edge_estimate.execution_adjusted_ev, 6)
                                            if replayed.edge_estimate else None),
        "deterministic": (orig.decision_summary.decision == replayed.decision.value),
    }
    with open(ARTIFACTS / "replay_examples" / "replay_same_policy_same_result.json",
              "w", encoding="utf-8") as f:
        json.dump(same_result, f, indent=2)
    print(f"  replay_same_policy_same_result.json: deterministic={same_result['deterministic']}")

# --- replay_live_vs_paper_strict_diff.json ---
# Find a record that is REJECT in live but look for what paper_strict does with same inputs
live_recs = [r for r in ALL_RECORDS if r.policy_profile == "live"]
reject_recs = [r for r in live_recs if r.decision_summary.decision == "REJECT" and
               r.decision_summary.rejection_reason not in ("WEAK_CALIBRATION", "STALE_PRICING",
                                                             "SUSPICIOUS_UNDERROUND")]

# Use a candidate that shows policy difference: borderline confidence
# Build a borderline case manually
borderline_sig = _signal(0.78, quality=CalibrationQuality.STRONG)
borderline_snap = _snap(999, ask_yes=0.50)  # thinner edge

runner_pair = ShadowRunner([LIVE_CAL_CONFIG, PAPER_STRICT_CAL_CONFIG, PAPER_LOOSE_CAL_CONFIG])
borderline_recs = runner_pair.evaluate(borderline_sig, borderline_snap,
                                        intended_size_usdc=20.0, now_utc=BASE)

live_br   = next(r for r in borderline_recs if r.policy_profile == "live")
strict_br = next(r for r in borderline_recs if r.policy_profile == "paper_strict")
loose_br  = next(r for r in borderline_recs if r.policy_profile == "paper_loose")

diff_result = {
    "description": (
        "Same candidate evaluated across all three profiles. "
        "Shows how policy profile changes final decision on a borderline signal."
    ),
    "candidate": {
        "asset": "BTC",
        "horizon_minutes": 15,
        "raw_confidence": 0.78,
        "calibration_quality": "strong",
        "ask_yes": borderline_snap.ask_yes,
        "liquidity": borderline_snap.liquidity,
    },
    "live": {
        "profile": "live",
        "decision": live_br.decision_summary.decision,
        "rejection_reason": live_br.decision_summary.rejection_reason,
        "required_edge_threshold": live_br.decision_summary.required_edge_threshold,
        "execution_adjusted_ev": live_br.decision_summary.execution_adjusted_ev,
        "fill_fraction": live_br.decision_summary.fill_fraction,
    },
    "paper_strict": {
        "profile": "paper_strict",
        "decision": strict_br.decision_summary.decision,
        "rejection_reason": strict_br.decision_summary.rejection_reason,
        "required_edge_threshold": strict_br.decision_summary.required_edge_threshold,
        "execution_adjusted_ev": strict_br.decision_summary.execution_adjusted_ev,
        "fill_fraction": strict_br.decision_summary.fill_fraction,
    },
    "paper_loose": {
        "profile": "paper_loose",
        "decision": loose_br.decision_summary.decision,
        "rejection_reason": loose_br.decision_summary.rejection_reason,
        "required_edge_threshold": loose_br.decision_summary.required_edge_threshold,
        "execution_adjusted_ev": loose_br.decision_summary.execution_adjusted_ev,
        "fill_fraction": loose_br.decision_summary.fill_fraction,
        "pricing_sanity_notes": loose_br.decision_summary.pricing_sanity_notes,
    },
    "interpretation": (
        "If live=REJECT and paper_strict=EXECUTE: edge_threshold difference — strict is more permissive. "
        "If paper_loose=EXECUTE and live=REJECT: paper_loose results CANNOT be used as live readiness evidence."
    ),
}
with open(ARTIFACTS / "replay_examples" / "replay_live_vs_paper_strict_diff.json",
          "w", encoding="utf-8") as f:
    json.dump(diff_result, f, indent=2)
print(f"  replay_live_vs_paper_strict_diff.json: live={diff_result['live']['decision']} strict={diff_result['paper_strict']['decision']} loose={diff_result['paper_loose']['decision']}")


# ─── Step 6: decision scenarios ─────────────────────────────────────────────

print("Step 6: Generating decision scenarios...")

scenarios = []

# 1. Live-like clean execute
runner_live_only = ShadowRunner([LIVE_CAL_CONFIG])
clean_recs = runner_live_only.evaluate(_signal(0.82), _snap(0), intended_size_usdc=20.0, now_utc=BASE)
r = clean_recs[0]
scenarios.append({
    "scenario_id": "S01",
    "label": "clean_live_execute",
    "description": "Strong calibration, healthy liquidity, clean pricing → EXECUTE_YES in live",
    "profile": r.policy_profile,
    "decision": r.decision_summary.decision,
    "rejection_reason": r.decision_summary.rejection_reason,
    "gross_ev": r.decision_summary.gross_ev,
    "execution_adjusted_ev": r.decision_summary.execution_adjusted_ev,
    "fill_fraction": r.decision_summary.fill_fraction,
    "pricing_sanity_notes": r.decision_summary.pricing_sanity_notes,
    "raw_confidence": 0.82,
    "ask_yes": 0.44,
    "liquidity": 5000.0,
})

# 2. Suspicious underround reject
ur_recs = runner_live_only.evaluate(_signal(0.82), _underround_snap(0), intended_size_usdc=20.0, now_utc=BASE)
r = ur_recs[0]
scenarios.append({
    "scenario_id": "S02",
    "label": "suspicious_underround_reject",
    "description": "ask_yes + ask_no = 0.96 < 0.97 threshold → SUSPICIOUS_UNDERROUND reject",
    "profile": r.policy_profile,
    "decision": r.decision_summary.decision,
    "rejection_reason": r.decision_summary.rejection_reason,
    "gross_ev": r.decision_summary.gross_ev,
    "execution_adjusted_ev": r.decision_summary.execution_adjusted_ev,
    "fill_fraction": r.decision_summary.fill_fraction,
    "ask_yes": 0.46,
    "ask_no": 0.50,
    "sum_ask": 0.96,
    "threshold": 0.97,
})

# 3. Stale pricing reject
stale_recs = runner_live_only.evaluate(_signal(0.82), _snap(0, age_seconds=200), intended_size_usdc=20.0, now_utc=BASE)
r = stale_recs[0]
scenarios.append({
    "scenario_id": "S03",
    "label": "stale_pricing_reject",
    "description": "Pricing snapshot is 200s old (>120s threshold) → STALE_PRICING reject in live",
    "profile": r.policy_profile,
    "decision": r.decision_summary.decision,
    "rejection_reason": r.decision_summary.rejection_reason,
    "snapshot_age_seconds": 200.0,
    "stale_threshold_seconds": 120,
})

# 4. Weak calibration reject
weak_recs = runner_live_only.evaluate(
    _signal(0.80, quality=CalibrationQuality.WEAK), _snap(0),
    intended_size_usdc=20.0, now_utc=BASE
)
r = weak_recs[0]
scenarios.append({
    "scenario_id": "S04",
    "label": "weak_calibration_reject",
    "description": "CalibrationQuality.WEAK → WEAK_CALIBRATION reject in live-like mode",
    "profile": r.policy_profile,
    "decision": r.decision_summary.decision,
    "rejection_reason": r.decision_summary.rejection_reason,
    "calibration_quality": "WEAK",
    "raw_confidence": 0.80,
})

# 5. Partial fill reject (low liquidity)
_pf_snap = MarketPricingSnapshot(
    market_id="mkt-lowliq-scenario", ask_yes=0.44, bid_yes=0.42,
    ask_no=0.57, bid_no=0.55, liquidity=50.0, timestamp_utc=BASE,
)
pf_recs = runner_live_only.evaluate(
    _signal(0.82), _pf_snap, intended_size_usdc=20.0, now_utc=BASE
)
r = pf_recs[0]
scenarios.append({
    "scenario_id": "S05",
    "label": "partial_fill_rejected_or_small_fill",
    "description": "Liquidity=50 USDC, intended_size=20 USDC → partial fill scenario in live mode",
    "profile": r.policy_profile,
    "decision": r.decision_summary.decision,
    "rejection_reason": r.decision_summary.rejection_reason,
    "fill_fraction": r.decision_summary.fill_fraction,
    "liquidity": _pf_snap.liquidity,
    "intended_size_usdc": 20.0,
})

# 6. paper_loose executes, live rejects (same candidate)
trio_recs = runner_all.evaluate(
    _signal(0.80, quality=CalibrationQuality.WEAK), _snap(0),
    intended_size_usdc=20.0, now_utc=BASE
)
live_trio   = next(r for r in trio_recs if r.policy_profile == "live")
loose_trio  = next(r for r in trio_recs if r.policy_profile == "paper_loose")
strict_trio = next(r for r in trio_recs if r.policy_profile == "paper_strict")
scenarios.append({
    "scenario_id": "S06",
    "label": "paper_loose_pass_live_reject",
    "description": (
        "Weak calibration: live and paper_strict reject. "
        "paper_loose may execute depending on threshold. "
        "This is the observation zone — paper_loose results are NOT pilot evidence."
    ),
    "live_decision": live_trio.decision_summary.decision,
    "live_rejection_reason": live_trio.decision_summary.rejection_reason,
    "paper_strict_decision": strict_trio.decision_summary.decision,
    "paper_strict_rejection_reason": strict_trio.decision_summary.rejection_reason,
    "paper_loose_decision": loose_trio.decision_summary.decision,
    "paper_loose_rejection_reason": loose_trio.decision_summary.rejection_reason,
    "paper_loose_pricing_sanity_notes": loose_trio.decision_summary.pricing_sanity_notes,
    "interpretation": (
        "If paper_loose=EXECUTE and live=REJECT: this candidate is in the OBSERVATION ZONE. "
        "paper_loose execution here provides ZERO evidence of live readiness."
    ),
})

# 7. Insufficient evidence → NO_GO blocker
mini_records = []
for i in range(5):
    recs = runner_live_only.evaluate(_signal(0.82), _snap(i), intended_size_usdc=20.0,
                                      now_utc=BASE + timedelta(hours=i))
    mini_records.extend(recs)
mini_ev = check_evidence_sufficiency(mini_records)
mini_metrics = compute_summary_metrics(mini_records, "live")
mini_report = assess_readiness(mini_metrics, mini_ev)
scenarios.append({
    "scenario_id": "S07",
    "label": "insufficient_evidence_no_go",
    "description": "Only 5 live-like records over <1 day → evidence gate fails → NO_GO",
    "live_like_evaluated": mini_ev.live_like_evaluated,
    "required_live_like_evaluated": 50,
    "observation_days": round(mini_ev.observation_days, 3),
    "required_observation_days": 3.0,
    "evidence_sufficient": mini_ev.sufficient,
    "gaps": [g.dimension for g in mini_ev.gaps],
    "verdict": mini_report.verdict.value,
    "verdict_reason": mini_report.verdict_reason,
})

# 8. Blocker triggered (hypothetical: force execution_rate > 85% via all-pass scenario)
# Build a corpus that passes evidence but has very high exec rate
high_exec_records = []
for i in range(80):
    now = BASE + timedelta(hours=i * 1.5)
    recs = runner_loose.evaluate(_signal(0.90), _snap(i), intended_size_usdc=20.0, now_utc=now)
    high_exec_records.extend(recs)
he_metrics = compute_summary_metrics(high_exec_records, "paper_loose")
he_ev = check_evidence_sufficiency(high_exec_records, EvidenceRequirements(
    min_live_like_evaluated=50, min_live_like_executes=5, min_live_like_rejects=5,
    min_assets_covered=1, min_horizons_covered=1, min_observation_days=3.0,
    live_like_profile="paper_loose",
))
he_report = assess_readiness(he_metrics, he_ev)
scenarios.append({
    "scenario_id": "S08",
    "label": "high_execution_rate_warn_or_blocker",
    "description": (
        "Very permissive policy (paper_loose, high confidence) → very high execution rate. "
        "If >85% → BLOCKER. If 60-85% → WARN. Gates not functioning when everything passes."
    ),
    "execution_rate": round(he_metrics.execution_rate, 4),
    "verdict": he_report.verdict.value,
    "checks": [{"name": c.name, "level": c.level.value} for c in he_report.checks],
    "verdict_reason": he_report.verdict_reason,
})

# 9. Candidate for pilot review (all clean, sufficient evidence)
pilot_ev = check_evidence_sufficiency(ALL_RECORDS, req)
pilot_metrics = compute_summary_metrics(ALL_RECORDS, "live")
pilot_loose = compute_summary_metrics(ALL_RECORDS, "paper_loose")
pilot_report = assess_readiness(pilot_metrics, pilot_ev, pilot_loose)
scenarios.append({
    "scenario_id": "S09",
    "label": "candidate_for_pilot_review",
    "description": (
        "Sufficient corpus, behavioral checks pass → readiness assessment available. "
        "Verdict depends on actual metric values. See readiness_report_sample.json for full detail."
    ),
    "evidence_sufficient": pilot_ev.sufficient,
    "live_like_evaluated": pilot_ev.live_like_evaluated,
    "observation_days": round(pilot_ev.observation_days, 2),
    "execution_rate": round(pilot_metrics.execution_rate, 4),
    "verdict": pilot_report.verdict.value,
    "blockers": [c.name for c in pilot_report.blockers],
    "fails": [c.name for c in pilot_report.fails],
    "warns": [c.name for c in pilot_report.warns],
    "pilot_constraints_applied": {
        "asset": pilot_report.pilot_constraints.pilot_asset,
        "max_size_usdc": pilot_report.pilot_constraints.max_nominal_usdc,
        "max_positions": pilot_report.pilot_constraints.max_open_positions,
        "pilot_days": pilot_report.pilot_constraints.pilot_evaluation_days,
    },
})

with open(ARTIFACTS / "decision_examples" / "scenarios.json", "w", encoding="utf-8") as f:
    json.dump({"scenarios": scenarios, "generated_utc": datetime.now(timezone.utc).isoformat()},
              f, indent=2)
print(f"  scenarios.json: {len(scenarios)} scenarios")


# ─── Step 7: run tests and capture output ─────────────────────────────────────

print("Step 7: Running pytest...")

result = subprocess.run(
    [sys.executable, "-m", "pytest", "-v", "--tb=short", "--no-header"],
    capture_output=True, text=True, cwd=str(ROOT), encoding="utf-8", errors="replace",
    timeout=300,
)
pytest_output = result.stdout + result.stderr
with open(ARTIFACTS / "pytest_output.txt", "w", encoding="utf-8") as f:
    f.write(pytest_output)

# parse summary line
summary_line = ""
for line in reversed(pytest_output.splitlines()):
    if "passed" in line or "failed" in line or "error" in line:
        summary_line = line.strip()
        break
print(f"  pytest: {summary_line}")

notes = f"""Test Run Notes
==============
Date:           {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}
Command:        python -m pytest -v --tb=short --no-header
Working dir:    project root
Python:         {sys.version.split()[0]}
Exit code:      {result.returncode}
Summary:        {summary_line}

Test paths configured in pytest.ini:
  tests/
  calibration/tests/
  execution_realism/tests/
  crypto_directional/tests/
  signal_bridge/tests/

Note: review_bundle/tests/ is intentionally excluded (legacy bundle — would
cause import collision with tests/ without pytest.ini testpaths constraint).
"""
with open(ARTIFACTS / "test_run_notes.txt", "w", encoding="utf-8") as f:
    f.write(notes)
print(f"  test_run_notes.txt written")


# ─── Step 8: build ZIP ───────────────────────────────────────────────────────

print("Step 8: Building project_snapshot.zip...")

ZIP_PATH = ROOT / "project_snapshot.zip"

# Spec docs to include in docs/ inside zip
SPEC_DOCS_ROOT = [
    "README.md",
    "SHADOW_VALIDATION_GATE.md",
    "LIVE_PILOT_READINESS.md",
    "SHADOW_REVIEW_TEMPLATE.md",
    "SHADOW_RUNNER_SPEC.md",
    "JOURNAL_SCHEMA.md",
    "DRIFT_MONITORING_SPEC.md",
    "PRICING_SANITY_SPEC.md",
    "PAPER_MODE_POLICY.md",
    "PARTIAL_FILL_POLICY.md",
    "EXECUTABLE_NOTIONAL_SPEC.md",
    "BRIDGE_SPEC.md",
    "CALIBRATION_SPEC.md",
    "EDGE_ESTIMATION.md",
    "MARKET_MAPPING.md",
    "FIXES.md",
    "PACKAGE_MANIFEST.md",
]

# calibration subdoc specs
SPEC_DOCS_CALIBRATION = [
    "calibration/CONTRACT_HARDENING_SPEC.md",
    "calibration/DECISION_FLOW.md",
    "calibration/DECISION_INTEGRATION_SPEC.md",
    "calibration/HARDENING_SPEC.md",
    "calibration/LIVE_VS_PAPER_POLICY.md",
    "calibration/SPEC.md",
]

SPEC_DOCS_EXEC = [
    "execution_realism/COST_MODEL.md",
    "execution_realism/EXECUTION_REALISM_SPEC.md",
]

# source directories to include fully
SOURCE_DIRS = [
    "config",
    "signal_bridge",
    "calibration",
    "execution_realism",
    "shadow_runner",
    "monitoring",
    "tests",
]

# test subdirectories under module packages
TEST_SUBDIRS = [
    "calibration/tests",
    "execution_realism/tests",
    "crypto_directional/tests",
    "signal_bridge/tests",
]

SKIP_DIRS = {".venv", "__pycache__", ".pytest_cache", "review_bundle",
             "node_modules", ".git", "logs", "data", "web", "backtesting",
             "agents", "core", "strategies"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".env", ".key", ".pem"}
SKIP_NAMES = {".env", "private_key", "mnemonic", "seed_phrase", "credentials"}


def should_skip(path: Path) -> bool:
    for part in path.parts:
        if part in SKIP_DIRS:
            return True
    if path.suffix in SKIP_SUFFIXES:
        return True
    if path.name in SKIP_NAMES:
        return True
    if path.name.startswith(".") and path.suffix not in {".md", ".yaml", ".yml", ".toml", ".cfg", ".ini"}:
        return True
    return False


def add_dir(zf: zipfile.ZipFile, src_dir: str, zip_prefix: str):
    src_path = ROOT / src_dir
    if not src_path.exists():
        return
    for fpath in sorted(src_path.rglob("*")):
        if fpath.is_file() and not should_skip(fpath):
            arcname = f"project_snapshot/{zip_prefix}/{fpath.relative_to(ROOT / src_dir)}"
            arcname = arcname.replace("\\", "/")
            zf.write(fpath, arcname)


with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:

    # requirements
    for f in ["requirements.txt", "requirements-dev.txt", "pyproject.toml",
               "poetry.lock", "uv.lock", "pytest.ini", "setup.cfg"]:
        p = ROOT / f
        if p.exists():
            zf.write(p, f"project_snapshot/{f}")

    # spec docs → docs/
    for doc in SPEC_DOCS_ROOT:
        p = ROOT / doc
        if p.exists():
            zf.write(p, f"project_snapshot/docs/{Path(doc).name}")

    for doc in SPEC_DOCS_CALIBRATION + SPEC_DOCS_EXEC:
        p = ROOT / doc
        if p.exists():
            zf.write(p, f"project_snapshot/docs/{Path(doc).name}")

    # docs/ subfolder
    for p in (ROOT / "docs").glob("*.md"):
        zf.write(p, f"project_snapshot/docs/{p.name}")

    # source dirs
    for d in SOURCE_DIRS:
        add_dir(zf, d, d)

    # artifacts
    for fpath in sorted(ARTIFACTS.rglob("*")):
        if fpath.is_file():
            arcname = f"project_snapshot/artifacts/{fpath.relative_to(ARTIFACTS)}"
            arcname = arcname.replace("\\", "/")
            zf.write(fpath, arcname)

size_mb = ZIP_PATH.stat().st_size / (1024 * 1024)
print(f"\nDone. project_snapshot.zip -> {size_mb:.2f} MB")
print(f"Verdict from readiness_report_sample: {report.verdict.value}")
print(f"  blockers: {[c.name for c in report.blockers]}")
print(f"  fails:    {[c.name for c in report.fails]}")
print(f"  warns:    {[c.name for c in report.warns]}")
