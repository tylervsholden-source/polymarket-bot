"""execution_realism/staleness_penalty.py — Horizon-aware staleness penalty."""
from __future__ import annotations

from execution_realism.types import (
    StalenessZone,
    StalenessResult,
    STALENESS_5M,
    STALENESS_15M,
    STALENESS_60M,
    STALENESS_240M,
)

_THRESHOLDS = {5: STALENESS_5M, 15: STALENESS_15M, 60: STALENESS_60M, 240: STALENESS_240M}


def compute_staleness_penalty(age_seconds: float, horizon_minutes: int) -> StalenessResult:
    """Horizon-aware staleness: 5m tighter than 15m."""
    thr = _THRESHOLDS.get(horizon_minutes)
    if thr is None:
        return StalenessResult(
            zone=StalenessZone.EXPIRED,
            age_seconds=age_seconds,
            penalty=0.030,
            should_reject=True,
            rationale=f"horizon={horizon_minutes} not supported",
        )
    if age_seconds <= thr.fresh_max_seconds:
        return StalenessResult(
            zone=StalenessZone.FRESH,
            age_seconds=age_seconds,
            penalty=0.0,
            should_reject=False,
            rationale=f"age={age_seconds:.0f}s → FRESH",
        )
    elif age_seconds <= thr.aging_max_seconds:
        return StalenessResult(
            zone=StalenessZone.AGING,
            age_seconds=age_seconds,
            penalty=thr.aging_penalty,
            should_reject=False,
            rationale=f"age={age_seconds:.0f}s → AGING, pen={thr.aging_penalty:.3f}",
        )
    elif age_seconds <= thr.stale_max_seconds:
        return StalenessResult(
            zone=StalenessZone.STALE,
            age_seconds=age_seconds,
            penalty=thr.stale_penalty,
            should_reject=False,
            rationale=f"age={age_seconds:.0f}s → STALE, pen={thr.stale_penalty:.3f}",
        )
    else:
        return StalenessResult(
            zone=StalenessZone.EXPIRED,
            age_seconds=age_seconds,
            penalty=thr.stale_penalty,
            should_reject=True,
            rationale=f"age={age_seconds:.0f}s > {thr.stale_max_seconds}s → EXPIRED",
        )
