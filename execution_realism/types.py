"""execution_realism/types.py — Data models for execution realism."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class StalenessZone(str, Enum):
    FRESH   = "FRESH"
    AGING   = "AGING"
    STALE   = "STALE"
    EXPIRED = "EXPIRED"


class FillDecision(str, Enum):
    FILLABLE   = "FILLABLE"
    PARTIAL    = "PARTIAL"
    UNFILLABLE = "UNFILLABLE"


class LiquidityBucket(str, Enum):
    VERY_HIGH = "very_high"
    HIGH      = "high"
    MEDIUM    = "medium"
    LOW       = "low"
    VERY_LOW  = "very_low"


@dataclass
class StalenessThresholds:
    horizon_minutes:   int
    fresh_max_seconds: float
    aging_max_seconds: float
    stale_max_seconds: float
    aging_penalty:     float
    stale_penalty:     float


STALENESS_5M = StalenessThresholds(
    horizon_minutes=5, fresh_max_seconds=30, aging_max_seconds=90,
    stale_max_seconds=180, aging_penalty=0.005, stale_penalty=0.015,
)
STALENESS_15M = StalenessThresholds(
    horizon_minutes=15, fresh_max_seconds=60, aging_max_seconds=180,
    stale_max_seconds=300, aging_penalty=0.003, stale_penalty=0.010,
)
# 1h and 4h horizons are live-traded (agents/orchestrator.py explicitly
# allows 5m/15m/1h/4h markets, and control_plane/entry_window_guard.py
# treats 60m as a first-class supported horizon), so they need their own
# thresholds too — continuing the same trend as 5m→15m (longer horizon =
# a stale price snapshot matters proportionally less, so wider windows /
# smaller penalties), rather than falling through to the "unsupported
# horizon" branch below (which always returned EXPIRED/should_reject=True,
# silently maxing out the staleness penalty on every 1h/4h trade).
STALENESS_60M = StalenessThresholds(
    horizon_minutes=60, fresh_max_seconds=120, aging_max_seconds=300,
    stale_max_seconds=600, aging_penalty=0.002, stale_penalty=0.007,
)
STALENESS_240M = StalenessThresholds(
    horizon_minutes=240, fresh_max_seconds=180, aging_max_seconds=450,
    stale_max_seconds=900, aging_penalty=0.001, stale_penalty=0.005,
)


@dataclass
class StalenessResult:
    zone:          StalenessZone
    age_seconds:   float
    penalty:       float
    should_reject: bool
    rationale:     str


@dataclass
class LiquidityResult:
    bucket:         LiquidityBucket
    liquidity_usdc: float
    penalty:        float
    rationale:      str


@dataclass
class SlippageResult:
    side:              str
    base_slippage:     float
    liquidity_penalty: float
    size_penalty:      float
    total_slippage:    float
    diagnostics:       dict = field(default_factory=dict)


@dataclass
class FillSimulationResult:
    fill_decision:          FillDecision
    entry_price:            float
    expected_fill_fraction: float
    rationale:              str


@dataclass
class ExecutableCostBreakdown:
    """Full execution cost breakdown. theoretical_hold_ev is the frictionless upper bound.

    partial_fill_penalty: additional friction applied when fill_sim.fill_decision == PARTIAL.
    For FILLABLE trades this is 0.0. For PARTIAL trades it is > 0.0 and is already
    included in total_friction and subtracted from executable_ev.

    Phase 11 audit trail fields:
        executable_notional_usdc — fill_fraction × intended_size_usdc (actual expected fill)
        fill_fraction            — from fill_sim.expected_fill_fraction (explicit audit copy)
        ev_before_fill_adjustment — EV before partial fill penalty; fee+slippage+staleness only
    """
    side:                str
    theoretical_hold_ev: float
    fee_cost:            float
    slippage:            SlippageResult
    staleness:           StalenessResult
    liquidity:           LiquidityResult
    fill_sim:            FillSimulationResult
    total_friction:      float
    executable_ev:       float
    passes_gate:         bool
    required_threshold:  float
    partial_fill_penalty:      float = 0.0
    executable_notional_usdc:  float = 0.0   # fill_fraction × intended_size_usdc
    fill_fraction:             float = 1.0   # explicit copy of fill_sim.expected_fill_fraction
    ev_before_fill_adjustment: float = 0.0   # EV before partial fill penalty (fee+slip+stale only)
    diagnostics:               dict  = field(default_factory=dict)
