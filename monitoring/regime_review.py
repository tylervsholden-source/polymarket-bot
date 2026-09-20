"""
monitoring/regime_review.py

Phase 14: Rolling stability analysis for shadow evidence.

Answers: "Is the system's behavior stable over time?"

A single summary metric (like average rejection rate) can mask instability.
This module computes rolling windows of metrics to detect:
  - Sudden shifts in rejection rate
  - EV drift over time
  - Fillability degradation
  - Profile divergence changes

Design decisions:
  1. Windows are defined by record count, not calendar time.
     Rationale: shadow evidence accumulates at irregular rates. Count-based
     windows give consistent sample sizes for statistical comparison.
  2. Minimum window size = 5 records. Below this, rates are meaningless.
  3. Rolling windows overlap: window i covers records [i, i+window_size).
     Each record contributes to multiple windows — this is intentional for
     smooth trend detection.
  4. StabilityFlags are set when adjacent window deltas exceed threshold.
     Default delta threshold = 0.15 (15pp) for rejection rate.
  5. RegimeReview.is_stable is True only when no stability flags are set.
     Callers should treat is_stable=False as a reason to continue observing
     before declaring readiness, but NOT necessarily as a hard blocker.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from shadow_runner.types import ShadowDecisionRecord
from shadow_runner.summary_metrics import compute_summary_metrics


@dataclass
class RollingSnapshot:
    """Metrics for a single rolling window."""
    window_index:         int
    window_size:          int
    records_start:        int               # index into the full record list
    records_end:          int
    ts_first:             Optional[datetime]
    ts_last:              Optional[datetime]
    execution_rate:       Optional[float]
    rejection_rate:       Optional[float]
    mean_executable_ev:   Optional[float]
    partial_fill_rate:    Optional[float]   # partial_fill_rejected / total
    underround_rate:      Optional[float]


@dataclass
class StabilityFlags:
    """Flags set when rolling windows show instability."""
    rejection_rate_spike:     bool = False   # delta > threshold between adjacent windows
    ev_trend_negative:        bool = False   # EV dropping across windows
    fillability_degrading:    bool = False   # partial fill rate rising
    underround_rising:        bool = False   # underround rate rising
    notes: list = field(default_factory=list)  # list[str]


@dataclass
class RegimeReview:
    """
    Output of compute_regime_review().

    rolling_snapshots: list of rolling window metrics (chronological).
    flags: StabilityFlags set when instability is detected.
    is_stable: True when no flags are set.
    summary: human-readable stability assessment.
    """
    profile:             str
    window_size:         int
    rolling_snapshots:   list           # list[RollingSnapshot]
    flags:               StabilityFlags
    is_stable:           bool
    summary:             str


def compute_regime_review(
    records: list[ShadowDecisionRecord],
    profile: str = "live",
    window_size: int = 20,
    step: int = 10,
    rejection_spike_threshold: float = 0.15,
    ev_drop_threshold: float = 0.005,
    fillability_rise_threshold: float = 0.10,
    underround_rise_threshold: float = 0.10,
) -> RegimeReview:
    """
    Compute rolling window stability analysis for a profile.

    Parameters
    ----------
    records : list[ShadowDecisionRecord]
        All records (any profile). Filtered to `profile` internally.
    profile : str
        Which profile to analyze.
    window_size : int
        Number of records per rolling window. Minimum 5.
    step : int
        Records to advance between windows.
    rejection_spike_threshold : float
        Delta in rejection rate between adjacent windows that triggers a flag.
    ev_drop_threshold : float
        Absolute drop in mean executable EV between first and last window.
    fillability_rise_threshold : float
        Rise in partial fill rejection rate that triggers a flag.
    underround_rise_threshold : float
        Rise in suspicious underround rate that triggers a flag.

    Returns
    -------
    RegimeReview
    """
    window_size = max(window_size, 5)
    filtered = [r for r in records if r.policy_profile == profile]
    n = len(filtered)

    if n < window_size:
        flags = StabilityFlags(notes=[
            f"Insufficient records ({n}) for rolling analysis (window={window_size}). "
            "Cannot assess stability."
        ])
        return RegimeReview(
            profile=profile,
            window_size=window_size,
            rolling_snapshots=[],
            flags=flags,
            is_stable=False,  # unknown ≠ stable
            summary=f"Insufficient data: {n} records < {window_size} window size.",
        )

    snapshots: list[RollingSnapshot] = []
    start = 0
    idx = 0
    while start + window_size <= n:
        end = start + window_size
        window_recs = filtered[start:end]
        metrics = compute_summary_metrics(window_recs, profile=profile)

        ts_list = [r.ts_recorded_utc for r in window_recs]
        snap = RollingSnapshot(
            window_index=idx,
            window_size=window_size,
            records_start=start,
            records_end=end,
            ts_first=min(ts_list) if ts_list else None,
            ts_last=max(ts_list) if ts_list else None,
            execution_rate=metrics.execution_rate,
            rejection_rate=1.0 - metrics.execution_rate if metrics.total_evaluated > 0 else None,
            mean_executable_ev=metrics.mean_executable_ev,
            partial_fill_rate=metrics.partial_fill_rejection_rate,
            underround_rate=metrics.suspicious_underround_rate,
        )
        snapshots.append(snap)
        start += step
        idx += 1

    flags = StabilityFlags()
    insufficient_windows = len(snapshots) < 2

    if len(snapshots) >= 2:
        rej_rates = [s.rejection_rate for s in snapshots if s.rejection_rate is not None]
        if len(rej_rates) >= 2:
            max_delta = max(
                abs(rej_rates[i+1] - rej_rates[i]) for i in range(len(rej_rates) - 1)
            )
            if max_delta > rejection_spike_threshold:
                flags.rejection_rate_spike = True
                flags.notes.append(
                    f"Rejection rate spike detected: max adjacent delta = "
                    f"{max_delta:.1%} > {rejection_spike_threshold:.0%} threshold."
                )

        ev_vals = [s.mean_executable_ev for s in snapshots if s.mean_executable_ev is not None]
        if len(ev_vals) >= 2:
            total_drop = ev_vals[0] - ev_vals[-1]  # first - last
            if total_drop > ev_drop_threshold:
                flags.ev_trend_negative = True
                flags.notes.append(
                    f"EV declining trend: first window EV={ev_vals[0]:.5f}, "
                    f"last window EV={ev_vals[-1]:.5f}, drop={total_drop:.5f}."
                )

        fill_rates = [s.partial_fill_rate for s in snapshots if s.partial_fill_rate is not None]
        if len(fill_rates) >= 2:
            fill_rise = fill_rates[-1] - fill_rates[0]
            if fill_rise > fillability_rise_threshold:
                flags.fillability_degrading = True
                flags.notes.append(
                    f"Partial fill rejection rising: {fill_rates[0]:.1%} → "
                    f"{fill_rates[-1]:.1%}, rise={fill_rise:.1%}."
                )

        ur_rates = [s.underround_rate for s in snapshots if s.underround_rate is not None]
        if len(ur_rates) >= 2:
            ur_rise = ur_rates[-1] - ur_rates[0]
            if ur_rise > underround_rise_threshold:
                flags.underround_rising = True
                flags.notes.append(
                    f"Suspicious underround rising: {ur_rates[0]:.1%} → "
                    f"{ur_rates[-1]:.1%}, rise={ur_rise:.1%}."
                )

    if insufficient_windows:
        flags.notes.append(
            f"Only {len(snapshots)} rolling window(s) formed (need >= 2 to detect a "
            "trend); stability cannot be assessed."
        )
        is_stable = False  # unknown ≠ stable
    else:
        is_stable = not (
            flags.rejection_rate_spike
            or flags.ev_trend_negative
            or flags.fillability_degrading
            or flags.underround_rising
        )

    if insufficient_windows:
        summary = (
            f"Insufficient rolling windows ({len(snapshots)}) for trend analysis "
            f"(window={window_size}, step={step}); need >= 2 windows. "
            "Cannot assess stability."
        )
    elif is_stable:
        summary = (
            f"Stable over {len(snapshots)} rolling windows "
            f"(window={window_size}, step={step}). No instability flags set."
        )
    else:
        triggered = [n for n in flags.notes]
        summary = (
            f"Instability detected over {len(snapshots)} rolling windows. "
            + " | ".join(triggered)
        )

    return RegimeReview(
        profile=profile,
        window_size=window_size,
        rolling_snapshots=snapshots,
        flags=flags,
        is_stable=is_stable,
        summary=summary,
    )
