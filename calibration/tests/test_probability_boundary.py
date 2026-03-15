"""
calibration/tests/test_probability_boundary.py

Phase 10: Probability boundary enforcement at all layers.

Covers:
1. Negative probability → ValueError at RawSignalOutput or REJECT at decide()
2. Probability > 1 → REJECT (INCONSISTENT_PROBS)
3. Under-summed live distribution (< 0.90) → REJECT
4. Under-summed paper distribution (>= 0.50, < 0.90) → passes boundary
5. Missing class_probabilities in live → REJECT
6. Malformed class dict keys → reject at mapper
7. Over-summed total (> 1.01) → REJECT
8. Total exactly at live threshold (0.90) → passes
9. Total just below live threshold (0.899) → REJECT
10. Negative individual calibrated prob → REJECT at decide()
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from calibration.decision_policy import decide
from calibration.probability_mapper import map_to_event_probability
from calibration.types import (
    LIVE_CAL_CONFIG,
    PAPER_CAL_CONFIG,
    PROB_LIVE_MIN_SUM,
    PROB_MIN_SUM,
    CalibrationMethod,
    CalibrationQuality,
    CalibrationRejectionReason,
    MarketPricingSnapshot,
    RawSignalOutput,
    TradeDecisionType,
)

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _raw(
    up: float = 0.72,
    down: float = 0.18,
    no_trade: float = 0.10,
    horizon: int = 5,
    with_class_probs: bool = True,
    predicted_class: str = "UP",
) -> RawSignalOutput:
    return RawSignalOutput(
        asset="BTC",
        horizon_minutes=horizon,
        timestamp_utc=_NOW,
        predicted_class=predicted_class,
        raw_confidence=up,
        class_probabilities=(
            {"UP": up, "DOWN": down, "NO_TRADE": no_trade}
            if with_class_probs else None
        ),
    )


def _map(raw, up, down, no_trade, quality=CalibrationQuality.STRONG):
    return map_to_event_probability(
        raw=raw,
        calibrated_up_prob=up,
        calibrated_down_prob=down,
        calibrated_no_trade_prob=no_trade,
        polarity="NORMAL",
        calibration_method=CalibrationMethod.PLATT,
        calibration_quality=quality,
    )


def _pricing() -> MarketPricingSnapshot:
    return MarketPricingSnapshot(
        market_id="mkt-test",
        ask_yes=0.44, bid_yes=0.42,
        ask_no=0.57,  bid_no=0.55,
        liquidity=5000.0,
        timestamp_utc=_NOW,
    )


# ── 1. Negative probabilities ─────────────────────────────────────────────────

class TestNegativeProbabilities:

    def test_negative_up_prob_rejected_by_mapper(self):
        """Negatif up prob → mapper INCONSISTENT_PROBS döner."""
        raw = _raw(up=0.50, down=0.40, no_trade=0.10)
        cal, err = _map(raw, up=-0.10, down=0.40, no_trade=0.10)
        assert cal is None
        assert err == CalibrationRejectionReason.INCONSISTENT_PROBS

    def test_negative_down_prob_rejected_by_mapper(self):
        raw = _raw(up=0.50, down=0.40, no_trade=0.10)
        cal, err = _map(raw, up=0.50, down=-0.10, no_trade=0.10)
        assert cal is None
        assert err == CalibrationRejectionReason.INCONSISTENT_PROBS

    def test_negative_no_trade_prob_rejected_by_mapper(self):
        raw = _raw(up=0.50, down=0.40, no_trade=0.10)
        cal, err = _map(raw, up=0.50, down=0.40, no_trade=-0.05)
        assert cal is None
        assert err == CalibrationRejectionReason.INCONSISTENT_PROBS

    def test_negative_prob_in_class_dict_rejected_at_decide(self):
        """
        Mapper kendisi valid kabul etti (p_up=0.72, p_down=0.18, p_nt=0.10) ama
        CalibratedSignal.calibrated_up_prob negatif yapılırsa decide() step 4 yakalar.
        """
        import dataclasses
        raw = _raw()
        cal, err = _map(raw, up=0.72, down=0.18, no_trade=0.10)
        assert err is None
        # Negatif calibrated_up_prob ile tampered signal
        bad = dataclasses.replace(cal, calibrated_up_prob=-0.05)
        result = decide(bad, _pricing(), config=PAPER_CAL_CONFIG, now_utc=_NOW)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.INCONSISTENT_PROBS


# ── 2. Probability > 1 ────────────────────────────────────────────────────────

class TestProbabilityAboveOne:

    def test_up_prob_above_1_rejected_by_mapper(self):
        raw = _raw()
        cal, err = _map(raw, up=1.20, down=0.10, no_trade=0.10)
        assert cal is None
        assert err == CalibrationRejectionReason.INCONSISTENT_PROBS

    def test_over_sum_rejected_by_mapper(self):
        """Toplam > 1.01 → mapper INCONSISTENT_PROBS."""
        raw = _raw()
        cal, err = _map(raw, up=0.80, down=0.80, no_trade=0.10)
        assert cal is None
        assert err == CalibrationRejectionReason.INCONSISTENT_PROBS

    def test_over_sum_rejected_at_decide_step4(self):
        """
        Mapper geçti ama tampered sum 1.02 → decide() step 4 yakalar.
        """
        import dataclasses
        raw = _raw()
        cal, err = _map(raw, up=0.72, down=0.18, no_trade=0.10)
        assert err is None
        bad = dataclasses.replace(cal,
                                  calibrated_up_prob=0.90,
                                  calibrated_down_prob=0.72,  # sum=1.72 > 1.01
                                  calibrated_no_trade_prob=0.10)
        result = decide(bad, _pricing(), config=PAPER_CAL_CONFIG, now_utc=_NOW)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.INCONSISTENT_PROBS


# ── 3. Under-summed distributions ─────────────────────────────────────────────

class TestUnderSummedDistributions:

    def test_live_under_sum_060_rejects(self):
        """Kanıt #3: Live modda toplam=0.60 < 0.90 → INCONSISTENT_PROBS."""
        raw = _raw(up=0.50, down=0.10, no_trade=0.00)  # total=0.60
        cal, err = _map(raw, up=0.50, down=0.10, no_trade=0.00)
        assert err is None  # mapper 0.50 eşiğini geçer (PROB_MIN_SUM=0.50)
        result = decide(cal, _pricing(), config=LIVE_CAL_CONFIG, now_utc=_NOW,
                        intended_size_usdc=20.0)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.INCONSISTENT_PROBS
        assert "0.60" in result.rationale or "boundary" in result.rationale.lower()

    def test_live_under_sum_072_rejects(self):
        """Live modda toplam=0.72 < 0.90 → REJECT."""
        raw = _raw(up=0.60, down=0.12, no_trade=0.00)  # total=0.72
        cal, err = _map(raw, up=0.60, down=0.12, no_trade=0.00)
        assert err is None
        result = decide(cal, _pricing(), config=LIVE_CAL_CONFIG, now_utc=_NOW,
                        intended_size_usdc=20.0)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.INCONSISTENT_PROBS

    def test_live_sum_exactly_090_passes(self):
        """Live modda toplam tam 0.90 → boundary geçer."""
        raw = _raw(up=0.80, down=0.10, no_trade=0.00)  # total=0.90
        cal, err = _map(raw, up=0.80, down=0.10, no_trade=0.00)
        assert err is None
        result = decide(cal, _pricing(), config=LIVE_CAL_CONFIG, now_utc=_NOW,
                        intended_size_usdc=20.0)
        assert result.rejection_reason != CalibrationRejectionReason.INCONSISTENT_PROBS

    def test_live_sum_0899_rejects(self):
        """Live modda toplam 0.899 < 0.90 → REJECT."""
        raw = _raw(up=0.80, down=0.099, no_trade=0.00)  # total=0.899
        cal, err = _map(raw, up=0.800, down=0.099, no_trade=0.000)
        assert err is None
        result = decide(cal, _pricing(), config=LIVE_CAL_CONFIG, now_utc=_NOW,
                        intended_size_usdc=20.0)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.INCONSISTENT_PROBS

    def test_paper_sum_072_passes_boundary(self):
        """Paper modda toplam=0.72 ≥ 0.50 → boundary geçer (daha düşük eşik)."""
        raw = _raw(up=0.60, down=0.12, no_trade=0.00)
        cal, err = _map(raw, up=0.60, down=0.12, no_trade=0.00)
        assert err is None
        result = decide(cal, _pricing(), config=PAPER_CAL_CONFIG, now_utc=_NOW)
        assert result.rejection_reason != CalibrationRejectionReason.INCONSISTENT_PROBS

    def test_paper_sum_051_passes_boundary(self):
        """Paper modda toplam=0.51 ≥ 0.50 → boundary geçer."""
        raw = _raw(up=0.50, down=0.01, no_trade=0.00)  # total=0.51
        cal, err = _map(raw, up=0.50, down=0.01, no_trade=0.00)
        assert err is None
        result = decide(cal, _pricing(), config=PAPER_CAL_CONFIG, now_utc=_NOW)
        assert result.rejection_reason != CalibrationRejectionReason.INCONSISTENT_PROBS

    def test_paper_sum_below_050_rejects(self):
        """Paper modda toplam < 0.50 → mapper REJECT (PROB_MIN_SUM=0.50)."""
        raw = _raw(up=0.20, down=0.10, no_trade=0.05)  # total=0.35
        cal, err = _map(raw, up=0.20, down=0.10, no_trade=0.05)
        assert cal is None
        assert err == CalibrationRejectionReason.INCONSISTENT_PROBS


# ── 4. Missing class_probabilities in live ────────────────────────────────────

class TestMissingClassProbsLive:

    def test_live_no_class_probs_strong_quality_rejects(self):
        """Live: class_probabilities=None, STRONG quality → REJECT (require=True in live)."""
        raw = _raw(with_class_probs=False)
        cal, err = _map(raw, up=0.72, down=0.18, no_trade=0.10,
                        quality=CalibrationQuality.STRONG)
        assert err is None
        result = decide(cal, _pricing(), config=LIVE_CAL_CONFIG, now_utc=_NOW,
                        intended_size_usdc=20.0)
        assert result.decision == TradeDecisionType.REJECT

    def test_paper_no_class_probs_accepted(self):
        """Paper: class_probabilities=None → accepted (require_class_probabilities=False)."""
        raw = _raw(with_class_probs=False)
        cal, err = _map(raw, up=0.72, down=0.18, no_trade=0.10,
                        quality=CalibrationQuality.UNKNOWN)
        assert err is None
        result = decide(cal, _pricing(), config=PAPER_CAL_CONFIG, now_utc=_NOW)
        assert result.rejection_reason != CalibrationRejectionReason.UNKNOWN_CALIBRATION


# ── 5. Mapper-level class key validation ──────────────────────────────────────

class TestMapperClassKeyValidation:

    def test_no_trade_class_is_rejected(self):
        """predicted_class=NO_TRADE → mapper REJECT (not INCONSISTENT_PROBS)."""
        raw = _raw(predicted_class="NO_TRADE", up=0.10, down=0.80, no_trade=0.10)
        # Rebuild with NO_TRADE predicted_class
        raw2 = RawSignalOutput(
            asset="BTC", horizon_minutes=5, timestamp_utc=_NOW,
            predicted_class="NO_TRADE", raw_confidence=0.80,
            class_probabilities={"UP": 0.10, "DOWN": 0.80, "NO_TRADE": 0.10},
        )
        cal, err = _map(raw2, up=0.10, down=0.80, no_trade=0.10)
        assert cal is None
        assert err == CalibrationRejectionReason.NO_TRADE_SIGNAL

    def test_invalid_predicted_class_raises_at_construction(self):
        """Phase 10: predicted_class='SIDEWAYS' → ValueError at RawSignalOutput()."""
        with pytest.raises(ValueError, match="predicted_class"):
            RawSignalOutput(
                asset="BTC", horizon_minutes=5, timestamp_utc=_NOW,
                predicted_class="SIDEWAYS", raw_confidence=0.72,
            )
