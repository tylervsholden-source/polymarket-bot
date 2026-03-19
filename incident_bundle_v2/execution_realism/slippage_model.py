"""execution_realism/slippage_model.py — Side/liquidity/size-aware slippage."""
from __future__ import annotations

from execution_realism.types import LiquidityBucket, LiquidityResult, SlippageResult

_LIQ_BUCKETS = [
    (100_000, 0.001, LiquidityBucket.VERY_HIGH),
    ( 20_000, 0.002, LiquidityBucket.HIGH),
    (  5_000, 0.005, LiquidityBucket.MEDIUM),
    (  1_000, 0.010, LiquidityBucket.LOW),
    (      0, 0.020, LiquidityBucket.VERY_LOW),
]
_SIZE_INCREMENTS = [(200, 0.007), (50, 0.003), (10, 0.001), (0, 0.000)]
_BASE_SLIPPAGE = {"YES": 0.003, "NO": 0.003}


def compute_liquidity_result(liquidity_usdc: float) -> LiquidityResult:
    for threshold, penalty, bucket in _LIQ_BUCKETS:
        if liquidity_usdc > threshold:
            return LiquidityResult(
                bucket=bucket,
                liquidity_usdc=liquidity_usdc,
                penalty=penalty,
                rationale=f"liquidity={liquidity_usdc:.0f} → {bucket.value}, pen={penalty:.3f}",
            )
    return LiquidityResult(
        bucket=LiquidityBucket.VERY_LOW,
        liquidity_usdc=liquidity_usdc,
        penalty=0.020,
        rationale="liquidity=0",
    )


def _size_penalty(size_usdc: float) -> float:
    for threshold, penalty in _SIZE_INCREMENTS:
        if size_usdc >= threshold:
            return penalty
    return 0.0


def compute_slippage(side: str, liquidity_usdc: float, intended_size_usdc: float) -> SlippageResult:
    """Side + liquidity + size aware slippage."""
    base = _BASE_SLIPPAGE.get(side, 0.003)
    liq = compute_liquidity_result(liquidity_usdc)
    sz  = _size_penalty(intended_size_usdc)
    return SlippageResult(
        side=side,
        base_slippage=base,
        liquidity_penalty=liq.penalty,
        size_penalty=sz,
        total_slippage=base + liq.penalty + sz,
        diagnostics={"liquidity_bucket": liq.bucket.value, "size": intended_size_usdc},
    )
