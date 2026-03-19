"""
monitoring/readiness_checks.py

Phase 14: Individual readiness criteria checks.

Each check takes a scalar metric and returns a CheckResult with a
GREEN / WARN / FAIL / BLOCKER level.

Design decisions:
  1. BLOCKER supersedes all other levels — if any check is BLOCKER,
     ReadinessReport.verdict must be NO_GO regardless of other checks.
  2. FAIL means the threshold is clearly violated but not catastrophically.
     Accumulation of FAILs also produces NO_GO (see readiness.py).
  3. WARN is advisory. Two or more WARNs produce a CONDITIONAL verdict.
  4. GREEN = threshold satisfied.
  5. Thresholds are frozen constants in this module.
     Override only through ReadinessConfig (see readiness.py).

Threshold rationale (all thresholds for "live" profile):

  Execution rate [5%, 60%]:
    < 5%:  system is filtering almost everything — likely over-strict or
           signal quality collapsed. Investigate before pilot.
    > 60%: FAIL — system executes most candidates — suspicious unless market is
           highly selective by nature. Likely under-filtering.
    > 85%: BLOCKER — almost nothing is being rejected; pricing and EV
           gates are probably not functioning.

  paper_strict vs live execution rate divergence (absolute pp):
    < 15pp:  GREEN — paper_strict and live profiles are aligned
    15–25pp: WARN  — policy drift between strict and live
    > 25pp:  FAIL  — paper_strict is no longer a reliable reference for live

  Regime stability (rolling window analysis):
    is_stable + no flags:  GREEN
    insufficient data:     WARN
    1 stability flag:      WARN
    2+ stability flags:    FAIL

  Journal integrity (bad lines in JSONL):
    0 bad lines:           GREEN
    >0 but < 5%:           WARN
    >= 5% but < 10%:       FAIL
    >= 10%:                BLOCKER — evidence corpus is unreliable

  EV haircut pct (gross → executable):
    < 30%: acceptable — execution friction is not destroying most of the edge
    30–60%: WARN — large fraction of theoretical edge is lost to friction
    > 60%: FAIL — executable EV is less than 40% of gross EV; edge thesis
           depends heavily on the theoretical number, which is unproven
    > 80%: BLOCKER — execution friction is consuming >80% of theoretical edge;
           pilot trades would be economic noise

  Suspicious underround rate:
    < 10%: GREEN — underround events are occasional
    10–25%: WARN — a noticeable fraction of candidates have suspicious pricing
    > 25%: FAIL — pricing quality is poor; many trades may be artefacts
    > 40%: BLOCKER — majority of "opportunities" are likely pricing artefacts

  Stale pricing rate:
    < 10%: GREEN
    10–30%: WARN
    > 30%: FAIL — pricing feed is consistently lagging

  Partial fill rejection rate:
    < 20%: GREEN
    20–40%: WARN
    > 40%: FAIL — liquidity is consistently insufficient for intended size

  paper_loose vs live-like execution rate divergence (absolute pp):
    < 20pp:  GREEN — profiles are reasonably aligned
    20–40pp: WARN  — loose is materially more permissive
    > 40pp:  FAIL  — paper_loose is not informative about live behavior
    > 60pp:  BLOCKER — paper_loose is dangerously misleading
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class CheckLevel(str, Enum):
    GREEN   = "GREEN"
    WARN    = "WARN"
    FAIL    = "FAIL"
    BLOCKER = "BLOCKER"    # hard no-go regardless of other checks


@dataclass
class CheckResult:
    name: str
    metric_value: Optional[float]
    level: CheckLevel
    threshold_warn: Optional[float]
    threshold_fail: Optional[float]
    threshold_blocker: Optional[float]
    message: str
    is_blocker: bool


def _result(
    name: str,
    value: Optional[float],
    level: CheckLevel,
    t_warn: Optional[float],
    t_fail: Optional[float],
    t_blocker: Optional[float],
    msg: str,
) -> CheckResult:
    return CheckResult(
        name=name,
        metric_value=value,
        level=level,
        threshold_warn=t_warn,
        threshold_fail=t_fail,
        threshold_blocker=t_blocker,
        message=msg,
        is_blocker=(level == CheckLevel.BLOCKER),
    )


# ── Execution rate ─────────────────────────────────────────────────────────────

def check_execution_rate(rate: Optional[float]) -> CheckResult:
    name = "execution_rate"
    if rate is None:
        return _result(name, None, CheckLevel.FAIL, 0.05, 0.60, 0.85,
                       "Execution rate could not be computed (no data).")
    if rate > 0.85:
        return _result(name, rate, CheckLevel.BLOCKER, 0.05, 0.60, 0.85,
                       f"Execution rate {rate:.1%} > 85% — almost nothing is being rejected. "
                       "Pricing, EV, and calibration gates may not be functioning.")
    if rate > 0.60:
        return _result(name, rate, CheckLevel.FAIL, 0.05, 0.60, 0.85,
                       f"Execution rate {rate:.1%} > 60% — suspiciously high. "
                       "Check if edge threshold is too permissive.")
    if rate < 0.05:
        return _result(name, rate, CheckLevel.WARN, 0.05, 0.60, 0.85,
                       f"Execution rate {rate:.1%} < 5% — very few opportunities passing. "
                       "Verify signal quality and threshold calibration.")
    return _result(name, rate, CheckLevel.GREEN, 0.05, 0.60, 0.85,
                   f"Execution rate {rate:.1%} is within normal range [5%, 60%].")


# ── EV haircut ─────────────────────────────────────────────────────────────────

def check_ev_haircut_pct(haircut_pct: Optional[float]) -> CheckResult:
    """
    haircut_pct = (gross_ev - executable_ev) / gross_ev.
    Fraction of theoretical edge consumed by execution friction.
    """
    name = "ev_haircut_pct"
    if haircut_pct is None:
        return _result(name, None, CheckLevel.WARN, 0.30, 0.60, 0.80,
                       "EV haircut could not be computed (no executes with EV data).")
    if haircut_pct > 0.80:
        return _result(name, haircut_pct, CheckLevel.BLOCKER, 0.30, 0.60, 0.80,
                       f"EV haircut {haircut_pct:.1%} > 80% — execution friction consumes "
                       ">80% of theoretical edge. Pilot trades would be economic noise.")
    if haircut_pct > 0.60:
        return _result(name, haircut_pct, CheckLevel.FAIL, 0.30, 0.60, 0.80,
                       f"EV haircut {haircut_pct:.1%} > 60% — executable EV is < 40% "
                       "of gross EV. Edge thesis relies on unproven theoretical number.")
    if haircut_pct > 0.30:
        return _result(name, haircut_pct, CheckLevel.WARN, 0.30, 0.60, 0.80,
                       f"EV haircut {haircut_pct:.1%} > 30% — significant friction. "
                       "Investigate fill model and fee assumptions.")
    return _result(name, haircut_pct, CheckLevel.GREEN, 0.30, 0.60, 0.80,
                   f"EV haircut {haircut_pct:.1%} ≤ 30% — acceptable friction level.")


# ── Suspicious underround ──────────────────────────────────────────────────────

def check_suspicious_underround_rate(rate: Optional[float]) -> CheckResult:
    name = "suspicious_underround_rate"
    if rate is None:
        return _result(name, None, CheckLevel.WARN, 0.10, 0.25, 0.40,
                       "Underround rate could not be computed.")
    if rate > 0.40:
        return _result(name, rate, CheckLevel.BLOCKER, 0.10, 0.25, 0.40,
                       f"Suspicious underround rate {rate:.1%} > 40% — majority of "
                       "candidates have suspect pricing. These are likely artefacts.")
    if rate > 0.25:
        return _result(name, rate, CheckLevel.FAIL, 0.10, 0.25, 0.40,
                       f"Suspicious underround rate {rate:.1%} > 25% — pricing "
                       "quality is poor. Many apparent trades may be pricing errors.")
    if rate > 0.10:
        return _result(name, rate, CheckLevel.WARN, 0.10, 0.25, 0.40,
                       f"Suspicious underround rate {rate:.1%} > 10% — monitor for "
                       "deteriorating pricing feed quality.")
    return _result(name, rate, CheckLevel.GREEN, 0.10, 0.25, 0.40,
                   f"Suspicious underround rate {rate:.1%} ≤ 10% — acceptable.")


# ── Stale pricing ──────────────────────────────────────────────────────────────

def check_stale_pricing_rate(rate: Optional[float]) -> CheckResult:
    name = "stale_pricing_rate"
    if rate is None:
        return _result(name, None, CheckLevel.WARN, 0.10, 0.30, None,
                       "Stale pricing rate could not be computed.")
    if rate > 0.30:
        return _result(name, rate, CheckLevel.FAIL, 0.10, 0.30, None,
                       f"Stale pricing rate {rate:.1%} > 30% — pricing feed is "
                       "consistently lagging. Investigate data pipeline latency.")
    if rate > 0.10:
        return _result(name, rate, CheckLevel.WARN, 0.10, 0.30, None,
                       f"Stale pricing rate {rate:.1%} > 10% — elevated staleness. "
                       "Monitor feed latency.")
    return _result(name, rate, CheckLevel.GREEN, 0.10, 0.30, None,
                   f"Stale pricing rate {rate:.1%} ≤ 10% — acceptable.")


# ── Partial fill rejection ─────────────────────────────────────────────────────

def check_partial_fill_rejection_rate(rate: Optional[float]) -> CheckResult:
    name = "partial_fill_rejection_rate"
    if rate is None:
        return _result(name, None, CheckLevel.WARN, 0.20, 0.40, None,
                       "Partial fill rejection rate could not be computed.")
    if rate > 0.40:
        return _result(name, rate, CheckLevel.FAIL, 0.20, 0.40, None,
                       f"Partial fill rejection rate {rate:.1%} > 40% — liquidity is "
                       "consistently insufficient for intended size. Reduce intended_size_usdc "
                       "or widen acceptable fill fraction.")
    if rate > 0.20:
        return _result(name, rate, CheckLevel.WARN, 0.20, 0.40, None,
                       f"Partial fill rejection rate {rate:.1%} > 20% — elevated. "
                       "Monitor intended size vs liquidity profile.")
    return _result(name, rate, CheckLevel.GREEN, 0.20, 0.40, None,
                   f"Partial fill rejection rate {rate:.1%} ≤ 20% — acceptable.")


# ── paper_loose vs live-like divergence ────────────────────────────────────────

def check_profile_divergence(
    live_exec_rate: Optional[float],
    loose_exec_rate: Optional[float],
) -> CheckResult:
    """
    Check how much paper_loose overstates opportunity vs live-like.

    divergence = loose_exec_rate - live_exec_rate  (absolute pp).
    """
    name = "paper_loose_vs_live_divergence"
    if live_exec_rate is None or loose_exec_rate is None:
        return _result(name, None, CheckLevel.WARN, 0.20, 0.40, 0.60,
                       "Cannot compute profile divergence: missing execution rates.")
    assert live_exec_rate is not None and loose_exec_rate is not None
    divergence = loose_exec_rate - live_exec_rate
    if divergence > 0.60:
        return _result(name, divergence, CheckLevel.BLOCKER, 0.20, 0.40, 0.60,
                       f"paper_loose vs live divergence {divergence:.1%} pp > 60% — "
                       "paper_loose is dangerously misleading. Its results must not be "
                       "used to assess readiness.")
    if divergence > 0.40:
        return _result(name, divergence, CheckLevel.FAIL, 0.20, 0.40, 0.60,
                       f"paper_loose vs live divergence {divergence:.1%} pp > 40% — "
                       "paper_loose is not informative about live behavior.")
    if divergence > 0.20:
        return _result(name, divergence, CheckLevel.WARN, 0.20, 0.40, 0.60,
                       f"paper_loose vs live divergence {divergence:.1%} pp > 20% — "
                       "paper_loose is materially more permissive than live. "
                       "Use paper_strict as reference.")
    return _result(name, divergence, CheckLevel.GREEN, 0.20, 0.40, 0.60,
                   f"paper_loose vs live divergence {divergence:.1%} pp ≤ 20% — aligned.")


# ── Rejection concentration ────────────────────────────────────────────────────

def check_rejection_concentration(
    rejection_rates: dict,
    concentration_threshold: float = 0.70,
) -> CheckResult:
    """
    Check whether any single rejection reason dominates above threshold.

    A single reason accounting for > 70% of all rejections (not just reject count,
    but of total evaluated) may indicate a systematic problem worth investigating.
    """
    name = "rejection_concentration"
    if not rejection_rates:
        return _result(name, None, CheckLevel.GREEN, concentration_threshold, None, None,
                       "No rejections observed — nothing to check.")
    top_reason  = max(rejection_rates, key=lambda k: rejection_rates[k])
    top_rate    = rejection_rates[top_reason]
    if top_rate > concentration_threshold:
        return _result(name, top_rate, CheckLevel.WARN, concentration_threshold, None, None,
                       f"Rejection reason '{top_reason}' accounts for {top_rate:.1%} of "
                       f"all evaluated decisions. Investigate whether this reflects genuine "
                       f"market conditions or a systematic filter issue.")
    return _result(name, top_rate, CheckLevel.GREEN, concentration_threshold, None, None,
                   f"No single rejection reason exceeds {concentration_threshold:.0%} "
                   f"of total evaluations. Composition looks diverse.")


# ── paper_strict vs live divergence ────────────────────────────────────────────

def check_paper_strict_divergence(
    live_exec_rate: Optional[float],
    strict_exec_rate: Optional[float],
) -> CheckResult:
    """
    Check how much paper_strict diverges from live execution rate.

    divergence = strict_exec_rate - live_exec_rate (absolute pp).
    Paper_strict should behave close to live; large divergence indicates
    policy drift or threshold misconfiguration.
    """
    name = "paper_strict_vs_live_divergence"
    if live_exec_rate is None or strict_exec_rate is None:
        return _result(name, None, CheckLevel.WARN, 0.15, 0.25, None,
                       "Cannot compute paper_strict divergence: missing execution rates.")
    assert live_exec_rate is not None and strict_exec_rate is not None
    divergence = strict_exec_rate - live_exec_rate
    if divergence > 0.25:
        return _result(name, divergence, CheckLevel.FAIL, 0.15, 0.25, None,
                       f"paper_strict vs live divergence {divergence:.1%} pp > 25% — "
                       "paper_strict is not a reliable reference for live behavior. "
                       "Review edge thresholds between strict and live configs.")
    if divergence > 0.15:
        return _result(name, divergence, CheckLevel.WARN, 0.15, 0.25, None,
                       f"paper_strict vs live divergence {divergence:.1%} pp > 15% — "
                       "monitor for policy drift between strict and live profiles.")
    return _result(name, divergence, CheckLevel.GREEN, 0.15, 0.25, None,
                   f"paper_strict vs live divergence {divergence:.1%} pp ≤ 15% — aligned.")


# ── Regime stability ────────────────────────────────────────────────────────────

def check_regime_stability(regime_review: Any) -> CheckResult:
    """
    Check rolling window stability of the live profile.

    Accepts a RegimeReview object from monitoring.regime_review.
    Uses duck typing to avoid circular import.
    """
    name = "regime_stability"
    if regime_review is None:
        return _result(name, None, CheckLevel.WARN, None, None, None,
                       "No regime review provided — rolling stability not assessed.")
    if not (hasattr(regime_review, "is_stable") and hasattr(regime_review, "flags")):
        return _result(name, None, CheckLevel.WARN, None, None, None,
                       "Regime review object missing expected fields.")
    if regime_review.is_stable:
        n = len(getattr(regime_review, "rolling_snapshots", []))
        return _result(name, 0.0, CheckLevel.GREEN, None, None, None,
                       f"Regime stable over {n} rolling windows. No instability flags.")
    flags = regime_review.flags
    flag_count = sum([
        bool(flags.rejection_rate_spike),
        bool(flags.ev_trend_negative),
        bool(flags.fillability_degrading),
        bool(flags.underround_rising),
    ])
    if flag_count == 0:
        # is_stable=False due to insufficient data
        return _result(name, None, CheckLevel.WARN, None, None, None,
                       f"Regime review: insufficient data for rolling analysis. "
                       f"{regime_review.summary}")
    if flag_count >= 2:
        return _result(name, float(flag_count), CheckLevel.FAIL, None, None, None,
                       f"Regime instability: {flag_count} stability flags triggered. "
                       f"{regime_review.summary}")
    return _result(name, float(flag_count), CheckLevel.WARN, None, None, None,
                   f"Regime: 1 stability flag triggered. {regime_review.summary}")


# ── Journal integrity ───────────────────────────────────────────────────────────

def check_journal_integrity(integrity_stats: Any) -> CheckResult:
    """
    Check for corrupt or skipped lines in the shadow evidence journal.

    Accepts a JournalIntegrityStats object from shadow_runner.journal.
    Uses duck typing to avoid circular import.
    """
    name = "journal_integrity"
    if integrity_stats is None:
        return _result(name, None, CheckLevel.WARN, 0.0, 0.05, None,
                       "No journal integrity stats provided.")
    total = getattr(integrity_stats, "total_lines", 0)
    if total == 0:
        return _result(name, 0.0, CheckLevel.WARN, 0.0, 0.05, 0.10,
                       "Journal is empty — no lines to validate.")
    bad_fraction = getattr(integrity_stats, "bad_line_fraction", 0.0)
    bad_json = getattr(integrity_stats, "bad_json_count", 0)
    version_skips = getattr(integrity_stats, "version_skip_count", 0)
    if bad_fraction >= 0.10:
        return _result(name, bad_fraction, CheckLevel.BLOCKER, 0.0, 0.05, 0.10,
                       f"Journal integrity: {bad_fraction:.1%} of lines are corrupt "
                       f"({bad_json} bad JSON, {version_skips} unknown schema). "
                       "Evidence corpus is unreliable — readiness assessment is invalid.")
    if bad_fraction >= 0.05:
        return _result(name, bad_fraction, CheckLevel.FAIL, 0.0, 0.05, 0.10,
                       f"Journal integrity: {bad_fraction:.1%} of lines are corrupt "
                       f"({bad_json} bad JSON, {version_skips} unknown schema). "
                       "Evidence corpus may be incomplete.")
    if bad_fraction > 0.0:
        return _result(name, bad_fraction, CheckLevel.WARN, 0.0, 0.05, 0.10,
                       f"Journal integrity: {bad_fraction:.1%} of lines skipped "
                       f"({bad_json} bad JSON, {version_skips} unknown schema).")
    return _result(name, 0.0, CheckLevel.GREEN, 0.0, 0.05, 0.10,
                   f"Journal integrity: all {total} lines parsed successfully.")
