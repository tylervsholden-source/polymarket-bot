"""execution_realism/liquidity_model.py — Liquidity quality assessment."""
from __future__ import annotations

from execution_realism.slippage_model import compute_liquidity_result
from execution_realism.types import LiquidityResult


def assess_liquidity(liquidity_usdc: float) -> LiquidityResult:
    """Liquidity quality and its cost penalty."""
    return compute_liquidity_result(liquidity_usdc)


def liquidity_penalty_for_edge(liquidity_usdc: float) -> float:
    """Penalty float only."""
    return compute_liquidity_result(liquidity_usdc).penalty
