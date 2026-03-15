"""
calibration/tests/test_guardrails.py

Comprehensive guardrail contract tests for the calibration decision layer.

Covers:
- bridge_intent_side guard (step 8)
- horizon guard (step 4)
- live mode quality guards (steps 1-3)
- Guard ordering (earlier steps win)
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone, timedelta

import pytest

from calibration.decision_policy import decide
from calibration.probability_mapper import map_to_event_probability
from calibration.types import (
    CalibrationConfig,
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _raw(predicted_class="UP", confidence=0.72, horizon=15):
    return RawSignalOutput(
        asset="BTC",
        horizon_minutes=horizon,
        timestamp_utc=_NOW,
        predicted_class=predicted_class,
        raw_confidence=confidence,
        class_probabilities={"UP": confidence, "DOWN": 0.18, "NO_TRADE": 0.10},
    )


def _raw_no_class_probs(predicted_class="UP", confidence=0.72, horizon=15):
    """RawSignalOutput without class_probabilities."""
    return RawSignalOutput(
        asset="BTC",
        horizon_minutes=horizon,
        timestamp_utc=_NOW,
        predicted_class=predicted_class,
        raw_confidence=confidence,
        class_probabilities=None,
    )


def _pricing(ask_yes=0.44, bid_yes=0.42, ask_no=0.57, bid_no=0.55, liquidity=5000.0):
    return MarketPricingSnapshot(
        market_id="mkt-001",
        ask_yes=ask_yes,
        bid_yes=bid_yes,
        ask_no=ask_no,
        bid_no=bid_no,
        liquidity=liquidity,
        spread_yes=round(ask_yes - bid_yes, 6),
        spread_no=round(ask_no - bid_no, 6),
        timestamp_utc=_NOW,
    )


def _cal(
    up=0.72,
    down=0.18,
    no_trade=0.10,
    polarity="NORMAL",
    quality=CalibrationQuality.STRONG,
    horizon=15,
):
    r = _raw(horizon=horizon)
    cal, err = map_to_event_probability(
        raw=r,
        calibrated_up_prob=up,
        calibrated_down_prob=down,
        calibrated_no_trade_prob=no_trade,
        polarity=polarity,
        calibration_method=CalibrationMethod.PLATT,
        calibration_quality=quality,
    )
    assert err is None, f"map_to_event_probability failed: {err}"
    return cal


def _cal_no_class_probs(
    up=0.72,
    down=0.18,
    no_trade=0.10,
    polarity="NORMAL",
    quality=CalibrationQuality.UNKNOWN,
    horizon=15,
):
    """CalibratedSignal from a RawSignalOutput without class_probabilities."""
    r = _raw_no_class_probs(horizon=horizon)
    cal, err = map_to_event_probability(
        raw=r,
        calibrated_up_prob=up,
        calibrated_down_prob=down,
        calibrated_no_trade_prob=no_trade,
        polarity=polarity,
        calibration_method=CalibrationMethod.IDENTITY,
        calibration_quality=quality,
    )
    assert err is None, f"map_to_event_probability failed: {err}"
    return cal


# ── TestBridgeIntentGuard ─────────────────────────────────────────────────────

class TestBridgeIntentGuard:
    """
    Phase 10: bridge_intent_side is now validated at CalibratedSignal construction.
    dataclasses.replace() triggers __post_init__ → invalid values raise ValueError
    before reaching decide(). The guard is now at the object boundary, not decide().
    """

    def test_maybe_rejected(self):
        """Phase 10: 'MAYBE' raises ValueError at construction, not at decide()."""
        cal = _cal()
        with pytest.raises(ValueError, match="bridge_intent_side"):
            dataclasses.replace(cal, bridge_intent_side="MAYBE")

    def test_empty_string_rejected(self):
        """Phase 10: empty string raises ValueError at construction."""
        cal = _cal()
        with pytest.raises(ValueError, match="bridge_intent_side"):
            dataclasses.replace(cal, bridge_intent_side="")

    def test_lowercase_yes_rejected(self):
        """Phase 10: 'yes' (lowercase) raises ValueError at construction."""
        cal = _cal()
        with pytest.raises(ValueError, match="bridge_intent_side"):
            dataclasses.replace(cal, bridge_intent_side="yes")

    def test_lowercase_no_rejected(self):
        """Phase 10: 'no' (lowercase) raises ValueError at construction."""
        cal = _cal()
        with pytest.raises(ValueError, match="bridge_intent_side"):
            dataclasses.replace(cal, bridge_intent_side="no")

    def test_uppercase_yes_passes_bridge_guard(self):
        """'YES' is valid — should not reject at bridge guard step."""
        cal = _cal()
        assert cal.bridge_intent_side == "YES"
        result = decide(cal, _pricing(), PAPER_CAL_CONFIG, now_utc=_NOW)
        # Should not reject due to AMBIGUOUS_MAPPING
        assert result.rejection_reason != CalibrationRejectionReason.AMBIGUOUS_MAPPING

    def test_uppercase_no_passes_bridge_guard(self):
        """'NO' is valid — should not reject at bridge guard step."""
        cal = _cal(polarity="INVERTED")  # INVERTED+UP → NO
        assert cal.bridge_intent_side == "NO"
        result = decide(cal, _pricing(), PAPER_CAL_CONFIG, now_utc=_NOW)
        assert result.rejection_reason != CalibrationRejectionReason.AMBIGUOUS_MAPPING


# ── TestHorizonGuard ──────────────────────────────────────────────────────────

class TestHorizonGuard:
    """Step 4: horizon_minutes must be in SUPPORTED_HORIZONS = {5, 15}."""

    def _decide_horizon(self, horizon: int, config=None):
        if config is None:
            config = PAPER_CAL_CONFIG
        cal = _cal(horizon=horizon)
        return decide(cal, _pricing(), config, now_utc=_NOW)

    def test_horizon_1_rejected(self):
        result = self._decide_horizon(1)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.UNSUPPORTED_HORIZON

    def test_horizon_10_rejected(self):
        result = self._decide_horizon(10)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.UNSUPPORTED_HORIZON

    def test_horizon_30_rejected(self):
        result = self._decide_horizon(30)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.UNSUPPORTED_HORIZON

    def test_horizon_60_rejected(self):
        result = self._decide_horizon(60)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.UNSUPPORTED_HORIZON

    def test_horizon_120_rejected(self):
        result = self._decide_horizon(120)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.UNSUPPORTED_HORIZON

    def test_horizon_5_passes(self):
        result = self._decide_horizon(5)
        # Must not reject with UNSUPPORTED_HORIZON
        assert result.rejection_reason != CalibrationRejectionReason.UNSUPPORTED_HORIZON

    def test_horizon_15_passes(self):
        result = self._decide_horizon(15)
        assert result.rejection_reason != CalibrationRejectionReason.UNSUPPORTED_HORIZON


# ── TestLiveModeGuard ─────────────────────────────────────────────────────────

class TestLiveModeGuard:
    """Steps 1-3: LIVE rejects UNKNOWN/WEAK quality and missing class_probs."""

    def test_unknown_cal_rejected_in_live(self):
        cal = _cal(quality=CalibrationQuality.UNKNOWN)
        result = decide(cal, _pricing(), LIVE_CAL_CONFIG, now_utc=_NOW, intended_size_usdc=20.0)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.UNKNOWN_CALIBRATION

    def test_unknown_cal_passes_in_paper(self):
        cal = _cal(quality=CalibrationQuality.UNKNOWN)
        result = decide(cal, _pricing(), PAPER_CAL_CONFIG, now_utc=_NOW)
        assert result.rejection_reason != CalibrationRejectionReason.UNKNOWN_CALIBRATION

    def test_weak_cal_rejected_in_live(self):
        cal = _cal(quality=CalibrationQuality.WEAK)
        result = decide(cal, _pricing(), LIVE_CAL_CONFIG, now_utc=_NOW, intended_size_usdc=20.0)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.WEAK_CALIBRATION

    def test_weak_cal_passes_in_paper(self):
        cal = _cal(quality=CalibrationQuality.WEAK)
        result = decide(cal, _pricing(), PAPER_CAL_CONFIG, now_utc=_NOW)
        assert result.rejection_reason != CalibrationRejectionReason.WEAK_CALIBRATION

    def test_no_class_probs_rejected_in_live(self):
        cal = _cal_no_class_probs()
        result = decide(cal, _pricing(), LIVE_CAL_CONFIG, now_utc=_NOW, intended_size_usdc=20.0)
        assert result.decision == TradeDecisionType.REJECT
        # Step 2 fires first (UNKNOWN quality), reason is UNKNOWN_CALIBRATION
        assert result.rejection_reason == CalibrationRejectionReason.UNKNOWN_CALIBRATION

    def test_no_class_probs_strong_quality_rejected_in_live_step3(self):
        """
        Strong quality but no class_probs: step 1 and 2 pass (quality=STRONG),
        step 3 fires (require_class_probabilities=True).
        """
        cal = _cal_no_class_probs(quality=CalibrationQuality.STRONG)
        result = decide(cal, _pricing(), LIVE_CAL_CONFIG, now_utc=_NOW, intended_size_usdc=20.0)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.UNKNOWN_CALIBRATION

    def test_no_class_probs_passes_in_paper(self):
        cal = _cal_no_class_probs(quality=CalibrationQuality.UNKNOWN)
        result = decide(cal, _pricing(), PAPER_CAL_CONFIG, now_utc=_NOW)
        # Should not reject on class_probs or calibration quality in paper
        assert result.rejection_reason not in (
            CalibrationRejectionReason.UNKNOWN_CALIBRATION,
            CalibrationRejectionReason.WEAK_CALIBRATION,
        )


# ── TestGuardOrdering ─────────────────────────────────────────────────────────

class TestGuardOrdering:
    """Earlier steps must win when multiple guard conditions are met simultaneously."""

    def test_class_probs_check_before_horizon(self):
        """
        Setup: no class_probs AND unsupported horizon.
        Live mode: step 3 (class_probs) fires before step 4 (horizon).
        Expected: UNKNOWN_CALIBRATION (not UNSUPPORTED_HORIZON).
        """
        # Build a CalibratedSignal with unsupported horizon=30 and no class_probs
        # We need STRONG quality so steps 1 and 2 pass; step 3 fires.
        cal = _cal_no_class_probs(quality=CalibrationQuality.STRONG, horizon=30)
        result = decide(cal, _pricing(), LIVE_CAL_CONFIG, now_utc=_NOW, intended_size_usdc=20.0)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.UNKNOWN_CALIBRATION, (
            f"Expected UNKNOWN_CALIBRATION (step 3 wins), got {result.rejection_reason}"
        )

    def test_horizon_check_before_pricing_validation(self):
        """
        Setup: unsupported horizon AND structurally invalid pricing (ask < bid).
        Expected: UNSUPPORTED_HORIZON (step 4) wins over INVALID_PRICING (step 5).
        """
        cal = _cal(horizon=30)
        # Invalid pricing: ask_yes < bid_yes
        bad_pricing = MarketPricingSnapshot(
            market_id="mkt-bad",
            ask_yes=0.40,
            bid_yes=0.45,  # bid > ask → structural error
            ask_no=0.57,
            bid_no=0.55,
            liquidity=5000.0,
            spread_yes=-0.05,
            spread_no=0.02,
            timestamp_utc=_NOW,
        )
        result = decide(cal, bad_pricing, PAPER_CAL_CONFIG, now_utc=_NOW)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.UNSUPPORTED_HORIZON, (
            f"Expected UNSUPPORTED_HORIZON (step 4 wins), got {result.rejection_reason}"
        )

    def test_weak_cal_before_horizon_in_live(self):
        """
        Step 1 (WEAK) fires before step 4 (horizon) in live mode.
        """
        cal = _cal(quality=CalibrationQuality.WEAK, horizon=30)
        result = decide(cal, _pricing(), LIVE_CAL_CONFIG, now_utc=_NOW, intended_size_usdc=20.0)
        assert result.rejection_reason == CalibrationRejectionReason.WEAK_CALIBRATION

    def test_unknown_cal_before_class_probs_in_live(self):
        """
        Step 2 (UNKNOWN quality) fires before step 3 (class_probs) in live mode.
        UNKNOWN quality + no class_probs: step 2 wins.
        """
        cal = _cal_no_class_probs(quality=CalibrationQuality.UNKNOWN)
        result = decide(cal, _pricing(), LIVE_CAL_CONFIG, now_utc=_NOW, intended_size_usdc=20.0)
        assert result.rejection_reason == CalibrationRejectionReason.UNKNOWN_CALIBRATION
