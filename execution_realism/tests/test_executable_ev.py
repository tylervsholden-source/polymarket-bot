"""
execution_realism/tests/test_executable_ev.py

Integration tests for compute_executable_ev arithmetic and gate logic.
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


class TestArithmeticContracts:
    """Core formula invariants must hold exactly."""

    def test_theoretical_hold_ev_equals_prob_minus_ask(self):
        result = _ev(prob=0.72, ask=0.44)
        assert abs(result.theoretical_hold_ev - (0.72 - 0.44)) < 1e-9

    def test_total_friction_components(self):
        result = _ev()
        expected = result.fee_cost + result.slippage.total_slippage + result.staleness.penalty
        assert abs(result.total_friction - expected) < 1e-9

    def test_executable_ev_equals_theoretical_minus_friction(self):
        result = _ev()
        expected = result.theoretical_hold_ev - result.total_friction
        assert abs(result.executable_ev - expected) < 1e-9

    def test_fee_cost_matches_input(self):
        result = _ev(fee=0.015)
        assert result.fee_cost == 0.015

    def test_required_threshold_stored(self):
        result = _ev(threshold=0.05)
        assert result.required_threshold == 0.05

    def test_side_stored(self):
        result = _ev(side="NO")
        assert result.side == "NO"


class TestGateConditions:
    """passes_gate requires all three conditions: EV >= threshold, not expired, not unfillable."""

    def test_all_good_passes(self):
        result = _ev(prob=0.72, ask=0.44, fee=0.01, size=10.0, liquidity=50_000.0,
                     age=15.0, horizon=15, threshold=0.03)
        assert result.passes_gate is True

    def test_ev_below_threshold_fails(self):
        # prob=0.52, ask=0.50 → theoretical=0.02, friction > 0.02 → executable_ev < 0
        result = _ev(prob=0.52, ask=0.50, fee=0.01, size=10.0, liquidity=5000.0,
                     age=10.0, horizon=15, threshold=0.05)
        assert result.passes_gate is False

    def test_expired_snapshot_fails_even_with_high_ev(self):
        """Even with prob=0.90, ask=0.10 → theoretical=0.80; expired kills it."""
        result = _ev(prob=0.90, ask=0.10, fee=0.005, size=5.0, liquidity=100_000.0,
                     age=200.0, horizon=5, threshold=0.01)
        assert result.staleness.zone == StalenessZone.EXPIRED
        assert result.staleness.should_reject is True
        assert result.passes_gate is False
        # But EV itself is positive
        assert result.executable_ev > 0

    def test_unfillable_fails_even_with_positive_ev(self):
        """size > 50% of liquidity → UNFILLABLE → passes_gate=False."""
        result = _ev(prob=0.95, ask=0.05, fee=0.005, size=3000.0, liquidity=5000.0,
                     age=10.0, horizon=15, threshold=0.01)
        assert result.fill_sim.fill_decision == FillDecision.UNFILLABLE
        assert result.passes_gate is False
        # EV should be positive
        assert result.executable_ev > 0


class TestFreshGoodLiquidityCloseToTheoretical:
    """Fresh + good liquidity → executable_ev close to theoretical."""

    def test_small_friction_fresh_high_liq(self):
        result = _ev(prob=0.72, ask=0.44, fee=0.01, size=10.0, liquidity=100_000.0,
                     age=10.0, horizon=15, threshold=0.01)
        diff = result.theoretical_hold_ev - result.executable_ev
        # Both theoretical_hold_ev and executable_ev are rounded to 6 places in core.py
        # The diff may differ from total_friction by up to 1e-6 due to rounding.
        assert abs(diff - result.total_friction) < 1e-5
        assert diff < 0.02

    def test_executable_ev_positive_and_near_theoretical(self):
        result = _ev(prob=0.72, ask=0.44, fee=0.01, size=5.0, liquidity=200_000.0,
                     age=5.0, horizon=15, threshold=0.01)
        assert result.executable_ev > 0
        assert result.theoretical_hold_ev - result.executable_ev < 0.02


class TestHighFrictionFlipsTrade:
    """High total friction can flip a theoretically positive trade to negative executable EV."""

    def test_high_friction_negative_executable_ev(self):
        """
        prob=0.65, ask=0.50 → theoretical=0.15
        Use very low liquidity + large size → friction > 0.15
        fee=0.01, slippage~0.030, staleness=0.000 → total~0.040+
        0.15 - 0.040 = 0.11 → still positive, need more friction.

        Use tiny liquidity + large size to make friction dominant:
        liq=200 (very_low: 0.020), size=100 (size>=50: 0.003)
        slippage = 0.003 + 0.020 + 0.003 = 0.026
        total = 0.01 + 0.026 = 0.036
        0.15 - 0.036 = 0.114 → positive

        For truly negative EV: narrow edge trade.
        prob=0.53, ask=0.50 → theoretical=0.03
        friction ~ 0.036 > 0.03 → executable_ev < 0
        """
        result = _ev(prob=0.53, ask=0.50, fee=0.01, size=100.0, liquidity=200.0,
                     age=10.0, horizon=15, threshold=0.05)
        assert result.theoretical_hold_ev == pytest.approx(0.03, abs=1e-6)
        assert result.executable_ev < 0

    def test_passes_gate_false_when_friction_exceeds_threshold(self):
        result = _ev(prob=0.53, ask=0.50, fee=0.01, size=100.0, liquidity=200.0,
                     age=10.0, horizon=15, threshold=0.05)
        assert result.passes_gate is False
