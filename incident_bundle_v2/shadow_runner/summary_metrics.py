"""
shadow_runner/summary_metrics.py

Phase 14: Aggregate shadow run metrics for a single policy profile.

Designed to be the canonical input for:
  - shadow_runner/validation.py  (evidence sufficiency)
  - shadow_runner/readiness.py   (pilot readiness gate)
  - monitoring/regime_review.py  (rolling stability)

Design decisions:
  1. Operates on a flat list of ShadowDecisionRecord — caller filters by date range.
  2. Profile is a filter, not an assumption. Pass profile=None to aggregate all.
  3. EV haircut uses gross_ev - execution_adjusted_ev.
       gross_ev         = raw calibrated edge before execution friction
       execution_adj_ev = after fee + fill discount
     Only computed for records where both fields are not None.
  4. Fillability classifies by fill_fraction + rejection_reason.
     PARTIAL_FILL_REJECTED is counted separately from partial-fill executes.
  5. observation_days is (ts_last - ts_first).total_seconds() / 86400.
     A single-day run returns < 1.0. Callers compare to minimum requirements.
  6. annotated_execute_count = paper_loose executes with pricing_sanity_notes set.
     This is always 0 for live/paper_strict (they reject instead of annotating).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from shadow_runner.types import ShadowDecisionRecord


@dataclass
class ShadowSummaryMetrics:
    """
    Aggregated metrics for one policy profile over a window of shadow records.

    All rates are fractions in [0, 1].
    All counts include EXECUTE and REJECT decisions.
    """
    profile: str
    total_evaluated: int
    execute_count: int
    reject_count: int
    execution_rate: float                     # execute_count / total

    # Rejection composition: {reason_str: count}
    rejection_counts: dict
    # {reason_str: fraction of total_evaluated}
    rejection_rates: dict

    # EV chain — None if no records reached the EV gate
    mean_gross_ev: Optional[float]            # before execution friction
    mean_executable_ev: Optional[float]       # after fee + fill discount
    mean_ev_haircut_abs: Optional[float]      # gross - executable (absolute)
    mean_ev_haircut_pct: Optional[float]      # haircut / gross (fraction)
    ev_haircut_sample_size: int               # records contributing to EV stats

    # Fillability
    full_fill_execute_count: int              # executes with fill_fraction >= 0.95
    partial_fill_execute_count: int           # executes with fill_fraction in [0.10, 0.95)
    partial_fill_rejected_count: int          # rejected with reason PARTIAL_FILL_REJECTED
    partial_fill_rejection_rate: float        # partial_fill_rejected_count / total

    # Pricing sanity
    suspicious_underround_count: int
    stale_pricing_count: int
    suspicious_underround_rate: float         # / total
    stale_pricing_rate: float                 # / total
    annotated_execute_count: int              # executes with pricing_sanity_notes (paper_loose only)

    # Coverage
    assets_seen: list                         # sorted list of asset strings
    horizons_seen: list                       # sorted list of horizon_minutes ints

    # Temporal span
    ts_first: Optional[datetime]
    ts_last: Optional[datetime]
    observation_days: float                   # fractional calendar days spanned


def compute_summary_metrics(
    records: list[ShadowDecisionRecord],
    profile: str = "live",
) -> ShadowSummaryMetrics:
    """
    Compute aggregate metrics for records matching the given profile.

    Parameters
    ----------
    records : list[ShadowDecisionRecord]
        Raw shadow records from journal or in-memory accumulation.
    profile : str
        Policy profile to filter on. e.g. "live", "paper_strict", "paper_loose".

    Returns
    -------
    ShadowSummaryMetrics
        All fields populated. Counts are 0 and rates are 0.0 for empty input.
    """
    recs = [r for r in records if r.policy_profile == profile]
    total = len(recs)

    if total == 0:
        return _empty_metrics(profile)

    executes = [r for r in recs if r.decision_summary.decision != "REJECT"]
    rejects  = [r for r in recs if r.decision_summary.decision == "REJECT"]

    exec_count = len(executes)
    rej_count  = len(rejects)

    # Rejection composition
    rej_counts: dict[str, int] = {}
    for r in rejects:
        reason = r.decision_summary.rejection_reason or "UNKNOWN"
        rej_counts[reason] = rej_counts.get(reason, 0) + 1
    rej_rates: dict[str, float] = {k: v / total for k, v in rej_counts.items()}

    # EV haircut — only records where both gross and executable are available
    ev_pairs = [
        (r.decision_summary.gross_ev, r.decision_summary.execution_adjusted_ev)
        for r in recs
        if r.decision_summary.gross_ev is not None
        and r.decision_summary.execution_adjusted_ev is not None
    ]
    n_ev = len(ev_pairs)
    if n_ev > 0:
        mean_gross_ev   = sum(g for g, _ in ev_pairs) / n_ev
        mean_exec_ev    = sum(e for _, e in ev_pairs) / n_ev
        abs_haircuts    = [g - e for g, e in ev_pairs]
        mean_hc_abs     = sum(abs_haircuts) / n_ev
        pct_haircuts    = [(g - e) / g for g, e in ev_pairs if g > 0]
        mean_hc_pct     = sum(pct_haircuts) / len(pct_haircuts) if pct_haircuts else None
    else:
        mean_gross_ev = mean_exec_ev = mean_hc_abs = mean_hc_pct = None

    # Fillability
    full_fill_exec = sum(
        1 for r in executes
        if r.decision_summary.fill_fraction is not None
        and r.decision_summary.fill_fraction >= 0.95
    )
    partial_fill_exec = sum(
        1 for r in executes
        if r.decision_summary.fill_fraction is not None
        and r.decision_summary.fill_fraction < 0.95
    )
    partial_fill_rejected = rej_counts.get("PARTIAL_FILL_REJECTED", 0)

    # Pricing sanity
    underround_count = rej_counts.get("SUSPICIOUS_UNDERROUND", 0)
    stale_count      = rej_counts.get("STALE_PRICING", 0)
    annotated_exec   = sum(
        1 for r in executes
        if r.decision_summary.pricing_sanity_notes is not None
    )

    # Coverage
    assets   = sorted(set(r.signal.asset for r in recs))
    horizons = sorted(set(r.signal.horizon_minutes for r in recs))

    # Temporal span
    ts_list = [r.ts_recorded_utc for r in recs]
    ts_first = min(ts_list) if ts_list else None
    ts_last  = max(ts_list) if ts_list else None
    if ts_first and ts_last:
        # ensure tz-aware comparison
        if ts_first.tzinfo is None:
            ts_first = ts_first.replace(tzinfo=timezone.utc)
        if ts_last.tzinfo is None:
            ts_last = ts_last.replace(tzinfo=timezone.utc)
        obs_days = (ts_last - ts_first).total_seconds() / 86400.0
    else:
        obs_days = 0.0

    return ShadowSummaryMetrics(
        profile=profile,
        total_evaluated=total,
        execute_count=exec_count,
        reject_count=rej_count,
        execution_rate=exec_count / total,

        rejection_counts=rej_counts,
        rejection_rates=rej_rates,

        mean_gross_ev=mean_gross_ev,
        mean_executable_ev=mean_exec_ev,
        mean_ev_haircut_abs=mean_hc_abs,
        mean_ev_haircut_pct=mean_hc_pct,
        ev_haircut_sample_size=n_ev,

        full_fill_execute_count=full_fill_exec,
        partial_fill_execute_count=partial_fill_exec,
        partial_fill_rejected_count=partial_fill_rejected,
        partial_fill_rejection_rate=partial_fill_rejected / total,

        suspicious_underround_count=underround_count,
        stale_pricing_count=stale_count,
        suspicious_underround_rate=underround_count / total,
        stale_pricing_rate=stale_count / total,
        annotated_execute_count=annotated_exec,

        assets_seen=assets,
        horizons_seen=horizons,
        ts_first=ts_first,
        ts_last=ts_last,
        observation_days=obs_days,
    )


def _empty_metrics(profile: str) -> ShadowSummaryMetrics:
    return ShadowSummaryMetrics(
        profile=profile,
        total_evaluated=0,
        execute_count=0,
        reject_count=0,
        execution_rate=0.0,
        rejection_counts={},
        rejection_rates={},
        mean_gross_ev=None,
        mean_executable_ev=None,
        mean_ev_haircut_abs=None,
        mean_ev_haircut_pct=None,
        ev_haircut_sample_size=0,
        full_fill_execute_count=0,
        partial_fill_execute_count=0,
        partial_fill_rejected_count=0,
        partial_fill_rejection_rate=0.0,
        suspicious_underround_count=0,
        stale_pricing_count=0,
        suspicious_underround_rate=0.0,
        stale_pricing_rate=0.0,
        annotated_execute_count=0,
        assets_seen=[],
        horizons_seen=[],
        ts_first=None,
        ts_last=None,
        observation_days=0.0,
    )
