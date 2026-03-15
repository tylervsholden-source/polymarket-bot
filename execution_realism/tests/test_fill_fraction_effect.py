"""
execution_realism/tests/test_fill_fraction_effect.py

Phase 11: How fill_fraction affects ev_before_fill_adjustment vs executable_ev.

ev_before_fill_adjustment = theoretical - fee - slippage - staleness
executable_ev             = ev_before_fill_adjustment - partial_fill_penalty

For FILLABLE: ev_before_fill == executable_ev (penalty=0)
For PARTIAL:  ev_before_fill > executable_ev  (penalty > 0)
For UNFILLABLE: penalty=0, but passes_gate=False
"""
from __future__ import annotations

import pytest

from execution_realism.core import compute_executable_ev
from execution_realism.types import FillDecision

_COMMON = dict(
    side="YES",
    calibrated_event_probability=0.70,
    ask_price=0.50,
    fee_pct=0.01,
    intended_size_usdc=100.0,
    snapshot_age_seconds=0.0,
    horizon_minutes=5,
    required_threshold=0.05,
)

_FILLABLE_LIQ   = 420.0
_PARTIAL_LIQ    = 290.0
_UNFILLABLE_LIQ = 150.0


class TestEvBeforeFillAdjustment:

    def test_fillable_ev_before_equals_executable_ev(self):
        """FILLABLE: ev_before_fill_adjustment == executable_ev (no penalty)."""
        result = compute_executable_ev(**{**_COMMON, "liquidity_usdc": _FILLABLE_LIQ})
        assert result.fill_sim.fill_decision == FillDecision.FILLABLE
        assert result.ev_before_fill_adjustment == pytest.approx(result.executable_ev)

    def test_partial_ev_before_greater_than_executable_ev(self):
        """PARTIAL: ev_before_fill > executable_ev (penalty reduces EV)."""
        result = compute_executable_ev(**{**_COMMON, "liquidity_usdc": _PARTIAL_LIQ})
        assert result.fill_sim.fill_decision == FillDecision.PARTIAL
        assert result.ev_before_fill_adjustment > result.executable_ev

    def test_partial_ev_before_minus_penalty_equals_executable_ev(self):
        """PARTIAL: ev_before - penalty == executable_ev (exact formula)."""
        result = compute_executable_ev(**{**_COMMON, "liquidity_usdc": _PARTIAL_LIQ})
        expected = result.ev_before_fill_adjustment - result.partial_fill_penalty
        assert result.executable_ev == pytest.approx(round(expected, 6), abs=1e-9)

    def test_unfillable_ev_before_equals_executable_ev(self):
        """UNFILLABLE: penalty=0, so ev_before == executable_ev."""
        result = compute_executable_ev(**{**_COMMON, "liquidity_usdc": _UNFILLABLE_LIQ})
        assert result.fill_sim.fill_decision == FillDecision.UNFILLABLE
        assert result.ev_before_fill_adjustment == pytest.approx(result.executable_ev)

    def test_ev_before_fill_in_diagnostics(self):
        """ev_before_fill_adjustment is recorded in diagnostics."""
        result = compute_executable_ev(**{**_COMMON, "liquidity_usdc": _PARTIAL_LIQ})
        assert "ev_before_fill_adjustment" in result.diagnostics
        assert result.diagnostics["ev_before_fill_adjustment"] == pytest.approx(
            result.ev_before_fill_adjustment
        )

    def test_ev_before_fill_computed_from_base_frictions_only(self):
        """ev_before_fill = theoretical - fee - slippage - staleness (no fill penalty)."""
        result = compute_executable_ev(**{**_COMMON, "liquidity_usdc": _PARTIAL_LIQ})
        theoretical = 0.70 - 0.50
        expected_before = (
            theoretical
            - result.fee_cost
            - result.slippage.total_slippage
            - result.staleness.penalty
        )
        assert result.ev_before_fill_adjustment == pytest.approx(round(expected_before, 6), abs=1e-9)

    def test_fillable_ev_before_fill_computed_correctly(self):
        """FILLABLE ev_before_fill = theoretical - fee - slippage - staleness."""
        result = compute_executable_ev(**{**_COMMON, "liquidity_usdc": _FILLABLE_LIQ})
        theoretical = 0.70 - 0.50
        expected_before = (
            theoretical
            - result.fee_cost
            - result.slippage.total_slippage
            - result.staleness.penalty
        )
        assert result.ev_before_fill_adjustment == pytest.approx(round(expected_before, 6), abs=1e-9)


class TestFillFractionZone:

    def test_fresh_snapshot_no_staleness_partial(self):
        """PARTIAL fill with fresh snapshot: penalty is purely from fill friction."""
        result = compute_executable_ev(**{**_COMMON, "liquidity_usdc": _PARTIAL_LIQ})
        assert result.staleness.penalty == 0.0
        # penalty = (1-0.90) * 0.05 = 0.005
        assert result.partial_fill_penalty == pytest.approx(0.005)

    def test_partial_penalty_formula_exact(self):
        """Exact penalty: (1 - 0.90) * 0.050 = 0.005."""
        result = compute_executable_ev(**{**_COMMON, "liquidity_usdc": _PARTIAL_LIQ})
        assert result.fill_sim.expected_fill_fraction == pytest.approx(0.90)
        assert result.partial_fill_penalty == pytest.approx(0.005)

    def test_fillable_penalty_zero(self):
        result = compute_executable_ev(**{**_COMMON, "liquidity_usdc": _FILLABLE_LIQ})
        assert result.partial_fill_penalty == 0.0

    def test_unfillable_penalty_zero(self):
        result = compute_executable_ev(**{**_COMMON, "liquidity_usdc": _UNFILLABLE_LIQ})
        assert result.partial_fill_penalty == 0.0
