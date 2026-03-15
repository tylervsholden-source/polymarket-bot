"""
calibration/tests/test_probability_contracts.py

Tests for map_to_event_probability probability validity contracts.

Covers: PROB_MIN_SUM, PROB_MAX_OVER, individual bounds, class_probs via decide().
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
    PROB_MAX_OVER,
    PROB_MIN_SUM,
    MarketPricingSnapshot,
    RawSignalOutput,
)

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _raw(horizon=15):
    return RawSignalOutput(
        asset="BTC",
        horizon_minutes=horizon,
        timestamp_utc=_NOW,
        predicted_class="UP",
        raw_confidence=0.72,
        class_probabilities={"UP": 0.72, "DOWN": 0.18, "NO_TRADE": 0.10},
    )


def _pricing():
    return MarketPricingSnapshot(
        market_id="mkt-001",
        ask_yes=0.44,
        bid_yes=0.42,
        ask_no=0.57,
        bid_no=0.55,
        liquidity=5000.0,
        spread_yes=0.02,
        spread_no=0.02,
        timestamp_utc=_NOW,
    )


def _map(up, down, no_trade, polarity="NORMAL", quality=CalibrationQuality.STRONG):
    """Convenience: call map_to_event_probability and return (cal, err)."""
    r = _raw()
    return map_to_event_probability(
        raw=r,
        calibrated_up_prob=up,
        calibrated_down_prob=down,
        calibrated_no_trade_prob=no_trade,
        polarity=polarity,
        calibration_method=CalibrationMethod.PLATT,
        calibration_quality=quality,
    )


# ── Individual probability bounds ─────────────────────────────────────────────

class TestIndividualProbBounds:

    def test_negative_up_prob(self):
        cal, err = _map(up=-0.01, down=0.18, no_trade=0.10)
        assert cal is None
        assert err == CalibrationRejectionReason.INCONSISTENT_PROBS

    def test_up_prob_greater_than_one(self):
        cal, err = _map(up=1.05, down=0.10, no_trade=0.05)
        assert cal is None
        assert err == CalibrationRejectionReason.INCONSISTENT_PROBS

    def test_negative_down_prob(self):
        cal, err = _map(up=0.72, down=-0.05, no_trade=0.10)
        assert cal is None
        assert err == CalibrationRejectionReason.INCONSISTENT_PROBS

    def test_negative_no_trade_prob(self):
        cal, err = _map(up=0.72, down=0.18, no_trade=-0.10)
        assert cal is None
        assert err == CalibrationRejectionReason.INCONSISTENT_PROBS

    def test_no_trade_greater_than_one(self):
        cal, err = _map(up=0.10, down=0.05, no_trade=1.20)
        assert cal is None
        assert err == CalibrationRejectionReason.INCONSISTENT_PROBS


# ── Sum rules ─────────────────────────────────────────────────────────────────

class TestProbSumRules:

    def test_sum_150_rejected(self):
        """0.72 + 0.60 + 0.18 = 1.50 > 1 + PROB_MAX_OVER."""
        cal, err = _map(up=0.72, down=0.60, no_trade=0.18)
        assert cal is None
        assert err == CalibrationRejectionReason.INCONSISTENT_PROBS

    def test_sum_049_below_min_sum(self):
        """0.30 + 0.10 + 0.09 = 0.49 < PROB_MIN_SUM=0.50."""
        cal, err = _map(up=0.30, down=0.10, no_trade=0.09)
        assert cal is None
        assert err == CalibrationRejectionReason.INCONSISTENT_PROBS

    def test_sum_050_exactly_passes(self):
        """
        Sum at PROB_MIN_SUM floor: use 0.50 exactly representable in float.
        0.25 + 0.15 + 0.10 = 0.50 exactly (no float rounding issues).
        Should pass the probability check.
        """
        cal, err = _map(up=0.25, down=0.15, no_trade=0.10)
        # If it fails, it should NOT be due to INCONSISTENT_PROBS
        if err is not None:
            assert err != CalibrationRejectionReason.INCONSISTENT_PROBS
        # If it passes, cal should be set
        if err is None:
            assert cal is not None

    def test_sum_101_at_tolerance_boundary_passes(self):
        """
        0.72 + 0.18 + 0.11 = 1.01 == 1 + PROB_MAX_OVER.
        Exactly at boundary — should pass (not > 1 + PROB_MAX_OVER).
        """
        cal, err = _map(up=0.72, down=0.18, no_trade=0.11)
        if err is not None:
            assert err != CalibrationRejectionReason.INCONSISTENT_PROBS
        if err is None:
            assert cal is not None

    def test_sum_102_just_over_tolerance_rejected(self):
        """0.72 + 0.19 + 0.11 = 1.02 > 1 + PROB_MAX_OVER=1.01."""
        cal, err = _map(up=0.72, down=0.19, no_trade=0.11)
        assert cal is None
        assert err == CalibrationRejectionReason.INCONSISTENT_PROBS

    def test_partial_sum_075_passes(self):
        """
        up=0.50, down=0.20, no_trade=0.05 → sum=0.75 ≥ PROB_MIN_SUM=0.50.
        This represents a model that uses partial probability mass; acceptable.
        """
        cal, err = _map(up=0.50, down=0.20, no_trade=0.05)
        if err is not None:
            assert err != CalibrationRejectionReason.INCONSISTENT_PROBS
        if err is None:
            assert cal is not None

    def test_prob_min_sum_constant_value(self):
        """PROB_MIN_SUM must be 0.50 per spec."""
        assert PROB_MIN_SUM == 0.50

    def test_prob_max_over_constant_value(self):
        """PROB_MAX_OVER must be 0.01 per spec."""
        assert PROB_MAX_OVER == 0.01


# ── class_probabilities integration with decide() ─────────────────────────────

class TestClassProbsIntegrationWithDecide:
    """Tests that require_class_probabilities interacts correctly with live/paper."""

    def _raw_no_class_probs(self):
        return RawSignalOutput(
            asset="BTC",
            horizon_minutes=15,
            timestamp_utc=_NOW,
            predicted_class="UP",
            raw_confidence=0.72,
            class_probabilities=None,
        )

    def test_live_mode_missing_class_probs_unknown_calibration(self):
        """
        Live mode: class_probs=None + quality=UNKNOWN.
        Steps 1,2 reject at UNKNOWN_CALIBRATION before step 3.
        """
        r = self._raw_no_class_probs()
        cal, err = map_to_event_probability(
            raw=r,
            calibrated_up_prob=0.72,
            calibrated_down_prob=0.18,
            calibrated_no_trade_prob=0.10,
            polarity="NORMAL",
            calibration_method=CalibrationMethod.IDENTITY,
            calibration_quality=CalibrationQuality.UNKNOWN,
        )
        assert err is None
        assert cal is not None

        result = decide(cal, _pricing(), LIVE_CAL_CONFIG, now_utc=_NOW, intended_size_usdc=20.0)
        assert result.rejection_reason == CalibrationRejectionReason.UNKNOWN_CALIBRATION

    def test_live_mode_missing_class_probs_strong_quality_step3(self):
        """
        Live mode: class_probs=None + quality=STRONG → step 3 fires.
        """
        r = self._raw_no_class_probs()
        cal, err = map_to_event_probability(
            raw=r,
            calibrated_up_prob=0.72,
            calibrated_down_prob=0.18,
            calibrated_no_trade_prob=0.10,
            polarity="NORMAL",
            calibration_method=CalibrationMethod.PLATT,
            calibration_quality=CalibrationQuality.STRONG,
        )
        assert err is None

        result = decide(cal, _pricing(), LIVE_CAL_CONFIG, now_utc=_NOW, intended_size_usdc=20.0)
        assert result.rejection_reason == CalibrationRejectionReason.UNKNOWN_CALIBRATION

    def test_paper_mode_missing_class_probs_executes(self):
        """
        Paper mode: class_probs=None is OK — no rejection from calibration gates.
        """
        r = self._raw_no_class_probs()
        cal, err = map_to_event_probability(
            raw=r,
            calibrated_up_prob=0.72,
            calibrated_down_prob=0.18,
            calibrated_no_trade_prob=0.10,
            polarity="NORMAL",
            calibration_method=CalibrationMethod.IDENTITY,
            calibration_quality=CalibrationQuality.UNKNOWN,
        )
        assert err is None

        result = decide(cal, _pricing(), PAPER_CAL_CONFIG, now_utc=_NOW)
        assert result.rejection_reason not in (
            CalibrationRejectionReason.UNKNOWN_CALIBRATION,
            CalibrationRejectionReason.WEAK_CALIBRATION,
        )
