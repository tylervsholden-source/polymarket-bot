"""
calibration/tests/test_partial_fill_audit.py

Phase 11: Audit trail completeness for partial fill scenarios.

Proves that every relevant TradeDecision path carries:
  - intended_size_usdc_used  (pre-existing Phase 10 field)
  - policy_mode              (pre-existing Phase 10 field, now fixed for steps 1-9)
  - fill_fraction            (Phase 11)
  - executable_notional_usdc (Phase 11)

And that ExecutableCostBreakdown carries:
  - fill_fraction
  - executable_notional_usdc
  - ev_before_fill_adjustment
  - partial_fill_penalty
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from calibration.decision_policy import decide
from calibration.probability_mapper import map_to_event_probability
from calibration.types import (
    CalibrationMethod,
    CalibrationQuality,
    CalibrationRejectionReason,
    LIVE_CAL_CONFIG,
    PAPER_CAL_CONFIG,
    MarketPricingSnapshot,
    RawSignalOutput,
    TradeDecisionType,
)
from execution_realism.core import compute_executable_ev
from execution_realism.types import FillDecision

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)

_FILLABLE_LIQ   = 420.0
_PARTIAL_LIQ    = 290.0


def _cal(confidence=0.72):
    raw = RawSignalOutput(
        asset="BTC", horizon_minutes=5, timestamp_utc=_NOW,
        predicted_class="UP", raw_confidence=confidence,
        class_probabilities={"UP": confidence, "DOWN": 0.18, "NO_TRADE": 0.10},
    )
    cal, err = map_to_event_probability(
        raw=raw,
        calibrated_up_prob=confidence,
        calibrated_down_prob=0.18,
        calibrated_no_trade_prob=0.10,
        polarity="NORMAL",
        calibration_method=CalibrationMethod.PLATT,
        calibration_quality=CalibrationQuality.STRONG,
    )
    assert err is None
    return cal


def _pricing(liquidity=5000.0):
    return MarketPricingSnapshot(
        market_id="mkt", ask_yes=0.44, bid_yes=0.42,
        ask_no=0.57, bid_no=0.55,
        liquidity=liquidity, timestamp_utc=_NOW,
    )

def _pricing_partial():
    # size=400, liq=1200 → ratio≈0.333 → PARTIAL; liq>1000 passes step 8
    return _pricing(liquidity=1200.0)

# intended_size_usdc for partial tests: 400/1200≈0.333 → PARTIAL
_PARTIAL_SIZE = 400.0

def _pricing_fillable():
    return _pricing(liquidity=5000.0)


# ── ExecutableCostBreakdown audit fields ───────────────────────────────────────

class TestBreakdownAuditFields:

    def test_breakdown_has_fill_fraction_fillable(self):
        result = compute_executable_ev(
            side="YES", calibrated_event_probability=0.70, ask_price=0.50,
            fee_pct=0.01, intended_size_usdc=100.0, liquidity_usdc=_FILLABLE_LIQ,
            snapshot_age_seconds=0.0, horizon_minutes=5, required_threshold=0.05,
        )
        assert hasattr(result, "fill_fraction")
        assert result.fill_fraction == pytest.approx(1.0)

    def test_breakdown_has_fill_fraction_partial(self):
        result = compute_executable_ev(
            side="YES", calibrated_event_probability=0.70, ask_price=0.50,
            fee_pct=0.01, intended_size_usdc=100.0, liquidity_usdc=_PARTIAL_LIQ,
            snapshot_age_seconds=0.0, horizon_minutes=5, required_threshold=0.05,
        )
        assert hasattr(result, "fill_fraction")
        assert result.fill_fraction == pytest.approx(0.90)

    def test_breakdown_has_executable_notional_fillable(self):
        result = compute_executable_ev(
            side="YES", calibrated_event_probability=0.70, ask_price=0.50,
            fee_pct=0.01, intended_size_usdc=100.0, liquidity_usdc=_FILLABLE_LIQ,
            snapshot_age_seconds=0.0, horizon_minutes=5, required_threshold=0.05,
        )
        assert result.executable_notional_usdc == pytest.approx(100.0)

    def test_breakdown_has_executable_notional_partial(self):
        result = compute_executable_ev(
            side="YES", calibrated_event_probability=0.70, ask_price=0.50,
            fee_pct=0.01, intended_size_usdc=100.0, liquidity_usdc=_PARTIAL_LIQ,
            snapshot_age_seconds=0.0, horizon_minutes=5, required_threshold=0.05,
        )
        assert result.executable_notional_usdc == pytest.approx(90.0)

    def test_breakdown_has_ev_before_fill_partial(self):
        result = compute_executable_ev(
            side="YES", calibrated_event_probability=0.70, ask_price=0.50,
            fee_pct=0.01, intended_size_usdc=100.0, liquidity_usdc=_PARTIAL_LIQ,
            snapshot_age_seconds=0.0, horizon_minutes=5, required_threshold=0.05,
        )
        assert hasattr(result, "ev_before_fill_adjustment")
        assert result.ev_before_fill_adjustment > result.executable_ev

    def test_breakdown_has_partial_fill_penalty_field(self):
        result = compute_executable_ev(
            side="YES", calibrated_event_probability=0.70, ask_price=0.50,
            fee_pct=0.01, intended_size_usdc=100.0, liquidity_usdc=_PARTIAL_LIQ,
            snapshot_age_seconds=0.0, horizon_minutes=5, required_threshold=0.05,
        )
        assert hasattr(result, "partial_fill_penalty")
        assert result.partial_fill_penalty > 0.0


# ── TradeDecision audit fields ─────────────────────────────────────────────────

class TestTradeDecisionAuditFields:

    def test_execute_fillable_has_fill_fraction_one(self):
        """EXECUTE with FILLABLE fill → fill_fraction=1.0 in TradeDecision."""
        cal     = _cal()
        pricing = _pricing_fillable()
        result  = decide(cal, pricing, config=PAPER_CAL_CONFIG, now_utc=_NOW)
        assert result.decision == TradeDecisionType.EXECUTE_YES
        assert result.fill_fraction is not None
        assert result.fill_fraction == pytest.approx(1.0)

    def test_execute_fillable_has_notional_equals_intended(self):
        """EXECUTE with FILLABLE → executable_notional_usdc == intended_size."""
        cal     = _cal()
        pricing = _pricing_fillable()
        # paper default: intended_size = 20.0
        result  = decide(cal, pricing, config=PAPER_CAL_CONFIG, now_utc=_NOW)
        assert result.decision == TradeDecisionType.EXECUTE_YES
        assert result.executable_notional_usdc is not None
        assert result.executable_notional_usdc == pytest.approx(20.0, abs=1e-5)

    def test_execute_fillable_intended_size_used(self):
        """EXECUTE: intended_size_usdc_used = the passed size."""
        cal     = _cal()
        pricing = _pricing_fillable()
        result  = decide(cal, pricing, config=LIVE_CAL_CONFIG, now_utc=_NOW,
                         intended_size_usdc=75.0)
        assert result.decision == TradeDecisionType.EXECUTE_YES
        assert result.intended_size_usdc_used == pytest.approx(75.0)

    def test_execute_policy_mode_matches_config(self):
        """EXECUTE: policy_mode == config.mode."""
        cal     = _cal()
        pricing = _pricing_fillable()
        live_result  = decide(cal, pricing, config=LIVE_CAL_CONFIG, now_utc=_NOW,
                              intended_size_usdc=20.0)
        paper_result = decide(cal, pricing, config=PAPER_CAL_CONFIG, now_utc=_NOW)
        assert live_result.policy_mode  == "live"
        assert paper_result.policy_mode == "paper"

    def test_partial_rejection_audit_has_fill_fraction(self):
        """PARTIAL_FILL_REJECTED → fill_fraction < 1.0 in TradeDecision."""
        cal     = _cal()
        pricing = _pricing_partial()
        result  = decide(cal, pricing, config=LIVE_CAL_CONFIG, now_utc=_NOW,
                         intended_size_usdc=_PARTIAL_SIZE)
        assert result.rejection_reason == CalibrationRejectionReason.PARTIAL_FILL_REJECTED
        assert result.fill_fraction is not None
        assert result.fill_fraction < 1.0

    def test_partial_rejection_audit_has_executable_notional(self):
        """PARTIAL_FILL_REJECTED → executable_notional_usdc < intended in TradeDecision."""
        cal     = _cal()
        pricing = _pricing_partial()
        result  = decide(cal, pricing, config=LIVE_CAL_CONFIG, now_utc=_NOW,
                         intended_size_usdc=_PARTIAL_SIZE)
        assert result.rejection_reason == CalibrationRejectionReason.PARTIAL_FILL_REJECTED
        assert result.executable_notional_usdc is not None
        assert result.executable_notional_usdc < _PARTIAL_SIZE

    def test_partial_rejection_edge_estimate_present(self):
        """PARTIAL_FILL_REJECTED → edge_estimate populated (computed before rejection)."""
        cal     = _cal()
        pricing = _pricing_partial()
        result  = decide(cal, pricing, config=LIVE_CAL_CONFIG, now_utc=_NOW,
                         intended_size_usdc=_PARTIAL_SIZE)
        assert result.rejection_reason == CalibrationRejectionReason.PARTIAL_FILL_REJECTED
        assert result.edge_estimate is not None

    def test_partial_rejection_breakdown_present(self):
        """PARTIAL_FILL_REJECTED → executable_cost_breakdown populated."""
        cal     = _cal()
        pricing = _pricing_partial()
        result  = decide(cal, pricing, config=LIVE_CAL_CONFIG, now_utc=_NOW,
                         intended_size_usdc=_PARTIAL_SIZE)
        assert result.rejection_reason == CalibrationRejectionReason.PARTIAL_FILL_REJECTED
        assert result.executable_cost_breakdown is not None

    def test_passes_final_gate_false_on_partial_rejection(self):
        """PARTIAL_FILL_REJECTED → passes_final_gate=False."""
        cal     = _cal()
        pricing = _pricing_partial()
        result  = decide(cal, pricing, config=LIVE_CAL_CONFIG, now_utc=_NOW,
                         intended_size_usdc=_PARTIAL_SIZE)
        assert result.passes_final_gate is False

    def test_theoretical_hold_ev_present_on_partial_rejection(self):
        """PARTIAL_FILL_REJECTED → theoretical_hold_ev is populated."""
        cal     = _cal()
        pricing = _pricing_partial()
        result  = decide(cal, pricing, config=LIVE_CAL_CONFIG, now_utc=_NOW,
                         intended_size_usdc=_PARTIAL_SIZE)
        assert result.rejection_reason == CalibrationRejectionReason.PARTIAL_FILL_REJECTED
        assert result.theoretical_hold_ev is not None
