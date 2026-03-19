"""
monitoring/metrics.py

Phase 13: Metric extractors for drift monitoring.

Computes per-profile statistics from a window of ShadowDecisionRecords.
These metrics are the inputs to drift detection and alerting.

Design:
  - All extractors accept list[ShadowDecisionRecord] (a window).
  - Windows are sliced by the caller (DriftMonitor). This module is pure functions.
  - No state stored here.
"""
from __future__ import annotations

from collections import Counter
from typing import Optional

from shadow_runner.types import (
    EVMetrics,
    RejectionMetrics,
    ProfileDivergenceMetrics,
    ShadowDecisionRecord,
)


def compute_rejection_metrics(
    records: list[ShadowDecisionRecord],
    policy_profile: str,
) -> RejectionMetrics:
    """
    Compute rejection statistics for a single policy profile window.

    Parameters
    ----------
    records : list[ShadowDecisionRecord]
        Records already filtered to the target policy_profile.
    policy_profile : str
        Profile name (used in the returned object).
    """
    total = len(records)
    if total == 0:
        return RejectionMetrics(
            total_records=0,
            execute_count=0,
            reject_count=0,
            rejection_rate=0.0,
            reason_counts={},
            policy_profile=policy_profile,
        )

    execute_count = sum(1 for r in records if _is_execute(r))
    reject_count  = total - execute_count

    reason_counts: Counter = Counter()
    for r in records:
        if not _is_execute(r):
            reason = r.decision_summary.rejection_reason or "UNKNOWN"
            reason_counts[reason] += 1

    return RejectionMetrics(
        total_records=total,
        execute_count=execute_count,
        reject_count=reject_count,
        rejection_rate=reject_count / total,
        reason_counts=dict(reason_counts),
        policy_profile=policy_profile,
    )


def compute_ev_metrics(
    records: list[ShadowDecisionRecord],
    policy_profile: str,
) -> Optional[EVMetrics]:
    """
    Compute EV distribution for EXECUTE decisions in a window.

    Returns None if there are no EXECUTE decisions in the window.
    """
    executes = [r for r in records if _is_execute(r)]
    ev_values = [
        r.decision_summary.execution_adjusted_ev
        for r in executes
        if r.decision_summary.execution_adjusted_ev is not None
    ]

    if not ev_values:
        return None

    s = sorted(ev_values)
    n = len(s)
    mean = sum(s) / n
    p25  = s[max(0, int(n * 0.25))]
    p75  = s[min(n - 1, int(n * 0.75))]

    return EVMetrics(
        execute_count=len(executes),
        mean_exec_adj_ev=mean,
        p25_exec_adj_ev=p25,
        p75_exec_adj_ev=p75,
        policy_profile=policy_profile,
    )


def compute_divergence_metrics(
    records_a: list[ShadowDecisionRecord],
    profile_a: str,
    records_b: list[ShadowDecisionRecord],
    profile_b: str,
) -> ProfileDivergenceMetrics:
    """
    Compute between-profile divergence for records sharing the same candidates.

    Candidates are matched by (asset, market_id, signal_timestamp_utc).
    """
    def key(r: ShadowDecisionRecord) -> tuple:
        return (
            r.signal.asset,
            r.pricing.market_id,
            r.signal.signal_timestamp_utc,
        )

    map_a = {key(r): r for r in records_a}
    map_b = {key(r): r for r in records_b}
    shared_keys = set(map_a) & set(map_b)
    total_shared = len(shared_keys)

    if total_shared == 0:
        return ProfileDivergenceMetrics(
            profile_a=profile_a,
            profile_b=profile_b,
            total_shared=0,
            agreement_count=0,
            divergence_rate=0.0,
        )

    agreement_count = sum(
        1 for k in shared_keys
        if _is_execute(map_a[k]) == _is_execute(map_b[k])
    )
    divergence_rate = 1.0 - (agreement_count / total_shared)

    return ProfileDivergenceMetrics(
        profile_a=profile_a,
        profile_b=profile_b,
        total_shared=total_shared,
        agreement_count=agreement_count,
        divergence_rate=divergence_rate,
    )


def group_by_profile(
    records: list[ShadowDecisionRecord],
) -> dict[str, list[ShadowDecisionRecord]]:
    """Group a list of records by policy_profile."""
    result: dict[str, list[ShadowDecisionRecord]] = {}
    for r in records:
        result.setdefault(r.policy_profile, []).append(r)
    return result


# ── Private helpers ──────────────────────────────────────────────────────────

def _is_execute(r: ShadowDecisionRecord) -> bool:
    return r.decision_summary.decision in ("EXECUTE_YES", "EXECUTE_NO")
