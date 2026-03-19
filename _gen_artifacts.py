"""Generate all sample artifacts for the zip."""
import json
import dataclasses
import tempfile
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJ = Path("c:/Users/lcladm/.antigravity/Polymarket")
out = PROJ / "artifacts"
out.mkdir(parents=True, exist_ok=True)
(out / "replay_examples").mkdir(exist_ok=True)
(out / "decision_examples").mkdir(exist_ok=True)

from calibration.probability_mapper import map_to_event_probability
from calibration.types import (
    CalibrationMethod, CalibrationQuality,
    LIVE_CAL_CONFIG, PAPER_LOOSE_CAL_CONFIG, PAPER_STRICT_CAL_CONFIG,
    MarketPricingSnapshot, RawSignalOutput,
)
from shadow_runner.runner import ShadowRunner
from shadow_runner.journal import InMemoryJournal, JournalReader
from shadow_runner.replay import replay_record
from shadow_runner.reporting import generate_comparison_report, format_report
from monitoring.drift_monitor import DriftMonitor
from monitoring.alerts import AlertEngine

NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
runner = ShadowRunner([LIVE_CAL_CONFIG, PAPER_STRICT_CAL_CONFIG, PAPER_LOOSE_CAL_CONFIG])


def make_signal(confidence, quality=CalibrationQuality.STRONG, method=CalibrationMethod.PLATT):
    raw = RawSignalOutput(
        asset="BTC", horizon_minutes=5, timestamp_utc=NOW,
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


def make_snap(market_id, ask_yes=0.44, bid_yes=0.42, ask_no=0.57, bid_no=0.55,
              liquidity=5000.0, age_seconds=0):
    ts = NOW - timedelta(seconds=age_seconds)
    return MarketPricingSnapshot(
        market_id=market_id, ask_yes=ask_yes, bid_yes=bid_yes,
        ask_no=ask_no, bid_no=bid_no, liquidity=liquidity, timestamp_utc=ts,
    )


# ── 1. Shadow journal sample ──────────────────────────────────────────────────
print("Generating shadow journal...")
journal = InMemoryJournal()
scenarios = [
    ("execute_strong",    make_signal(0.82), make_snap("mkt-btc-001")),
    ("execute_medium",    make_signal(0.74), make_snap("mkt-btc-002")),
    ("reject_stale",      make_signal(0.82), make_snap("mkt-btc-003", age_seconds=200)),
    ("reject_weak_cal",   make_signal(0.80, quality=CalibrationQuality.WEAK,
                                      method=CalibrationMethod.IDENTITY),
                          make_snap("mkt-btc-004")),
    ("reject_underround", make_signal(0.82),
                          make_snap("mkt-btc-005", ask_yes=0.46, bid_yes=0.44,
                                    ask_no=0.50, bid_no=0.48)),
    ("partial_fill",      make_signal(0.78), make_snap("mkt-btc-006", liquidity=500.0)),
]

all_records = []
for label, sig, snap in scenarios:
    recs = runner.evaluate(sig, snap, intended_size_usdc=20.0, now_utc=NOW)
    journal.write_batch(recs)
    all_records.extend(recs)

with open(out / "shadow_journal_sample.jsonl", "w", encoding="utf-8") as f:
    f.write(journal.to_jsonl())
print(f"  {journal.record_count()} records written")

# ── 2. Daily report sample ────────────────────────────────────────────────────
print("Generating daily report...")
report = generate_comparison_report(all_records, runner.run_id)


def profile_summary_dict(ps):
    return {
        "policy_profile": ps.policy_profile,
        "total_candidates": ps.total_candidates,
        "execution_rate": round(ps.execution_rate, 4),
        "rejection_reasons": ps.rejection_reasons,
        "ev_mean": round(ps.ev_mean, 6) if ps.ev_mean is not None else None,
        "ev_p25": round(ps.ev_p25, 6) if ps.ev_p25 is not None else None,
        "ev_p75": round(ps.ev_p75, 6) if ps.ev_p75 is not None else None,
        "sanity_note_count": ps.sanity_note_count,
    }


def cross_cmp_dict(c):
    return {
        "profile_a": c.profile_a,
        "profile_b": c.profile_b,
        "shared_candidates": c.shared_candidates,
        "both_execute": c.both_execute,
        "both_reject": c.both_reject,
        "a_executes_b_rejects": c.a_executes_b_rejects,
        "b_executes_a_rejects": c.b_executes_a_rejects,
        "agreement_rate": round(c.agreement_rate, 4),
        "divergence_rate": round(c.divergence_rate, 4),
    }


daily_report = {
    "run_id": report.run_id,
    "generated_utc": report.generated_utc.isoformat() if hasattr(report.generated_utc, "isoformat") else str(report.generated_utc),
    "total_records": report.total_records,
    "profiles_found": report.profiles_found,
    "loose_only_execute_count": report.loose_only_execute_count,
    "strict_not_live_count": report.strict_not_live_count,
    "per_profile": [profile_summary_dict(p) for p in report.per_profile.values()],
    "cross_profile": [cross_cmp_dict(c) for c in report.cross_profile],
    "formatted_text": format_report(report),
}

with open(out / "daily_report_sample.json", "w", encoding="utf-8") as f:
    json.dump(daily_report, f, indent=2, default=str)
print("  daily_report_sample.json written")

# ── 3. Drift report sample ────────────────────────────────────────────────────
print("Generating drift report...")
baseline_runner = ShadowRunner([LIVE_CAL_CONFIG, PAPER_STRICT_CAL_CONFIG, PAPER_LOOSE_CAL_CONFIG])
baseline_records = []
for i in range(4):
    snap = make_snap(f"mkt-base-{i:03d}", ask_yes=0.43 + i * 0.005)
    baseline_records.extend(
        baseline_runner.evaluate(make_signal(0.82), snap, intended_size_usdc=20.0, now_utc=NOW)
    )

current_records = []
for i in range(3):
    snap = make_snap(f"mkt-curr-{i:03d}")
    current_records.extend(
        baseline_runner.evaluate(make_signal(0.82), snap, intended_size_usdc=20.0, now_utc=NOW)
    )
for i in range(2):
    snap = make_snap(f"mkt-stale-{i:03d}", age_seconds=200)
    current_records.extend(
        baseline_runner.evaluate(make_signal(0.82), snap, intended_size_usdc=20.0, now_utc=NOW)
    )

monitor = DriftMonitor(baseline_records, min_baseline_size=3)
drift = monitor.check(current_records)
alerts = AlertEngine().check(drift)

drift_dict = {
    "baseline_window_size": drift.baseline_window_size,
    "current_window_size": drift.current_window_size,
    "profiles_checked": drift.profiles_checked,
    "ts_computed_utc": drift.ts_computed_utc.isoformat(),
    "rejection_rate_delta": drift.rejection_rate_delta,
    "ev_mean_delta": drift.ev_mean_delta,
    "divergence_delta": drift.divergence_delta,
    "baseline_divergence": drift.baseline_divergence,
    "baseline_rejection": {p: dataclasses.asdict(v) for p, v in drift.baseline_rejection.items()},
    "current_rejection": {p: dataclasses.asdict(v) for p, v in drift.current_rejection.items()},
    "divergence": [dataclasses.asdict(d) for d in drift.divergence],
    "alerts": [dataclasses.asdict(a) for a in alerts],
    "alert_count": len(alerts),
}

with open(out / "drift_report_sample.json", "w", encoding="utf-8") as f:
    json.dump(drift_dict, f, indent=2, default=str)
print(f"  drift_report_sample.json written ({len(alerts)} alerts)")

# ── 4. Replay examples ────────────────────────────────────────────────────────
print("Generating replay examples...")
config_map = {
    "live": LIVE_CAL_CONFIG,
    "paper_strict": PAPER_STRICT_CAL_CONFIG,
    "paper_loose": PAPER_LOOSE_CAL_CONFIG,
}

tf = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8")
tf.write(journal.to_jsonl())
tf.close()
reader = JournalReader(tf.name)
journal_records = reader.read_all()
os.unlink(tf.name)

replay_examples = []
seen_keys = set()
for rec in journal_records:
    key = (rec.decision_summary.decision, rec.decision_summary.rejection_reason, rec.policy_profile)
    if key in seen_keys or len(replay_examples) >= 5:
        continue
    seen_keys.add(key)
    result = replay_record(rec, config_map)
    replay_examples.append({
        "record_id": rec.record_id,
        "policy_profile": rec.policy_profile,
        "market_id": rec.pricing.market_id,
        "original_decision": result.original_decision,
        "original_rejection": result.original_rejection,
        "replayed_decision": result.replayed_decision,
        "replayed_rejection": result.replayed_rejection,
        "match": result.match,
        "mismatch_reason": result.mismatch_reason,
        "execution_adjusted_ev": rec.decision_summary.execution_adjusted_ev,
    })

with open(out / "replay_examples" / "replay_decisions.json", "w", encoding="utf-8") as f:
    json.dump(replay_examples, f, indent=2)
print(f"  {len(replay_examples)} replay examples written")

# ── 5. Decision examples ──────────────────────────────────────────────────────
print("Generating decision examples...")
examples = []
for label, sig, snap in scenarios:
    recs = runner.evaluate(sig, snap, intended_size_usdc=20.0, now_utc=NOW)
    for rec in recs:
        ds = rec.decision_summary
        examples.append({
            "scenario": label,
            "policy_profile": rec.policy_profile,
            "decision": ds.decision,
            "rejection_reason": ds.rejection_reason,
            "execution_adjusted_ev": ds.execution_adjusted_ev,
            "gross_ev": ds.gross_ev,
            "net_ev_after_fee": ds.net_ev_after_fee,
            "fill_fraction": ds.fill_fraction,
            "passes_final_gate": ds.passes_final_gate,
            "pricing_sanity_notes": ds.pricing_sanity_notes,
            "required_edge_threshold": ds.required_edge_threshold,
        })

with open(out / "decision_examples" / "scenarios.json", "w", encoding="utf-8") as f:
    json.dump(examples, f, indent=2)
print(f"  {len(examples)} decision examples written")

print("\nAll artifacts done.")
print(f"Output dir: {out}")
