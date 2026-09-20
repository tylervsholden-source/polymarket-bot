"""
shadow_runner/readiness.py

Phase 14: Live pilot readiness gate.

Assembles individual readiness checks into a single ReadinessReport with
a formal verdict: GO / CONDITIONAL / NO_GO.

Design decisions:
  1. Readiness requires both evidence sufficiency AND behavioral quality.
     A large but badly-behaving corpus is still NO_GO.
  2. BLOCKER checks always produce NO_GO regardless of other results.
  3. FAIL checks: any FAIL produces NO_GO (Task 5.3: any FAIL blocks pilot —
     tightened from an earlier "1 FAIL -> CONDITIONAL" draft; see
     tests/test_live_pilot_readiness.py's "1 FAIL -> NO_GO" cases).
  4. WARN checks: 3 or more WARNs produce CONDITIONAL.
  5. GO verdict requires: 0 BLOCKERs, 0 FAILs, ≤ 2 WARNs.
  6. Pilot constraints are pre-defined and frozen.
     They describe the ONLY acceptable form of live pilot — always tiny and
     heavily constrained. They are included in ReadinessReport even on NO_GO
     so the operator knows what a future GO would look like.
  7. assess_readiness() always runs 7 core checks against live_metrics.
     Three additional checks are conditional on optional parameters:
       - strict_metrics → paper_strict-vs-live divergence (check 8)
       - regime_review  → rolling stability (check 9)
       - journal_integrity → JSONL corpus integrity (check 10)
     Optional checks are skipped (not WARNed) when not provided, so callers
     that don't have regime/journal data don't get artificially penalized.

Policy choices (explicit, per spec requirement):
  Pilot constraints:
    - max_open_positions = 1          (one position at a time — no portfolio effects)
    - asset = "BTC"                   (most liquid, most signal coverage)
    - horizon_minutes = 15            (15m more stable than 5m for first pilot)
    - max_nominal_usdc = 10.0         ($10 — minimum meaningful amount)
    - daily_max_loss_usdc = 5.0       ($5 = 50% of pilot allocation)
    - single_loss_kill_usdc = 3.0     (any single loss > $3 triggers kill)
    - mandatory_review_hours = 24     (human review every 24 hours required)
    - pilot_evaluation_days = 3       (72 hours for first evaluation window)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from shadow_runner.summary_metrics import ShadowSummaryMetrics
from shadow_runner.validation import EvidenceSufficiencyResult
from monitoring.readiness_checks import (
    CheckLevel,
    CheckResult,
    check_ev_haircut_pct,
    check_execution_rate,
    check_journal_integrity,
    check_paper_strict_divergence,
    check_partial_fill_rejection_rate,
    check_profile_divergence,
    check_regime_stability,
    check_rejection_concentration,
    check_stale_pricing_rate,
    check_suspicious_underround_rate,
)


class ReadinessVerdict(str, Enum):
    TINY_PILOT_CANDIDATE = "TINY_PILOT_CANDIDATE"   # all checks pass — constrained pilot may proceed
    CONDITIONAL_REVIEW   = "CONDITIONAL_REVIEW"     # warnings present — human review required first
    NO_GO                = "NO_GO"                  # blocker or failures — pilot blocked
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE" # evidence corpus too small to assess

    # Aliases kept for backward compatibility in tests/scripts
    GO          = "TINY_PILOT_CANDIDATE"
    CONDITIONAL = "CONDITIONAL_REVIEW"


@dataclass(frozen=True)
class PilotConstraints:
    """
    Constraints that govern any live pilot.

    These are pre-defined and frozen before any GO is ever issued.
    They represent the ONLY acceptable form of live pilot.
    """
    max_open_positions:        int   = 1
    pilot_asset:               str   = "BTC"
    pilot_horizon_minutes:     int   = 15
    max_nominal_usdc:          float = 10.0
    daily_max_loss_usdc:       float = 5.0
    single_loss_kill_usdc:     float = 3.0
    mandatory_review_hours:    int   = 24
    pilot_evaluation_days:     int   = 3
    notes: str = (
        "Live pilot is tiny and constrained. "
        "GO verdict authorizes a 3-day, single-asset, $10 max position pilot only. "
        "This is NOT authorization for general live trading."
    )


# Canonical pilot constraints — do not change without updating tests
LIVE_PILOT_CONSTRAINTS = PilotConstraints()


@dataclass
class ReadinessReport:
    """
    Output of assess_readiness().

    verdict is the single authoritative answer: GO / CONDITIONAL / NO_GO.
    blockers lists all CheckResults with level=BLOCKER.
    all_checks lists every check run.
    pilot_constraints is always populated — it describes future GO conditions.
    """
    verdict:              ReadinessVerdict
    evidence_sufficient:  bool
    checks:               list           # list[CheckResult]
    blockers:             list           # list[CheckResult] — subset of checks
    fails:                list           # list[CheckResult] — subset of checks
    warns:                list           # list[CheckResult] — subset of checks
    verdict_reason:       str
    pilot_constraints:    PilotConstraints


def assess_readiness(
    live_metrics: ShadowSummaryMetrics,
    evidence_result: EvidenceSufficiencyResult,
    loose_metrics: Optional[ShadowSummaryMetrics] = None,
    strict_metrics: Optional[ShadowSummaryMetrics] = None,
    regime_review: Optional[object] = None,
    journal_integrity: Optional[object] = None,
) -> ReadinessReport:
    """
    Assess live pilot readiness from shadow metrics.

    Parameters
    ----------
    live_metrics : ShadowSummaryMetrics
        Metrics for the live (or live-like) profile.
    evidence_result : EvidenceSufficiencyResult
        Output of check_evidence_sufficiency(). Must be pre-computed by caller.
    loose_metrics : ShadowSummaryMetrics | None
        Metrics for paper_loose profile. Used for loose-vs-live divergence check.
        If None, divergence check returns WARN.
    strict_metrics : ShadowSummaryMetrics | None
        Metrics for paper_strict profile. Used for strict-vs-live divergence check.
        If None, check is skipped entirely.
    regime_review : RegimeReview | None
        Rolling stability analysis from monitoring.regime_review.
        If None, check is skipped entirely.
    journal_integrity : JournalIntegrityStats | None
        Parse integrity stats from JournalReader.read_all_with_integrity().
        If None, check is skipped entirely.

    Returns
    -------
    ReadinessReport
        verdict is always populated. GO only if all criteria are met.
    """
    checks: list[CheckResult] = []

    # Evidence sufficiency is a pre-condition: failure → INSUFFICIENT_EVIDENCE immediately
    if not evidence_result.sufficient:
        gaps_str = "; ".join(g.message for g in evidence_result.gaps)
        return ReadinessReport(
            verdict=ReadinessVerdict.INSUFFICIENT_EVIDENCE,
            evidence_sufficient=False,
            checks=[],
            blockers=[],
            fails=[],
            warns=[],
            verdict_reason=f"Insufficient shadow evidence: {gaps_str}",
            pilot_constraints=LIVE_PILOT_CONSTRAINTS,
        )

    # Run behavioral checks
    checks.append(check_execution_rate(
        live_metrics.execution_rate if live_metrics.total_evaluated > 0 else None
    ))
    checks.append(check_ev_haircut_pct(live_metrics.mean_ev_haircut_pct))
    checks.append(check_suspicious_underround_rate(live_metrics.suspicious_underround_rate))
    checks.append(check_stale_pricing_rate(live_metrics.stale_pricing_rate))
    checks.append(check_partial_fill_rejection_rate(live_metrics.partial_fill_rejection_rate))
    checks.append(check_profile_divergence(
        live_metrics.execution_rate if live_metrics.total_evaluated > 0 else None,
        loose_metrics.execution_rate if loose_metrics and loose_metrics.total_evaluated > 0 else None,
    ))
    checks.append(check_rejection_concentration(live_metrics.rejection_rates))

    # Optional checks — only run when caller provides the relevant data
    if strict_metrics is not None:
        checks.append(check_paper_strict_divergence(
            live_metrics.execution_rate if live_metrics.total_evaluated > 0 else None,
            strict_metrics.execution_rate if strict_metrics.total_evaluated > 0 else None,
        ))
    if regime_review is not None:
        checks.append(check_regime_stability(regime_review))
    if journal_integrity is not None:
        checks.append(check_journal_integrity(journal_integrity))

    # Required-for-GO checks: strict_metrics and regime_review are not optional
    # for TINY_PILOT_CANDIDATE. If either is missing, cap verdict at CONDITIONAL_REVIEW.
    missing_required = []
    if strict_metrics is None:
        missing_required.append("paper_strict metrics")
    if regime_review is None:
        missing_required.append("regime review")

    blockers = [c for c in checks if c.level == CheckLevel.BLOCKER]
    fails    = [c for c in checks if c.level == CheckLevel.FAIL]
    warns    = [c for c in checks if c.level == CheckLevel.WARN]

    # Determine verdict
    if blockers:
        blocker_names = ", ".join(c.name for c in blockers)
        verdict = ReadinessVerdict.NO_GO
        reason  = f"Hard blocker(s) active: {blocker_names}"
    elif len(fails) >= 1:
        fail_names = ", ".join(c.name for c in fails)
        verdict = ReadinessVerdict.NO_GO
        reason  = f"Failure(s) present ({len(fails)}): {fail_names}"
    elif len(warns) >= 3:
        warn_names = ", ".join(c.name for c in warns)
        verdict = ReadinessVerdict.CONDITIONAL_REVIEW
        reason  = f"Multiple warnings ({len(warns)}): {warn_names}"
    elif missing_required:
        # Behavioral checks passed, but required inputs were not provided.
        # Cannot issue GO without paper_strict and regime evidence.
        missing_str = " and ".join(missing_required)
        verdict = ReadinessVerdict.CONDITIONAL_REVIEW
        reason  = (
            f"Behavioral checks passed but required inputs missing: {missing_str}. "
            f"TINY_PILOT_CANDIDATE requires both paper_strict metrics and regime review."
        )
    else:
        verdict = ReadinessVerdict.TINY_PILOT_CANDIDATE
        reason  = (
            f"All checks passed or acceptable. "
            f"Warns: {len(warns)}, Fails: 0, Blockers: 0. "
            f"Tiny constrained pilot may proceed — see pilot_constraints."
        )

    return ReadinessReport(
        verdict=verdict,
        evidence_sufficient=True,
        checks=checks,
        blockers=blockers,
        fails=fails,
        warns=warns,
        verdict_reason=reason,
        pilot_constraints=LIVE_PILOT_CONSTRAINTS,
    )


def format_readiness_report(report: ReadinessReport) -> str:
    """Human-readable readiness report for daily review."""
    lines = [
        "=" * 70,
        f"LIVE PILOT READINESS REPORT",
        f"Verdict: {report.verdict.value}",
        f"Evidence sufficient: {report.evidence_sufficient}",
        "-" * 70,
        f"Reason: {report.verdict_reason}",
        "",
        "CHECKS:",
    ]
    for c in report.checks:
        val_str = f"{c.metric_value:.4f}" if c.metric_value is not None else "N/A"
        lines.append(f"  [{c.level.value:7s}] {c.name}: {val_str} — {c.message}")

    if report.verdict == ReadinessVerdict.TINY_PILOT_CANDIDATE:
        pc = report.pilot_constraints
        lines += [
            "",
            "PILOT CONSTRAINTS (if proceeding):",
            f"  Asset:              {pc.pilot_asset}",
            f"  Horizon:            {pc.pilot_horizon_minutes}m",
            f"  Max open positions: {pc.max_open_positions}",
            f"  Max nominal size:   ${pc.max_nominal_usdc:.2f}",
            f"  Daily max loss:     ${pc.daily_max_loss_usdc:.2f}",
            f"  Kill threshold:     ${pc.single_loss_kill_usdc:.2f} per trade",
            f"  Mandatory review:   every {pc.mandatory_review_hours}h",
            f"  Evaluation window:  {pc.pilot_evaluation_days} days",
            f"  Note: {pc.notes}",
        ]

    lines.append("=" * 70)
    return "\n".join(lines)
