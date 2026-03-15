"""
calibration/tests/test_live_vs_paper_fill_behavior.py

Phase 11 Option C: Live vs paper fill behavior at the decide() level.

Key assertions:
1. Live mode: PARTIAL fill → REJECT with PARTIAL_FILL_REJECTED
2. Paper mode: PARTIAL fill → may EXECUTE (EV penalty applied but trade can pass)
3. FILLABLE: live and paper agree
4. Rejection reason for live partial is exactly PARTIAL_FILL_REJECTED (not NEGATIVE_EDGE)
5. Audit trail fields (executable_notional_usdc, fill_fraction) populated on partial rejection
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

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _raw(confidence=0.72):
    return RawSignalOutput(
        asset="BTC",
        horizon_minutes=5,
        timestamp_utc=_NOW,
        predicted_class="UP",
        raw_confidence=confidence,
        class_probabilities={"UP": confidence, "DOWN": round((1-confidence)*0.6, 6),
                              "NO_TRADE": round((1-confidence)*0.4, 6)},
    )


def _cal(confidence=0.72):
    down = round((1 - confidence) * 0.6, 6)
    nt   = round((1 - confidence) * 0.4, 6)
    raw  = _raw(confidence)
    cal, err = map_to_event_probability(
        raw=raw,
        calibrated_up_prob=confidence,
        calibrated_down_prob=down,
        calibrated_no_trade_prob=nt,
        polarity="NORMAL",
        calibration_method=CalibrationMethod.PLATT,
        calibration_quality=CalibrationQuality.STRONG,
    )
    assert err is None, f"Unexpected mapper error: {err}"
    return cal


def _pricing_partial(ask_yes=0.44, bid_yes=0.42, ask_no=0.57, bid_no=0.55):
    """Pricing with LOW liquidity to trigger PARTIAL fill for small sizes."""
    # PARTIAL: size/liq ∈ (0.25, 0.50]
    # liquidity must exceed min_liquidity=1000 to pass step 8.
    # With size=400, liq=1200 → ratio≈0.333 → PARTIAL ✓
    return MarketPricingSnapshot(
        market_id="mkt-partial",
        ask_yes=ask_yes,
        bid_yes=bid_yes,
        ask_no=ask_no,
        bid_no=bid_no,
        liquidity=1200.0,  # 400/1200 ≈ 0.333 → PARTIAL with _PARTIAL_SIZE=400
        timestamp_utc=_NOW,
    )


def _pricing_fillable(ask_yes=0.44, bid_yes=0.42, ask_no=0.57, bid_no=0.55):
    """Pricing with ample liquidity to guarantee FILLABLE fill."""
    return MarketPricingSnapshot(
        market_id="mkt-fillable",
        ask_yes=ask_yes,
        bid_yes=bid_yes,
        ask_no=ask_no,
        bid_no=bid_no,
        liquidity=5000.0,
        timestamp_utc=_NOW,
    )


# With liq=1200, size must be in (300, 600] for PARTIAL. Use 400.
_PARTIAL_SIZE = 400.0


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestLivePartialFillRejection:

    def test_live_partial_fill_rejects_with_correct_reason(self):
        """Live mode + PARTIAL fill → REJECT with PARTIAL_FILL_REJECTED."""
        cal     = _cal()
        pricing = _pricing_partial()
        result  = decide(cal, pricing, config=LIVE_CAL_CONFIG, now_utc=_NOW,
                         intended_size_usdc=_PARTIAL_SIZE)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.PARTIAL_FILL_REJECTED

    def test_live_partial_rejection_not_negative_edge(self):
        """PARTIAL_FILL_REJECTED is distinct from NEGATIVE_EDGE — EV may be positive."""
        cal     = _cal(confidence=0.80)  # very strong signal
        pricing = _pricing_partial()
        result  = decide(cal, pricing, config=LIVE_CAL_CONFIG, now_utc=_NOW,
                         intended_size_usdc=_PARTIAL_SIZE)
        assert result.rejection_reason == CalibrationRejectionReason.PARTIAL_FILL_REJECTED
        assert result.rejection_reason != CalibrationRejectionReason.NEGATIVE_EDGE

    def test_live_partial_rejection_rationale_mentions_fill_fraction(self):
        """Rejection rationale must describe the fill outcome."""
        cal     = _cal()
        pricing = _pricing_partial()
        result  = decide(cal, pricing, config=LIVE_CAL_CONFIG, now_utc=_NOW,
                         intended_size_usdc=_PARTIAL_SIZE)
        assert result.rejection_reason == CalibrationRejectionReason.PARTIAL_FILL_REJECTED
        assert "partial" in result.rationale.lower() or "fill" in result.rationale.lower()

    def test_live_fillable_executes(self):
        """Live mode + FILLABLE fill → EXECUTE_YES (fills pass in live)."""
        cal     = _cal()
        pricing = _pricing_fillable()
        result  = decide(cal, pricing, config=LIVE_CAL_CONFIG, now_utc=_NOW,
                         intended_size_usdc=20.0)
        assert result.decision == TradeDecisionType.EXECUTE_YES


class TestPaperPartialFillBehavior:

    def test_paper_partial_fill_may_execute(self):
        """Paper mode + PARTIAL fill + high edge → EXECUTE_YES (penalty applied, but passes)."""
        cal     = _cal(confidence=0.80)
        pricing = _pricing_partial()
        result  = decide(cal, pricing, config=PAPER_CAL_CONFIG, now_utc=_NOW,
                         intended_size_usdc=_PARTIAL_SIZE)
        # Paper mode allows partial fills — should not reject with PARTIAL_FILL_REJECTED
        assert result.rejection_reason != CalibrationRejectionReason.PARTIAL_FILL_REJECTED

    def test_paper_partial_never_gets_partial_fill_rejected(self):
        """PARTIAL_FILL_REJECTED must never appear in paper mode."""
        cal     = _cal()
        pricing = _pricing_partial()
        result  = decide(cal, pricing, config=PAPER_CAL_CONFIG, now_utc=_NOW,
                         intended_size_usdc=_PARTIAL_SIZE)
        assert result.rejection_reason != CalibrationRejectionReason.PARTIAL_FILL_REJECTED

    def test_paper_fillable_executes(self):
        """Paper mode + FILLABLE → EXECUTE_YES."""
        cal     = _cal()
        pricing = _pricing_fillable()
        result  = decide(cal, pricing, config=PAPER_CAL_CONFIG, now_utc=_NOW)
        assert result.decision == TradeDecisionType.EXECUTE_YES


class TestPartialFillAuditInDecide:

    def test_live_partial_rejection_has_fill_fraction(self):
        """Live partial rejection must expose fill_fraction in TradeDecision."""
        cal     = _cal()
        pricing = _pricing_partial()
        result  = decide(cal, pricing, config=LIVE_CAL_CONFIG, now_utc=_NOW,
                         intended_size_usdc=_PARTIAL_SIZE)
        assert result.rejection_reason == CalibrationRejectionReason.PARTIAL_FILL_REJECTED
        assert result.fill_fraction is not None
        assert 0.0 < result.fill_fraction < 1.0  # PARTIAL: between 0 and 1

    def test_live_partial_rejection_has_executable_notional(self):
        """Live partial rejection must expose executable_notional_usdc."""
        cal     = _cal()
        pricing = _pricing_partial()
        result  = decide(cal, pricing, config=LIVE_CAL_CONFIG, now_utc=_NOW,
                         intended_size_usdc=_PARTIAL_SIZE)
        assert result.rejection_reason == CalibrationRejectionReason.PARTIAL_FILL_REJECTED
        assert result.executable_notional_usdc is not None
        # executable_notional < intended_size (fill_fraction < 1)
        assert result.executable_notional_usdc < _PARTIAL_SIZE

    def test_live_partial_notional_equals_fraction_times_size(self):
        """executable_notional_usdc == fill_fraction × intended_size."""
        cal     = _cal()
        pricing = _pricing_partial()
        result  = decide(cal, pricing, config=LIVE_CAL_CONFIG, now_utc=_NOW,
                         intended_size_usdc=_PARTIAL_SIZE)
        assert result.rejection_reason == CalibrationRejectionReason.PARTIAL_FILL_REJECTED
        expected_notional = result.fill_fraction * _PARTIAL_SIZE
        assert result.executable_notional_usdc == pytest.approx(expected_notional, abs=1e-5)

    def test_execute_has_fill_fraction_and_notional(self):
        """EXECUTE_YES TradeDecision includes fill_fraction and executable_notional."""
        cal     = _cal()
        pricing = _pricing_fillable()
        result  = decide(cal, pricing, config=PAPER_CAL_CONFIG, now_utc=_NOW)
        assert result.decision == TradeDecisionType.EXECUTE_YES
        assert result.fill_fraction is not None
        assert result.executable_notional_usdc is not None


class TestRejectAuditTrailFix:
    """Phase 11 also fixes _reject() steps 1-9 audit trail (policy_mode + size)."""

    def test_early_reject_has_correct_policy_mode(self):
        """Steps 1-9 rejections must carry policy_mode=config.mode."""
        # Trigger step 1 (WEAK calibration rejection in live mode)
        raw = RawSignalOutput(
            asset="BTC", horizon_minutes=5, timestamp_utc=_NOW,
            predicted_class="UP", raw_confidence=0.72,
            class_probabilities={"UP": 0.72, "DOWN": 0.18, "NO_TRADE": 0.10},
        )
        from calibration.probability_mapper import map_to_event_probability
        from calibration.types import CalibrationMethod, CalibrationQuality
        cal, err = map_to_event_probability(
            raw=raw,
            calibrated_up_prob=0.72,
            calibrated_down_prob=0.18,
            calibrated_no_trade_prob=0.10,
            polarity="NORMAL",
            calibration_method=CalibrationMethod.IDENTITY,
            calibration_quality=CalibrationQuality.WEAK,
        )
        assert err is None
        pricing = _pricing_fillable()
        result = decide(cal, pricing, config=LIVE_CAL_CONFIG, now_utc=_NOW,
                        intended_size_usdc=50.0)
        # Live config rejects WEAK calibration at step 1
        assert result.rejection_reason == CalibrationRejectionReason.WEAK_CALIBRATION
        # _reject() fix: must carry correct mode and size
        assert result.policy_mode == "live"
        assert result.intended_size_usdc_used == pytest.approx(50.0)

    def test_early_reject_size_not_default_20(self):
        """Steps 1-9 rejections must NOT default to 20.0 when a different size was passed."""
        raw = RawSignalOutput(
            asset="BTC", horizon_minutes=5, timestamp_utc=_NOW,
            predicted_class="UP", raw_confidence=0.72,
            class_probabilities={"UP": 0.72, "DOWN": 0.18, "NO_TRADE": 0.10},
        )
        from calibration.probability_mapper import map_to_event_probability
        from calibration.types import CalibrationMethod, CalibrationQuality, PAPER_CAL_CONFIG
        cal, err = map_to_event_probability(
            raw=raw,
            calibrated_up_prob=0.72,
            calibrated_down_prob=0.18,
            calibrated_no_trade_prob=0.10,
            polarity="NORMAL",
            calibration_method=CalibrationMethod.PLATT,
            calibration_quality=CalibrationQuality.STRONG,
        )
        assert err is None
        # Trigger UNSUPPORTED_HORIZON by patching horizon to unsupported value
        # We do this by using a horizon that survives RawSignalOutput (no validation there)
        # and then manually constructing a CalibratedSignal with unsupported horizon
        # Easier: just test with WEAK cal in paper mode where it passes, then use LOW_LIQUIDITY
        from calibration.types import CalibrationConfig
        low_liq_config = CalibrationConfig(mode="paper", min_liquidity=100_000.0)
        pricing = _pricing_fillable()  # liquidity=5000, below 100_000 → LOW_LIQUIDITY
        result = decide(cal, pricing, config=low_liq_config, now_utc=_NOW,
                        intended_size_usdc=99.0)
        assert result.rejection_reason == CalibrationRejectionReason.LOW_LIQUIDITY
        assert result.policy_mode == "paper"
        assert result.intended_size_usdc_used == pytest.approx(99.0)
