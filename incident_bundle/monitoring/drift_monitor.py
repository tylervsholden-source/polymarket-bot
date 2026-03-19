"""
monitoring/drift_monitor.py

Phase 13: Drift detection — baseline window vs current window.

Design:
  - DriftMonitor holds a baseline window (the reference distribution).
  - On each call to check(), it compares a new current window to the baseline.
  - Returns a DriftReport with per-profile metrics and computed deltas.
  - Does NOT trigger alerts — that is AlertEngine's job (alerts.py).

Design decisions:
  1. Window-based comparison, not streaming.
     Rationale: simpler to reason about; streaming EWMA deferred to Phase 14.
  2. Baseline is fixed at construction time (pass records to __init__).
     To update the baseline, create a new DriftMonitor with updated records.
  3. All profiles found in baseline are checked. Profiles only in current
     window (not in baseline) are not checked — no baseline to compare to.
  4. EV drift compares mean_exec_adj_ev. P25/P75 drift deferred to Phase 14.
  5. ProfileDivergence is computed for the current window only (not baseline).
     Rationale: divergence is a property of how profiles relate to each other
     right now, not relative to a historical divergence rate.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from monitoring.metrics import (
    compute_divergence_metrics,
    compute_ev_metrics,
    compute_rejection_metrics,
    group_by_profile,
)
from shadow_runner.types import (
    DriftReport,
    EVMetrics,
    ProfileDivergenceMetrics,
    RejectionMetrics,
    ShadowDecisionRecord,
)


class DriftMonitor:
    """
    Compares a current window of records to a stored baseline.

    Parameters
    ----------
    baseline_records : list[ShadowDecisionRecord]
        The reference window. Must contain at least min_baseline_size records.
    profiles : list[str] | None
        Which policy profiles to monitor. If None, all profiles found in
        baseline_records are monitored.
    min_baseline_size : int
        Minimum number of records required in baseline_records.
        Default 5. A smaller baseline gives statistically meaningless rates.
    """

    def __init__(
        self,
        baseline_records: list[ShadowDecisionRecord],
        profiles: Optional[list[str]] = None,
        min_baseline_size: int = 5,
    ) -> None:
        if not baseline_records:
            raise ValueError("DriftMonitor requires at least one baseline record")
        if len(baseline_records) < min_baseline_size:
            raise ValueError(
                f"DriftMonitor baseline has {len(baseline_records)} records but requires "
                f"at least {min_baseline_size}. A smaller baseline produces statistically "
                f"meaningless drift rates. Pass min_baseline_size=N to override."
            )

        self._baseline = baseline_records
        by_profile     = group_by_profile(baseline_records)
        self._profiles = profiles or list(by_profile.keys())

        # Pre-compute baseline rejection and EV metrics
        self._baseline_rejection: dict[str, RejectionMetrics] = {}
        self._baseline_ev: dict[str, Optional[EVMetrics]] = {}
        for p in self._profiles:
            recs = by_profile.get(p, [])
            self._baseline_rejection[p] = compute_rejection_metrics(recs, p)
            self._baseline_ev[p] = compute_ev_metrics(recs, p)

        # Pre-compute baseline divergence rates for all profile pairs.
        # Stored as {"{pa}/{pb}": divergence_rate} for O(1) delta lookup.
        self._baseline_divergence: dict[str, float] = {}
        for i, pa in enumerate(self._profiles):
            for pb in self._profiles[i+1:]:
                div = compute_divergence_metrics(
                    by_profile.get(pa, []), pa,
                    by_profile.get(pb, []), pb,
                )
                self._baseline_divergence[f"{pa}/{pb}"] = div.divergence_rate

    @property
    def baseline_size(self) -> int:
        return len(self._baseline)

    @property
    def profiles(self) -> list[str]:
        return list(self._profiles)

    def check(self, current_records: list[ShadowDecisionRecord]) -> DriftReport:
        """
        Compare current_records against the baseline.

        Parameters
        ----------
        current_records : list[ShadowDecisionRecord]
            The window to compare. Typically the most recent N records.

        Returns
        -------
        DriftReport
        """
        by_profile = group_by_profile(current_records)

        current_rejection: dict[str, RejectionMetrics] = {}
        current_ev: dict[str, Optional[EVMetrics]] = {}
        rejection_rate_delta: dict[str, float] = {}
        ev_mean_delta: dict[str, float] = {}

        for p in self._profiles:
            recs = by_profile.get(p, [])
            cur_rej = compute_rejection_metrics(recs, p)
            cur_ev  = compute_ev_metrics(recs, p)
            current_rejection[p] = cur_rej
            current_ev[p]        = cur_ev

            base_rej = self._baseline_rejection[p]
            rejection_rate_delta[p] = (
                cur_rej.rejection_rate - base_rej.rejection_rate
            )

            base_ev = self._baseline_ev[p]
            if base_ev is not None and cur_ev is not None:
                ev_mean_delta[p] = cur_ev.mean_exec_adj_ev - base_ev.mean_exec_adj_ev
            else:
                ev_mean_delta[p] = 0.0

        # Profile divergence for the current window + delta vs baseline
        divergence: list[ProfileDivergenceMetrics] = []
        divergence_delta: dict[str, float] = {}
        profiles_list = list(self._profiles)
        n = len(profiles_list)
        for i in range(n):
            pa = profiles_list[i]
            for j in range(i + 1, n):
                pb = profiles_list[j]
                div = compute_divergence_metrics(
                    by_profile.get(pa, []), pa,
                    by_profile.get(pb, []), pb,
                )
                divergence.append(div)
                pair_key = f"{pa}/{pb}"
                base_div = self._baseline_divergence.get(pair_key, 0.0)
                divergence_delta[pair_key] = div.divergence_rate - base_div

        return DriftReport(
            baseline_window_size=self.baseline_size,
            current_window_size=len(current_records),
            profiles_checked=list(self._profiles),
            baseline_rejection=self._baseline_rejection,
            current_rejection=current_rejection,
            rejection_rate_delta=rejection_rate_delta,
            baseline_ev={p: self._baseline_ev[p] for p in self._profiles},
            current_ev=current_ev,
            ev_mean_delta=ev_mean_delta,
            baseline_divergence=dict(self._baseline_divergence),
            divergence=divergence,
            divergence_delta=divergence_delta,
            ts_computed_utc=datetime.now(timezone.utc),
        )
