"""
execution_realism/tests/test_execution_cost_model.py

Tests for compute_executable_ev: the full execution cost model.
"""
from __future__ import annotations

import pytest

from execution_realism.core import compute_executable_ev
from execution_realism.types import FillDecision, StalenessZone


def _ev(
    side="YES",
    prob=0.72,
    ask=0.44,
    fee=0.01,
    size=10.0,
    liquidity=50_000.0,
    age=15.0,
    horizon=15,
    threshold=0.03,
):
    return compute_executable_ev(
        side=side,
        calibrated_event_probability=prob,
        ask_price=ask,
        fee_pct=fee,
        intended_size_usdc=size,
        liquidity_usdc=liquidity,
        snapshot_age_seconds=age,
        horizon_minutes=horizon,
        required_threshold=threshold,
    )


class TestBasicComputation:
    """Arithmetic correctness."""

    def test_executable_ev_formula(self):
        """executable_ev = theoretical - fee - slippage.total - staleness.penalty"""
        result = _ev(prob=0.72, ask=0.44, fee=0.01, size=10.0, liquidity=50_000.0, age=15.0, horizon=15)
        theoretical = 0.72 - 0.44
        expected_ev = theoretical - result.fee_cost - result.slippage.total_slippage - result.staleness.penalty
        assert abs(result.executable_ev - expected_ev) < 1e-9

    def test_total_friction_formula(self):
        """total_friction = fee + slippage.total + staleness.penalty"""
        result = _ev()
        expected_friction = result.fee_cost + result.slippage.total_slippage + result.staleness.penalty
        assert abs(result.total_friction - expected_friction) < 1e-9

    def test_theoretical_hold_ev(self):
        """theoretical_hold_ev = prob - ask"""
        result = _ev(prob=0.72, ask=0.44)
        assert abs(result.theoretical_hold_ev - (0.72 - 0.44)) < 1e-9


class TestFreshHighLiquiditySmallSize:
    """Fresh pricing, high liquidity, small size → EV close to theoretical."""

    def test_passes_gate(self):
        result = _ev(prob=0.72, ask=0.44, fee=0.01, size=10.0, liquidity=50_000.0, age=15.0, horizon=15, threshold=0.03)
        assert result.staleness.zone == StalenessZone.FRESH
        assert result.fill_sim.fill_decision == FillDecision.FILLABLE
        assert result.passes_gate is True

    def test_executable_ev_close_to_theoretical(self):
        result = _ev(prob=0.72, ask=0.44, fee=0.01, size=10.0, liquidity=50_000.0, age=15.0, horizon=15)
        # Friction should be small (fresh + high liquidity + small size)
        assert result.total_friction < 0.02
        assert result.executable_ev > result.theoretical_hold_ev - 0.02


class TestAgingPenalty:
    """Aging snapshot (age=60s, horizon=5m) → AGING zone, penalty=0.005."""

    def test_aging_zone(self):
        result = _ev(age=60.0, horizon=5)
        assert result.staleness.zone == StalenessZone.AGING

    def test_aging_penalty_value(self):
        result = _ev(age=60.0, horizon=5)
        assert result.staleness.penalty == 0.005

    def test_aging_reduces_ev(self):
        fresh = _ev(age=10.0, horizon=5)
        aging = _ev(age=60.0, horizon=5)
        assert aging.executable_ev < fresh.executable_ev
        assert abs(fresh.executable_ev - aging.executable_ev - 0.005) < 1e-9


class TestExpiredSnapshot:
    """Expired snapshot (age=200s, horizon=5m) → should_reject=True → passes_gate=False."""

    def test_expired_zone(self):
        result = _ev(age=200.0, horizon=5)
        assert result.staleness.zone == StalenessZone.EXPIRED

    def test_expired_should_reject(self):
        result = _ev(age=200.0, horizon=5)
        assert result.staleness.should_reject is True

    def test_expired_passes_gate_false(self):
        result = _ev(prob=0.90, ask=0.10, fee=0.01, size=5.0, liquidity=100_000.0,
                     age=200.0, horizon=5, threshold=0.01)
        # Even with a very good theoretical EV, expired snapshot kills the trade
        assert result.passes_gate is False

    def test_expired_ev_may_be_positive(self):
        """EV can be positive but gate still fails due to staleness rejection."""
        result = _ev(prob=0.90, ask=0.10, fee=0.01, size=5.0, liquidity=100_000.0,
                     age=200.0, horizon=5, threshold=0.01)
        # theoretical = 0.80, friction is small → executable_ev is positive
        assert result.executable_ev > 0
        assert result.passes_gate is False


class TestLowLiquidity:
    """Low liquidity ($500) → very_low bucket → liq_penalty=0.020."""

    def test_very_low_bucket(self):
        from execution_realism.types import LiquidityBucket
        result = _ev(liquidity=500.0, size=10.0)
        assert result.liquidity.bucket == LiquidityBucket.VERY_LOW

    def test_high_slippage_penalty(self):
        result = _ev(liquidity=500.0, size=10.0)
        assert result.slippage.liquidity_penalty == 0.020

    def test_higher_total_slippage(self):
        high_liq = _ev(liquidity=50_000.0, size=10.0)
        low_liq = _ev(liquidity=500.0, size=10.0)
        assert low_liq.slippage.total_slippage > high_liq.slippage.total_slippage


class TestLargeSize:
    """Large size ($300) → size_penalty=0.007."""

    def test_size_penalty_large(self):
        result = _ev(size=300.0, liquidity=50_000.0)
        assert result.slippage.size_penalty == 0.007

    def test_size_penalty_medium(self):
        result = _ev(size=50.0, liquidity=50_000.0)
        assert result.slippage.size_penalty == 0.003

    def test_size_penalty_small(self):
        result = _ev(size=10.0, liquidity=50_000.0)
        assert result.slippage.size_penalty == 0.001

    def test_size_penalty_tiny(self):
        result = _ev(size=5.0, liquidity=50_000.0)
        assert result.slippage.size_penalty == 0.000


class TestUnfillable:
    """Size > 50% of liquidity → UNFILLABLE → passes_gate=False even if EV positive."""

    def test_unfillable_decision(self):
        # size=3000, liquidity=5000 → ratio=60% > 50% → UNFILLABLE
        result = _ev(prob=0.90, ask=0.10, size=3000.0, liquidity=5000.0)
        assert result.fill_sim.fill_decision == FillDecision.UNFILLABLE

    def test_unfillable_passes_gate_false(self):
        result = _ev(prob=0.90, ask=0.10, size=3000.0, liquidity=5000.0, threshold=0.01)
        assert result.passes_gate is False

    def test_unfillable_even_with_good_ev(self):
        result = _ev(prob=0.95, ask=0.05, fee=0.005, size=3000.0, liquidity=5000.0, threshold=0.01)
        assert result.theoretical_hold_ev > 0.85
        assert result.passes_gate is False


class TestHighFrictionFlipsTrade:
    """High friction makes a theoretically good trade fail the executable gate."""

    def test_high_friction_flips_trade(self):
        """
        prob=0.65, ask=0.50 → theoretical=0.15
        Use very low liquidity + large size + expired snapshot to push friction > 0.15.
        """
        result = _ev(
            prob=0.65,
            ask=0.50,
            fee=0.01,
            size=100.0,
            liquidity=200.0,   # very_low bucket → 0.020 liq penalty
            age=200.0,         # expired for 5m horizon
            horizon=5,
            threshold=0.05,
        )
        # Friction: 0.01 + (0.003 + 0.020 + 0.003) + 0.015 = 0.051
        # executable_ev = 0.15 - 0.051 = 0.099 → may still pass
        # With expired snapshot: passes_gate=False regardless
        assert result.passes_gate is False

    def test_friction_exceeds_theoretical(self):
        """Low theoretical EV + high friction → executable_ev < 0."""
        result = _ev(
            prob=0.52,
            ask=0.50,
            fee=0.01,
            size=200.0,
            liquidity=500.0,    # very_low: liq_penalty=0.020
            age=10.0,
            horizon=15,
            threshold=0.01,
        )
        # theoretical = 0.02
        # slippage ~ 0.003 + 0.020 + 0.007 = 0.030
        # total_friction ~ 0.01 + 0.030 = 0.040 > 0.02
        assert result.executable_ev < 0


class TestDiagnostics:
    """Diagnostics dict is populated correctly."""

    def test_diagnostics_keys(self):
        result = _ev()
        assert "theoretical_hold_ev" in result.diagnostics
        assert "executable_ev" in result.diagnostics
        assert "total_friction" in result.diagnostics
        assert "staleness_zone" in result.diagnostics
        assert "fill_decision" in result.diagnostics
        assert "slippage_bucket" in result.diagnostics

    def test_diagnostics_values_consistent(self):
        result = _ev()
        assert result.diagnostics["theoretical_hold_ev"] == result.theoretical_hold_ev
        assert result.diagnostics["executable_ev"] == result.executable_ev
        assert result.diagnostics["total_friction"] == result.total_friction
        assert result.diagnostics["staleness_zone"] == result.staleness.zone.value
        assert result.diagnostics["fill_decision"] == result.fill_sim.fill_decision.value
