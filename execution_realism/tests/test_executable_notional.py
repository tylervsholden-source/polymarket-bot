"""
execution_realism/tests/test_executable_notional.py

Phase 11: executable_notional_usdc computation.

executable_notional_usdc = fill_fraction × intended_size_usdc
  - FILLABLE:    fill_fraction=1.00 → executable_notional = intended_size
  - PARTIAL:     fill_fraction=0.90 → executable_notional = 0.90 × intended_size
  - UNFILLABLE:  fill_fraction=0.00 → executable_notional = 0.0

fill_fraction field mirrors fill_sim.expected_fill_fraction (explicit audit copy).
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
    snapshot_age_seconds=0.0,
    horizon_minutes=5,
    required_threshold=0.05,
)

# Size and liquidity for each fill bucket
_SIZE         = 100.0
_FILLABLE_LIQ = 420.0   # ratio ≈ 0.238 → FILLABLE  (fill_fraction=1.0)
_PARTIAL_LIQ  = 290.0   # ratio ≈ 0.345 → PARTIAL   (fill_fraction=0.90)
_UNFILLABLE_LIQ = 150.0 # ratio ≈ 0.667 → UNFILLABLE (fill_fraction=0.0)


class TestExecutableNotionalValues:

    def test_fillable_notional_equals_intended(self):
        result = compute_executable_ev(
            **{**_COMMON, "intended_size_usdc": _SIZE, "liquidity_usdc": _FILLABLE_LIQ}
        )
        assert result.fill_sim.fill_decision == FillDecision.FILLABLE
        assert result.executable_notional_usdc == pytest.approx(_SIZE)

    def test_partial_notional_is_fill_fraction_times_size(self):
        result = compute_executable_ev(
            **{**_COMMON, "intended_size_usdc": _SIZE, "liquidity_usdc": _PARTIAL_LIQ}
        )
        assert result.fill_sim.fill_decision == FillDecision.PARTIAL
        expected = result.fill_fraction * _SIZE
        assert result.executable_notional_usdc == pytest.approx(expected, abs=1e-6)

    def test_partial_notional_is_90pct_of_intended(self):
        """fill_fraction=0.90 → executable_notional=90 when intended=100."""
        result = compute_executable_ev(
            **{**_COMMON, "intended_size_usdc": _SIZE, "liquidity_usdc": _PARTIAL_LIQ}
        )
        assert result.fill_sim.fill_decision == FillDecision.PARTIAL
        assert result.fill_fraction == pytest.approx(0.90)
        assert result.executable_notional_usdc == pytest.approx(90.0)

    def test_unfillable_notional_is_zero(self):
        result = compute_executable_ev(
            **{**_COMMON, "intended_size_usdc": _SIZE, "liquidity_usdc": _UNFILLABLE_LIQ}
        )
        assert result.fill_sim.fill_decision == FillDecision.UNFILLABLE
        assert result.executable_notional_usdc == pytest.approx(0.0)

    def test_fillable_fill_fraction_is_one(self):
        result = compute_executable_ev(
            **{**_COMMON, "intended_size_usdc": _SIZE, "liquidity_usdc": _FILLABLE_LIQ}
        )
        assert result.fill_fraction == pytest.approx(1.0)

    def test_partial_fill_fraction_matches_fill_sim(self):
        """fill_fraction field = fill_sim.expected_fill_fraction."""
        result = compute_executable_ev(
            **{**_COMMON, "intended_size_usdc": _SIZE, "liquidity_usdc": _PARTIAL_LIQ}
        )
        assert result.fill_fraction == result.fill_sim.expected_fill_fraction

    def test_fillable_fill_fraction_matches_fill_sim(self):
        result = compute_executable_ev(
            **{**_COMMON, "intended_size_usdc": _SIZE, "liquidity_usdc": _FILLABLE_LIQ}
        )
        assert result.fill_fraction == result.fill_sim.expected_fill_fraction

    def test_notional_in_diagnostics(self):
        """executable_notional_usdc is recorded in diagnostics."""
        result = compute_executable_ev(
            **{**_COMMON, "intended_size_usdc": _SIZE, "liquidity_usdc": _PARTIAL_LIQ}
        )
        assert "executable_notional_usdc" in result.diagnostics
        assert result.diagnostics["executable_notional_usdc"] == pytest.approx(90.0)

    def test_fill_fraction_in_diagnostics(self):
        result = compute_executable_ev(
            **{**_COMMON, "intended_size_usdc": _SIZE, "liquidity_usdc": _PARTIAL_LIQ}
        )
        assert "fill_fraction" in result.diagnostics
        assert result.diagnostics["fill_fraction"] == pytest.approx(0.90)


class TestExecutableNotionalScaling:

    def test_larger_intended_size_gives_larger_notional_fillable(self):
        """FILLABLE: executable_notional scales 1:1 with intended_size."""
        small = compute_executable_ev(
            **{**_COMMON, "intended_size_usdc": 50.0, "liquidity_usdc": 1000.0}
        )
        large = compute_executable_ev(
            **{**_COMMON, "intended_size_usdc": 200.0, "liquidity_usdc": 1000.0}
        )
        assert small.fill_sim.fill_decision == FillDecision.FILLABLE
        assert large.fill_sim.fill_decision == FillDecision.FILLABLE
        assert large.executable_notional_usdc > small.executable_notional_usdc

    def test_partial_notional_less_than_intended(self):
        """PARTIAL: executable_notional < intended_size_usdc (because fill_fraction < 1)."""
        result = compute_executable_ev(
            **{**_COMMON, "intended_size_usdc": _SIZE, "liquidity_usdc": _PARTIAL_LIQ}
        )
        assert result.fill_sim.fill_decision == FillDecision.PARTIAL
        assert result.executable_notional_usdc < _SIZE

    def test_fillable_notional_equals_intended_various_sizes(self):
        """FILLABLE: executable_notional == intended for various sizes."""
        for size in [10.0, 25.0, 50.0, 100.0]:
            liq = size / 0.20  # size/liq=0.20 → FILLABLE
            result = compute_executable_ev(
                **{**_COMMON, "intended_size_usdc": size, "liquidity_usdc": liq}
            )
            assert result.fill_sim.fill_decision == FillDecision.FILLABLE
            assert result.executable_notional_usdc == pytest.approx(size, abs=1e-5)
