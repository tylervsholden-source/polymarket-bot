"""
monitoring/daily_review.py

Daily shadow evidence review report generator.

Answers: "What did the system decide today, and should we trust it?"

Designed to be called at end-of-day (or on demand) with the day's
shadow decision records. Produces a structured DailyReviewReport and
a human-readable text summary covering:

  1. Evidence sufficiency (is the corpus large enough to assess?)
  2. Readiness verdict (TINY_PILOT_CANDIDATE / CONDITIONAL_REVIEW / NO_GO / INSUFFICIENT_EVIDENCE)
  3. Per-profile execution rates and dominant rejection reasons
  4. Profile divergence (paper_loose inflation, paper_strict vs live drift)
  5. Pricing sanity: underround rate, stale pricing rate
  6. EV chain: gross vs executable, haircut
  7. Would-trade list: candidates that passed live_like gate
  8. Observation-only zone: candidates that passed paper_loose but not live_like

Usage
-----
    from monitoring.daily_review import generate_daily_review, format_daily_review
    from shadow_runner.journal import JournalReader

    records, integrity = JournalReader(path).read_all_with_integrity()
    report = generate_daily_review(records, journal_integrity=integrity)
    print(format_daily_review(report))
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from shadow_runner.types import ShadowDecisionRecord
from shadow_runner.summary_metrics import ShadowSummaryMetrics, compute_summary_metrics
from shadow_runner.validation import EvidenceRequirements, check_evidence_sufficiency
from shadow_runner.readiness import (
    ReadinessReport,
    ReadinessVerdict,
    assess_readiness,
    format_readiness_report,
)
from monitoring.regime_review import compute_regime_review
from monitoring.readiness_checks import check_journal_integrity


@dataclass
class WouldTradeCandidate:
    """A candidate that passed the live_like gate (would have been traded)."""
    asset: str
    market_id: str
    signal_ts: datetime
    decision: str              # EXECUTE_YES or EXECUTE_NO
    executable_ev: Optional[float]
    fill_fraction: Optional[float]
    intended_size_usdc: float
    passed_paper_strict: bool
    passed_paper_loose: bool


@dataclass
class DailyReviewReport:
    """
    Full daily shadow evidence review.

    generated_utc  : when this report was produced
    date_label     : human-readable date (e.g. "2026-03-15")
    total_records  : total shadow records reviewed
    live_metrics   : ShadowSummaryMetrics for live profile
    strict_metrics : ShadowSummaryMetrics for paper_strict profile
    loose_metrics  : ShadowSummaryMetrics for paper_loose profile
    readiness      : ReadinessReport — formal gate verdict
    would_trade    : candidates that passed live_like gate
    loose_only     : candidates that passed paper_loose but not live_like
    journal_integrity_stats : raw integrity stats passed in (or None)
    regime_summary : one-line stability summary from regime review
    """
    generated_utc: datetime
    date_label: str
    total_records: int
    live_metrics: ShadowSummaryMetrics
    strict_metrics: ShadowSummaryMetrics
    loose_metrics: ShadowSummaryMetrics
    readiness: ReadinessReport
    would_trade: list              # list[WouldTradeCandidate]
    loose_only: list               # list[WouldTradeCandidate]
    journal_integrity_stats: object   # JournalIntegrityStats | None
    regime_summary: str


def generate_daily_review(
    records: list[ShadowDecisionRecord],
    journal_integrity: object = None,
    evidence_requirements: Optional[EvidenceRequirements] = None,
) -> DailyReviewReport:
    """
    Generate a DailyReviewReport from today's shadow records.

    Parameters
    ----------
    records : list[ShadowDecisionRecord]
        All shadow records for the review window (typically one day).
    journal_integrity : JournalIntegrityStats | None
        From JournalReader.read_all_with_integrity(). If provided, feeds
        into the readiness gate's journal integrity check.
    evidence_requirements : EvidenceRequirements | None
        Custom evidence thresholds. None → defaults.

    Returns
    -------
    DailyReviewReport
    """
    now_utc = datetime.now(timezone.utc)
    date_label = now_utc.strftime("%Y-%m-%d")

    if not records:
        # Return an empty report — no data yet today
        empty_live = compute_summary_metrics([], profile="live")
        empty_strict = compute_summary_metrics([], profile="paper_strict")
        empty_loose = compute_summary_metrics([], profile="paper_loose")
        ev_result = check_evidence_sufficiency([], evidence_requirements)
        readiness = assess_readiness(
            empty_live, ev_result,
            loose_metrics=empty_loose,
            strict_metrics=empty_strict,
            journal_integrity=journal_integrity,
        )
        return DailyReviewReport(
            generated_utc=now_utc,
            date_label=date_label,
            total_records=0,
            live_metrics=empty_live,
            strict_metrics=empty_strict,
            loose_metrics=empty_loose,
            readiness=readiness,
            would_trade=[],
            loose_only=[],
            journal_integrity_stats=journal_integrity,
            regime_summary="No records — regime stability cannot be assessed.",
        )

    live_metrics   = compute_summary_metrics(records, profile="live")
    strict_metrics = compute_summary_metrics(records, profile="paper_strict")
    loose_metrics  = compute_summary_metrics(records, profile="paper_loose")

    ev_result = check_evidence_sufficiency(records, evidence_requirements)

    # Regime review — needs at least a window of records
    regime = compute_regime_review(records, profile="live")
    regime_summary = regime.summary

    readiness = assess_readiness(
        live_metrics,
        ev_result,
        loose_metrics=loose_metrics,
        strict_metrics=strict_metrics,
        regime_review=regime,
        journal_integrity=journal_integrity,
    )

    would_trade, loose_only = _build_candidate_lists(records)

    return DailyReviewReport(
        generated_utc=now_utc,
        date_label=date_label,
        total_records=len(records),
        live_metrics=live_metrics,
        strict_metrics=strict_metrics,
        loose_metrics=loose_metrics,
        readiness=readiness,
        would_trade=would_trade,
        loose_only=loose_only,
        journal_integrity_stats=journal_integrity,
        regime_summary=regime_summary,
    )


def format_daily_review(report: DailyReviewReport) -> str:
    """
    Format a DailyReviewReport as a human-readable daily review text.
    """
    verdict = report.readiness.verdict
    verdict_str = verdict.value

    lines = [
        "=" * 70,
        f"DAILY SHADOW REVIEW — {report.date_label}",
        f"Generated: {report.generated_utc.isoformat()}",
        f"Total records: {report.total_records}",
        f"",
        f"VERDICT: {verdict_str}",
        f"Reason: {report.readiness.verdict_reason}",
        "=" * 70,
    ]

    # Per-profile summary
    lines.append("\nPROFILE SUMMARY:")
    for profile, m in [
        ("live",         report.live_metrics),
        ("paper_strict", report.strict_metrics),
        ("paper_loose",  report.loose_metrics),
    ]:
        if m.total_evaluated == 0:
            lines.append(f"  [{profile}]  no records")
            continue
        top_reason = (
            max(m.rejection_counts, key=lambda k: m.rejection_counts[k])
            if m.rejection_counts else "—"
        )
        lines.append(
            f"  [{profile}]  "
            f"eval={m.total_evaluated}  "
            f"exec={m.execute_count} ({m.execution_rate:.0%})  "
            f"reject={m.reject_count}  "
            f"top_reason={top_reason}"
        )
        if m.mean_executable_ev is not None:
            lines.append(
                f"    EV: gross={m.mean_gross_ev:.4f}  "
                f"exec={m.mean_executable_ev:.4f}  "
                f"haircut={m.mean_ev_haircut_pct:.0%}"
            )

    # Divergence
    live_rate  = report.live_metrics.execution_rate
    loose_rate = report.loose_metrics.execution_rate
    strict_rate = report.strict_metrics.execution_rate
    lines.append(f"\nDIVERGENCE:")
    lines.append(f"  paper_loose vs live:   {(loose_rate - live_rate):+.1%} pp")
    lines.append(f"  paper_strict vs live:  {(strict_rate - live_rate):+.1%} pp")

    # Pricing sanity
    lm = report.live_metrics
    lines.append(f"\nPRICING SANITY (live):")
    lines.append(f"  suspicious underround: {lm.suspicious_underround_rate:.1%}")
    lines.append(f"  stale pricing:         {lm.stale_pricing_rate:.1%}")

    # Regime stability
    lines.append(f"\nREGIME STABILITY:")
    lines.append(f"  {report.regime_summary}")

    # Journal integrity
    if report.journal_integrity_stats is not None:
        js = report.journal_integrity_stats
        lines.append(f"\nJOURNAL INTEGRITY:")
        lines.append(
            f"  total_lines={js.total_lines}  "
            f"parsed_ok={js.parsed_ok}  "
            f"bad_json={js.bad_json_count}  "
            f"version_skip={js.version_skip_count}  "
            f"bad_fraction={js.bad_line_fraction:.1%}"
        )

    # Would-trade list
    if report.would_trade:
        lines.append(f"\nWOULD-TRADE ({len(report.would_trade)} candidates):")
        for c in report.would_trade:
            ev_str = f"{c.executable_ev:.4f}" if c.executable_ev is not None else "N/A"
            lines.append(
                f"  {c.asset} | {c.market_id} | {c.decision} | "
                f"ev={ev_str} | fill={c.fill_fraction or '?':.2f} | "
                f"strict={c.passed_paper_strict} loose={c.passed_paper_loose}"
            )
    else:
        lines.append(f"\nWOULD-TRADE: 0 candidates passed live_like gate today.")

    # Loose-only observation zone
    if report.loose_only:
        lines.append(
            f"\n*** OBSERVATION ZONE ({len(report.loose_only)} candidates): "
            f"passed paper_loose only — DO NOT use as live signal ***"
        )
        for c in report.loose_only:
            lines.append(f"  {c.asset} | {c.market_id} | {c.decision}")

    # Full readiness check list
    lines.append("\n" + format_readiness_report(report.readiness))

    return "\n".join(lines)


# ── Verdict persistence ───────────────────────────────────────────────────────

def write_readiness_verdict(report: DailyReviewReport, path: str) -> None:
    """
    Write the readiness verdict from a DailyReviewReport to a JSON file.

    The orchestrator reads this file in _readiness_clears_live() before
    enabling live order flow. File is only written (never appended) — each
    call overwrites the previous verdict.

    Parameters
    ----------
    report : DailyReviewReport
    path   : destination file path (e.g. "data/readiness_verdict.json")
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    def _check_to_dict(c) -> dict:
        return {
            "name": c.name,
            "level": c.level.value,
            "metric_value": c.metric_value,
            "message": c.message,
        }

    payload = {
        "verdict":        report.readiness.verdict.value,
        "verdict_reason": report.readiness.verdict_reason,
        "generated_utc":  report.generated_utc.isoformat(),
        "date_label":     report.date_label,
        "total_records":  report.total_records,
        "evidence_sufficient": report.readiness.evidence_sufficient,
        # operator_layer/readiness_view.py's build_readiness_state() reads
        # each of these as a list of {name, level, metric_value, message}
        # dicts (via _build_check_row()). Writing bare check-name strings
        # here (the previous behavior) made _build_check_row() call
        # str.get(...) and crash with AttributeError as soon as any check
        # actually produced a blocker/fail/warn — i.e. on every readiness
        # snapshot except a clean TINY_PILOT_CANDIDATE, exactly when an
        # operator most needs the dashboard to explain why live trading is
        # blocked.
        "checks":         [_check_to_dict(c) for c in report.readiness.checks],
        "blockers":       [_check_to_dict(c) for c in report.readiness.blockers],
        "fails":          [_check_to_dict(c) for c in report.readiness.fails],
        "warns":          [_check_to_dict(c) for c in report.readiness.warns],
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, path)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_candidate_lists(
    records: list[ShadowDecisionRecord],
) -> tuple[list[WouldTradeCandidate], list[WouldTradeCandidate]]:
    """
    Build would_trade and loose_only candidate lists.

    would_trade : passed live_like gate (EXECUTE_YES or EXECUTE_NO in live profile)
    loose_only  : passed paper_loose but NOT live_like
    """
    # Index records by (asset, market_id, signal_ts) → {profile: record}
    by_candidate: dict[tuple, dict[str, ShadowDecisionRecord]] = {}
    for r in records:
        key = (r.signal.asset, r.pricing.market_id, r.signal.signal_timestamp_utc)
        by_candidate.setdefault(key, {})[r.policy_profile] = r

    def _is_exec(rec: Optional[ShadowDecisionRecord]) -> bool:
        if rec is None:
            return False
        return rec.decision_summary.decision in ("EXECUTE_YES", "EXECUTE_NO")

    would_trade: list[WouldTradeCandidate] = []
    loose_only: list[WouldTradeCandidate] = []

    for key, profiles in by_candidate.items():
        live_rec   = profiles.get("live")
        strict_rec = profiles.get("paper_strict")
        loose_rec  = profiles.get("paper_loose")

        live_exec   = _is_exec(live_rec)
        strict_exec = _is_exec(strict_rec)
        loose_exec  = _is_exec(loose_rec)

        if live_exec and live_rec is not None:
            would_trade.append(WouldTradeCandidate(
                asset=live_rec.signal.asset,
                market_id=live_rec.pricing.market_id,
                signal_ts=live_rec.signal.signal_timestamp_utc,
                decision=live_rec.decision_summary.decision,
                executable_ev=live_rec.decision_summary.execution_adjusted_ev,
                fill_fraction=live_rec.decision_summary.fill_fraction,
                intended_size_usdc=live_rec.intended_size_usdc,
                passed_paper_strict=strict_exec,
                passed_paper_loose=loose_exec,
            ))
        elif loose_exec and not live_exec:
            ref = loose_rec
            assert ref is not None
            loose_only.append(WouldTradeCandidate(
                asset=ref.signal.asset,
                market_id=ref.pricing.market_id,
                signal_ts=ref.signal.signal_timestamp_utc,
                decision=ref.decision_summary.decision,
                executable_ev=ref.decision_summary.execution_adjusted_ev,
                fill_fraction=ref.decision_summary.fill_fraction,
                intended_size_usdc=ref.intended_size_usdc,
                passed_paper_strict=strict_exec,
                passed_paper_loose=True,
            ))

    return would_trade, loose_only
