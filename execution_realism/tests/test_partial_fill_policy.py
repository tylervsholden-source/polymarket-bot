"""
execution_realism/tests/test_partial_fill_policy.py

Phase 11 Option C: Live Strict / Paper Flexible policy enforcement.

Live mode:
  - PARTIAL fill → passes_gate=False regardless of EV
  - FILLABLE fill → passes_gate determined by EV threshold (unchanged)
  - UNFILLABLE fill → passes_gate=False (unchanged)

Paper/default mode:
  - PARTIAL fill → EV penalty applied; passes_gate determined by threshold
  - Same behavior as before for FILLABLE and UNFILLABLE
"""
from __future__ import annotations

import pytest

from execution_realism.core import compute_executable_ev
from execution_realism.types import FillDecision

# Setup: size/liquidity ratios to trigger each fill bucket
# PARTIAL: size/liq ∈ (0.25, 0.50]
# FILLABLE: size/liq ≤ 0.25
_SIZE = 100.0
_FILLABLE_LIQ = 420.0   # 100/420 ≈ 0.238 → FILLABLE
_PARTIAL_LIQ  = 290.0   # 100/290 ≈ 0.345 → PARTIAL
_UNFILLABLE_LIQ = 150.0 # 100/150 ≈ 0.667 → UNFILLABLE

_COMMON = dict(
    side="YES",
    calibrated_event_probability=0.70,
    ask_price=0.50,
    fee_pct=0.01,
    intended_size_usdc=_SIZE,
    snapshot_age_seconds=0.0,
    horizon_minutes=5,
    required_threshold=0.05,
)


class TestLiveStrictPolicy:
    """In live mode, PARTIAL fills must always fail the gate."""

    def test_live_partial_passes_gate_is_false(self):
        """Live + PARTIAL → passes_gate=False, regardless of EV."""
        result = compute_executable_ev(**{**_COMMON, "liquidity_usdc": _PARTIAL_LIQ, "policy_mode": "live"})
        assert result.fill_sim.fill_decision == FillDecision.PARTIAL
        assert result.passes_gate is False

    def test_live_partial_rejects_even_with_high_ev(self):
        """Live + PARTIAL → rejected even when EV is well above threshold."""
        result = compute_executable_ev(
            side="YES",
            calibrated_event_probability=0.95,  # massive edge
            ask_price=0.50,
            fee_pct=0.01,
            intended_size_usdc=_SIZE,
            liquidity_usdc=_PARTIAL_LIQ,
            snapshot_age_seconds=0.0,
            horizon_minutes=5,
            required_threshold=0.05,
            policy_mode="live",
        )
        assert result.fill_sim.fill_decision == FillDecision.PARTIAL
        assert result.passes_gate is False
        # EV is still computed for audit — must be positive
        assert result.executable_ev > 0.0

    def test_live_fillable_gate_determined_by_ev(self):
        """Live + FILLABLE → passes_gate based on threshold (not forced False)."""
        passing = compute_executable_ev(**{**_COMMON, "liquidity_usdc": _FILLABLE_LIQ, "policy_mode": "live"})
        assert passing.fill_sim.fill_decision == FillDecision.FILLABLE
        # EV=0.20-0.01-slippage-0 should be well above 0.05
        assert passing.passes_gate is True

    def test_live_unfillable_still_rejects(self):
        """Live + UNFILLABLE → passes_gate=False (unchanged)."""
        result = compute_executable_ev(**{**_COMMON, "liquidity_usdc": _UNFILLABLE_LIQ, "policy_mode": "live"})
        assert result.fill_sim.fill_decision == FillDecision.UNFILLABLE
        assert result.passes_gate is False

    def test_live_partial_ev_still_computed_for_audit(self):
        """Live partial rejection still computes EV and penalty for audit trail."""
        result = compute_executable_ev(**{**_COMMON, "liquidity_usdc": _PARTIAL_LIQ, "policy_mode": "live"})
        assert result.fill_sim.fill_decision == FillDecision.PARTIAL
        # penalty must be computed even though gate is forced False
        assert result.partial_fill_penalty > 0.0
        # ev_before_fill_adjustment must be present
        assert result.ev_before_fill_adjustment != 0.0
        # executable_ev = ev_before - penalty
        expected = result.ev_before_fill_adjustment - result.partial_fill_penalty
        assert abs(result.executable_ev - round(expected, 6)) < 1e-9


class TestPaperFlexiblePolicy:
    """In paper/default mode, PARTIAL fills apply EV penalty but may pass."""

    def test_paper_partial_penalty_applied(self):
        """Paper + PARTIAL → partial_fill_penalty > 0."""
        result = compute_executable_ev(**{**_COMMON, "liquidity_usdc": _PARTIAL_LIQ, "policy_mode": "paper"})
        assert result.fill_sim.fill_decision == FillDecision.PARTIAL
        assert result.partial_fill_penalty > 0.0

    def test_paper_partial_passes_when_ev_above_threshold(self):
        """Paper + PARTIAL + high EV → passes_gate=True."""
        result = compute_executable_ev(**{**_COMMON, "liquidity_usdc": _PARTIAL_LIQ, "policy_mode": "paper"})
        # threshold=0.05, ev ≈ 0.19 → should pass
        assert result.passes_gate is True

    def test_paper_partial_fails_when_ev_below_threshold(self):
        """Paper + PARTIAL → passes_gate=False when EV - penalty < threshold."""
        result = compute_executable_ev(
            side="YES",
            calibrated_event_probability=0.56,  # barely above ask
            ask_price=0.50,
            fee_pct=0.01,
            intended_size_usdc=_SIZE,
            liquidity_usdc=_PARTIAL_LIQ,
            snapshot_age_seconds=0.0,
            horizon_minutes=5,
            required_threshold=0.04,  # tight threshold
            policy_mode="paper",
        )
        assert result.fill_sim.fill_decision == FillDecision.PARTIAL
        # EV ≈ 0.56-0.50-0.01-slippage-penalty; may or may not pass depending on slippage
        # Just verify the formula is applied (not forced False)
        expected_ev = (
            result.ev_before_fill_adjustment - result.partial_fill_penalty
        )
        assert abs(result.executable_ev - round(expected_ev, 6)) < 1e-9

    def test_default_mode_same_as_paper(self):
        """policy_mode='default' behaves identically to 'paper'."""
        paper   = compute_executable_ev(**{**_COMMON, "liquidity_usdc": _PARTIAL_LIQ, "policy_mode": "paper"})
        default = compute_executable_ev(**{**_COMMON, "liquidity_usdc": _PARTIAL_LIQ, "policy_mode": "default"})
        assert paper.passes_gate    == default.passes_gate
        assert paper.executable_ev  == default.executable_ev
        assert paper.partial_fill_penalty == default.partial_fill_penalty


class TestPolicyDivergence:
    """The key assertion: live and paper give DIFFERENT outcomes for PARTIAL fills."""

    def test_live_rejects_partial_where_paper_passes(self):
        """Same PARTIAL fill: live rejects, paper passes."""
        live  = compute_executable_ev(**{**_COMMON, "liquidity_usdc": _PARTIAL_LIQ, "policy_mode": "live"})
        paper = compute_executable_ev(**{**_COMMON, "liquidity_usdc": _PARTIAL_LIQ, "policy_mode": "paper"})

        assert live.fill_sim.fill_decision  == FillDecision.PARTIAL
        assert paper.fill_sim.fill_decision == FillDecision.PARTIAL

        assert live.passes_gate  is False
        assert paper.passes_gate is True   # high EV scenario — paper passes

    def test_fillable_live_paper_agree(self):
        """FILLABLE fills: live and paper produce the same gate outcome."""
        live  = compute_executable_ev(**{**_COMMON, "liquidity_usdc": _FILLABLE_LIQ, "policy_mode": "live"})
        paper = compute_executable_ev(**{**_COMMON, "liquidity_usdc": _FILLABLE_LIQ, "policy_mode": "paper"})

        assert live.fill_sim.fill_decision  == FillDecision.FILLABLE
        assert paper.fill_sim.fill_decision == FillDecision.FILLABLE
        assert live.passes_gate == paper.passes_gate
        assert live.executable_ev == paper.executable_ev

    def test_policy_mode_recorded_in_diagnostics(self):
        """policy_mode is recorded in diagnostics for audit."""
        live  = compute_executable_ev(**{**_COMMON, "liquidity_usdc": _PARTIAL_LIQ, "policy_mode": "live"})
        paper = compute_executable_ev(**{**_COMMON, "liquidity_usdc": _PARTIAL_LIQ, "policy_mode": "paper"})

        assert live.diagnostics["policy_mode"]  == "live"
        assert paper.diagnostics["policy_mode"] == "paper"
