"""
calibration/tests/test_live_vs_paper_policy.py

Systematic live vs paper policy comparison.
Same signal + same pricing + different config → verify expected outcomes.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

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

def _raw(confidence=0.72, horizon=15, with_class_probs=True):
    class_probs = (
        {"UP": confidence, "DOWN": round(1 - confidence - 0.10, 6), "NO_TRADE": 0.10}
        if with_class_probs
        else None
    )
    return RawSignalOutput(
        asset="BTC",
        horizon_minutes=horizon,
        timestamp_utc=_NOW,
        predicted_class="UP",
        raw_confidence=confidence,
        class_probabilities=class_probs,
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
    quality=CalibrationQuality.STRONG,
    horizon=15,
    with_class_probs=True,
):
    r = _raw(confidence=up, horizon=horizon, with_class_probs=with_class_probs)
    method = (
        CalibrationMethod.PLATT
        if quality == CalibrationQuality.STRONG
        else CalibrationMethod.IDENTITY
    )
    cal, err = map_to_event_probability(
        raw=r,
        calibrated_up_prob=up,
        calibrated_down_prob=down,
        calibrated_no_trade_prob=no_trade,
        polarity="NORMAL",
        calibration_method=method,
        calibration_quality=quality,
    )
    assert err is None, f"map_to_event_probability failed: {err}"
    return cal


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestBaselineStrongQuality:
    """Both live and paper should execute on a strong-quality signal with clear edge."""

    def test_strong_quality_live_executes(self):
        cal = _cal(quality=CalibrationQuality.STRONG)
        result = decide(cal, _pricing(), LIVE_CAL_CONFIG, now_utc=_NOW, intended_size_usdc=20.0)
        assert result.decision == TradeDecisionType.EXECUTE_YES

    def test_strong_quality_paper_executes(self):
        cal = _cal(quality=CalibrationQuality.STRONG)
        result = decide(cal, _pricing(), PAPER_CAL_CONFIG, now_utc=_NOW)
        assert result.decision == TradeDecisionType.EXECUTE_YES


class TestUnknownQualityDifference:
    """UNKNOWN quality: paper passes, live rejects UNKNOWN_CALIBRATION."""

    def test_unknown_quality_paper_executes(self):
        cal = _cal(quality=CalibrationQuality.UNKNOWN)
        result = decide(cal, _pricing(), PAPER_CAL_CONFIG, now_utc=_NOW)
        # Paper allows UNKNOWN — should not reject on calibration quality
        assert result.rejection_reason != CalibrationRejectionReason.UNKNOWN_CALIBRATION
        assert result.rejection_reason != CalibrationRejectionReason.WEAK_CALIBRATION

    def test_unknown_quality_live_rejects(self):
        cal = _cal(quality=CalibrationQuality.UNKNOWN)
        result = decide(cal, _pricing(), LIVE_CAL_CONFIG, now_utc=_NOW, intended_size_usdc=20.0)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.UNKNOWN_CALIBRATION


class TestWeakQualityDifference:
    """WEAK quality: paper passes, live rejects WEAK_CALIBRATION."""

    def test_weak_quality_paper_executes(self):
        cal = _cal(quality=CalibrationQuality.WEAK)
        result = decide(cal, _pricing(), PAPER_CAL_CONFIG, now_utc=_NOW)
        assert result.rejection_reason != CalibrationRejectionReason.WEAK_CALIBRATION

    def test_weak_quality_live_rejects(self):
        cal = _cal(quality=CalibrationQuality.WEAK)
        result = decide(cal, _pricing(), LIVE_CAL_CONFIG, now_utc=_NOW, intended_size_usdc=20.0)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.WEAK_CALIBRATION


class TestNoClassProbabilitiesDifference:
    """Missing class_probabilities: paper passes, live rejects."""

    def test_no_class_probs_paper_passes(self):
        cal = _cal(quality=CalibrationQuality.UNKNOWN, with_class_probs=False)
        result = decide(cal, _pricing(), PAPER_CAL_CONFIG, now_utc=_NOW)
        # Should not reject on class_probs in paper
        assert result.rejection_reason not in (
            CalibrationRejectionReason.UNKNOWN_CALIBRATION,
            CalibrationRejectionReason.WEAK_CALIBRATION,
        )

    def test_no_class_probs_live_rejects(self):
        cal = _cal(quality=CalibrationQuality.UNKNOWN, with_class_probs=False)
        result = decide(cal, _pricing(), LIVE_CAL_CONFIG, now_utc=_NOW, intended_size_usdc=20.0)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.UNKNOWN_CALIBRATION

    def test_no_class_probs_strong_quality_live_rejects_step3(self):
        """
        STRONG quality (steps 1,2 pass) but no class_probs → live rejects at step 3.
        """
        cal = _cal(quality=CalibrationQuality.STRONG, with_class_probs=False)
        result = decide(cal, _pricing(), LIVE_CAL_CONFIG, now_utc=_NOW, intended_size_usdc=20.0)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.UNKNOWN_CALIBRATION


class TestEdgeThresholdDifference:
    """
    Edge threshold: paper=0.02, live=0.03.

    executable_ev = calibrated_prob - ask_yes - total_friction
    With realistic path (liquidity=5000, size=20):
        fee          = 0.010
        slippage     = 0.014  (base=0.003 + liq_penalty=0.010 + size_penalty=0.001)
        total_friction = 0.024
        executable_ev  = prob - ask - 0.024
    """

    def _cal_and_pricing_for_edge(self, calibrated_prob: float, ask_yes: float):
        """Build cal + pricing where exec_adj ≈ calibrated_prob - ask_yes - 0.015."""
        up = calibrated_prob
        down = round(1.0 - up - 0.10, 6)
        no_trade = 0.10

        r = RawSignalOutput(
            asset="BTC",
            horizon_minutes=15,
            timestamp_utc=_NOW,
            predicted_class="UP",
            raw_confidence=up,
            class_probabilities={"UP": up, "DOWN": down, "NO_TRADE": no_trade},
        )
        cal, err = map_to_event_probability(
            raw=r,
            calibrated_up_prob=up,
            calibrated_down_prob=down,
            calibrated_no_trade_prob=no_trade,
            polarity="NORMAL",
            calibration_method=CalibrationMethod.PLATT,
            calibration_quality=CalibrationQuality.STRONG,
        )
        assert err is None

        bid_yes = round(ask_yes - 0.02, 6)
        ask_no = round(1.0 - bid_yes, 6)
        bid_no = round(ask_no - 0.02, 6)
        pricing = MarketPricingSnapshot(
            market_id="mkt-edge",
            ask_yes=ask_yes,
            bid_yes=bid_yes,
            ask_no=ask_no,
            bid_no=bid_no,
            liquidity=5000.0,
            spread_yes=0.02,
            spread_no=0.02,
            timestamp_utc=_NOW,
        )
        return cal, pricing

    def test_edge_between_paper_and_live_thresholds(self):
        """
        executable_ev between 0.020 and 0.030 → paper passes, live rejects.

        With realistic execution (liquidity=5000, size=20):
            total_friction = fee(0.010) + slippage(0.014) = 0.024
            executable_ev  = theoretical - total_friction = (p - ask) - 0.024

        prob=0.65, ask=0.60:
            theoretical    = 0.65 - 0.60 = 0.050
            executable_ev  = 0.050 - 0.024 = 0.026
            paper min=0.02: 0.026 >= 0.02 → PASS
            live  min=0.03: 0.026 <  0.03 → REJECT
        """
        cal, pricing = self._cal_and_pricing_for_edge(
            calibrated_prob=0.65, ask_yes=0.60
        )
        paper_result = decide(cal, pricing, PAPER_CAL_CONFIG, now_utc=_NOW)
        live_result = decide(cal, pricing, LIVE_CAL_CONFIG, now_utc=_NOW, intended_size_usdc=20.0)

        assert paper_result.rejection_reason != CalibrationRejectionReason.NEGATIVE_EDGE, (
            f"Paper should pass exec_adj≈0.025 >= 0.02, got: {paper_result.rejection_reason}"
        )
        assert live_result.decision == TradeDecisionType.REJECT
        assert live_result.rejection_reason == CalibrationRejectionReason.NEGATIVE_EDGE, (
            f"Live should reject exec_adj≈0.025 < 0.03, got: {live_result.rejection_reason}"
        )

    def test_edge_020_live_rejects(self):
        """exec_adj ≈ 0.020 → live (min=0.03) rejects NEGATIVE_EDGE.

        prob=0.59, ask=0.555:
            exec_adj = 0.590 - 0.555 - 0.01 - 0.005 ≈ 0.020 (float: 0.0199...)
        This is below both thresholds due to float arithmetic, so both reject.
        Test just verifies live rejects with NEGATIVE_EDGE.
        """
        cal, pricing = self._cal_and_pricing_for_edge(
            calibrated_prob=0.59, ask_yes=0.555
        )
        result = decide(cal, pricing, LIVE_CAL_CONFIG, now_utc=_NOW, intended_size_usdc=20.0)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.NEGATIVE_EDGE

    def test_edge_030_both_pass(self):
        """exec_adj > 0.030 → both paper and live pass edge gate."""
        # exec_adj = prob - ask - 0.015 → use 0.61 - 0.555 = 0.055 - 0.015 = 0.040
        # Avoid exact boundary; use a value clearly above both thresholds.
        cal, pricing = self._cal_and_pricing_for_edge(
            calibrated_prob=0.61, ask_yes=0.555
        )
        paper_result = decide(cal, pricing, PAPER_CAL_CONFIG, now_utc=_NOW)
        live_result = decide(cal, pricing, LIVE_CAL_CONFIG, now_utc=_NOW, intended_size_usdc=20.0)
        assert paper_result.rejection_reason != CalibrationRejectionReason.NEGATIVE_EDGE
        assert live_result.rejection_reason != CalibrationRejectionReason.NEGATIVE_EDGE

    def test_custom_config_override(self):
        """CalibrationConfig with custom min_execution_adjusted_edge=0.025."""
        custom_config = CalibrationConfig(
            reject_on_unknown_calibration=False,
            reject_on_weak_calibration=False,
            min_execution_adjusted_edge=0.025,
        )
        # exec_adj = 0.590 - 0.555 - 0.015 = 0.020 < 0.025 → reject
        cal, pricing = self._cal_and_pricing_for_edge(
            calibrated_prob=0.59, ask_yes=0.555
        )
        result = decide(cal, pricing, custom_config, now_utc=_NOW)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.NEGATIVE_EDGE
