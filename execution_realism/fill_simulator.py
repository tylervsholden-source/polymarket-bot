"""
execution_realism/fill_simulator.py — Conservative fill simulation.

PARTIAL FILL POLICY (Phase 9):
    FILLABLE   (size ≤ 25% liquidity): 100% fill, no EV impact beyond normal slippage
    PARTIAL    (25% < size ≤ 50% liq): 90% fill fraction, EV computation uses full size
                                        (conservative: slippage already penalizes large size)
    UNFILLABLE (size > 50% liquidity): REJECT — trade cannot be meaningfully executed

    Policy choice: PARTIAL fills are ALLOWED with reduced expected notional.
    The slippage model's size-bucket penalty already degrades EV for large sizes.
    UNFILLABLE always causes gate failure (passes_gate=False in ExecutableCostBreakdown).
"""
from __future__ import annotations

from execution_realism.types import FillDecision, FillSimulationResult

_MAX_RATIO  = 0.50   # > 50% liquidity → UNFILLABLE
_SOFT_RATIO = 0.25   # > 25% liquidity → PARTIAL
_PARTIAL_FILL = 0.90


def simulate_fill(
    ask_price: float,
    intended_size_usdc: float,
    liquidity_usdc: float,
) -> FillSimulationResult:
    """Conservative fill policy. Assume fill at ask. No fill improvement."""
    if liquidity_usdc <= 0:
        return FillSimulationResult(FillDecision.UNFILLABLE, ask_price, 0.0, "liquidity=0")
    ratio = intended_size_usdc / liquidity_usdc
    if ratio > _MAX_RATIO:
        return FillSimulationResult(
            FillDecision.UNFILLABLE,
            ask_price,
            0.0,
            f"size={intended_size_usdc:.1f} > {_MAX_RATIO*100:.0f}% of liq={liquidity_usdc:.0f} → UNFILLABLE",
        )
    elif ratio > _SOFT_RATIO:
        return FillSimulationResult(
            FillDecision.PARTIAL,
            ask_price,
            _PARTIAL_FILL,
            f"size={intended_size_usdc:.1f} > {_SOFT_RATIO*100:.0f}% of liq={liquidity_usdc:.0f} → PARTIAL ({_PARTIAL_FILL*100:.0f}%)",
        )
    else:
        return FillSimulationResult(
            FillDecision.FILLABLE,
            ask_price,
            1.0,
            f"size={intended_size_usdc:.1f} ≤ {_SOFT_RATIO*100:.0f}% of liq={liquidity_usdc:.0f} → FILLABLE",
        )
