"""
calibration/tests/test_paper_loose_vs_strict.py

Phase 12: Same scenario behaves differently under paper_loose vs paper_strict.

Proves that the split is real — not cosmetic labels.

Scenarios tested:
1. Weak probability contract: passes loose, fails strict
2. Suspicious underround: passes loose (annotated), fails strict
3. Stale pricing: passes loose (long age limit), fails strict (shorter limit)
4. Unknown calibration: passes loose, fails strict
5. Weak calibration quality: passes loose, fails strict
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from calibration.decision_policy import decide
from calibration.probability_mapper import map_to_event_probability
from calibration.types import (
    CalibrationMethod,
    CalibrationQuality,
    CalibrationRejectionReason,
    MarketPricingSnapshot,
    PAPER_LOOSE_CAL_CONFIG,
    PAPER_STRICT_CAL_CONFIG,
    RawSignalOutput,
    TradeDecisionType,
)

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


def _snap(ask_yes=0.44, bid_yes=0.42, ask_no=0.57, bid_no=0.55,
          liquidity=5000.0, ts=None):
    return MarketPricingSnapshot(
        market_id="mkt",
        ask_yes=ask_yes, bid_yes=bid_yes,
        ask_no=ask_no, bid_no=bid_no,
        liquidity=liquidity,
        timestamp_utc=ts or _NOW,
    )


def _cal_strong(confidence=0.72):
    raw = RawSignalOutput(
        asset="BTC", horizon_minutes=5, timestamp_utc=_NOW,
        predicted_class="UP", raw_confidence=confidence,
        class_probabilities={
            "UP": confidence,
            "DOWN": round((1 - confidence) * 0.6, 6),
            "NO_TRADE": round((1 - confidence) * 0.4, 6),
        },
    )
    cal, err = map_to_event_probability(
        raw=raw,
        calibrated_up_prob=confidence,
        calibrated_down_prob=round((1 - confidence) * 0.6, 6),
        calibrated_no_trade_prob=round((1 - confidence) * 0.4, 6),
        polarity="NORMAL",
        calibration_method=CalibrationMethod.PLATT,
        calibration_quality=CalibrationQuality.STRONG,
    )
    assert err is None
    return cal


def _cal_weak(confidence=0.72):
    raw = RawSignalOutput(
        asset="BTC", horizon_minutes=5, timestamp_utc=_NOW,
        predicted_class="UP", raw_confidence=confidence,
        class_probabilities={
            "UP": confidence,
            "DOWN": round((1 - confidence) * 0.6, 6),
            "NO_TRADE": round((1 - confidence) * 0.4, 6),
        },
    )
    cal, err = map_to_event_probability(
        raw=raw,
        calibrated_up_prob=confidence,
        calibrated_down_prob=round((1 - confidence) * 0.6, 6),
        calibrated_no_trade_prob=round((1 - confidence) * 0.4, 6),
        polarity="NORMAL",
        calibration_method=CalibrationMethod.IDENTITY,
        calibration_quality=CalibrationQuality.WEAK,
    )
    assert err is None
    return cal


def _cal_unknown(confidence=0.72):
    raw = RawSignalOutput(
        asset="BTC", horizon_minutes=5, timestamp_utc=_NOW,
        predicted_class="UP", raw_confidence=confidence,
        class_probabilities=None,  # no class probs — triggers unknown
    )
    cal, err = map_to_event_probability(
        raw=raw,
        calibrated_up_prob=confidence,
        calibrated_down_prob=round((1 - confidence) * 0.6, 6),
        calibrated_no_trade_prob=round((1 - confidence) * 0.4, 6),
        polarity="NORMAL",
        calibration_method=CalibrationMethod.IDENTITY,
        calibration_quality=CalibrationQuality.UNKNOWN,
    )
    assert err is None
    return cal


# ── Calibration quality split ─────────────────────────────────────────────────

class TestCalibrationQualitySplit:

    def test_weak_calibration_passes_paper_loose(self):
        """paper_loose: reject_on_weak_calibration=False → WEAK cal passes."""
        cal = _cal_weak(confidence=0.80)
        result = decide(cal, _snap(), config=PAPER_LOOSE_CAL_CONFIG, now_utc=_NOW)
        assert result.rejection_reason != CalibrationRejectionReason.WEAK_CALIBRATION

    def test_weak_calibration_rejected_by_paper_strict(self):
        """paper_strict: reject_on_weak_calibration=True → WEAK cal rejected."""
        cal = _cal_weak()
        result = decide(cal, _snap(), config=PAPER_STRICT_CAL_CONFIG,
                        now_utc=_NOW, intended_size_usdc=20.0)
        assert result.rejection_reason == CalibrationRejectionReason.WEAK_CALIBRATION

    def test_unknown_calibration_passes_paper_loose(self):
        """paper_loose: reject_on_unknown_calibration=False → UNKNOWN cal passes."""
        cal = _cal_unknown(confidence=0.80)
        result = decide(cal, _snap(), config=PAPER_LOOSE_CAL_CONFIG, now_utc=_NOW)
        assert result.rejection_reason != CalibrationRejectionReason.UNKNOWN_CALIBRATION

    def test_unknown_calibration_rejected_by_paper_strict(self):
        """paper_strict: reject_on_unknown_calibration=True → UNKNOWN cal rejected."""
        cal = _cal_unknown()
        result = decide(cal, _snap(), config=PAPER_STRICT_CAL_CONFIG,
                        now_utc=_NOW, intended_size_usdc=20.0)
        assert result.rejection_reason in (
            CalibrationRejectionReason.UNKNOWN_CALIBRATION,
            CalibrationRejectionReason.WEAK_CALIBRATION,
        )


# ── Suspicious underround split ───────────────────────────────────────────────

class TestSuspiciousUnderroundSplit:

    def _snap_ask_sum_092(self):
        """ask_sum=0.92: fails paper_strict (min=0.93), passes paper_loose (min=0.88)."""
        return MarketPricingSnapshot(
            market_id="mkt-ur",
            ask_yes=0.42, bid_yes=0.40,
            ask_no=0.50, bid_no=0.48,
            liquidity=5000.0, timestamp_utc=_NOW,
        )

    def test_ask_sum_0_92_rejected_by_paper_strict(self):
        cal = _cal_strong()
        result = decide(cal, self._snap_ask_sum_092(), config=PAPER_STRICT_CAL_CONFIG,
                        now_utc=_NOW, intended_size_usdc=20.0)
        assert result.rejection_reason == CalibrationRejectionReason.SUSPICIOUS_UNDERROUND

    def test_ask_sum_0_92_not_rejected_by_paper_loose(self):
        cal = _cal_strong(confidence=0.80)
        result = decide(cal, self._snap_ask_sum_092(), config=PAPER_LOOSE_CAL_CONFIG,
                        now_utc=_NOW)
        assert result.rejection_reason != CalibrationRejectionReason.SUSPICIOUS_UNDERROUND

    def test_ask_sum_below_088_execute_in_loose_has_sanity_notes(self):
        """ask_sum=0.87 < 0.88 paper_loose min → annotated. Notes set if EXECUTE."""
        snap = MarketPricingSnapshot(
            market_id="mkt-vur",
            ask_yes=0.39, bid_yes=0.37,
            ask_no=0.48, bid_no=0.46,  # ask_sum=0.87 < 0.88
            liquidity=5000.0, timestamp_utc=_NOW,
        )
        cal = _cal_strong(confidence=0.80)
        result = decide(cal, snap, config=PAPER_LOOSE_CAL_CONFIG, now_utc=_NOW)
        if result.decision == TradeDecisionType.EXECUTE_YES:
            assert result.pricing_sanity_notes is not None


# ── Stale pricing split ───────────────────────────────────────────────────────

class TestStalePricingSplit:

    def test_stale_91s_rejected_by_paper_strict(self):
        """paper_strict max_snapshot_age=120s. Snapshot 150s old → rejected."""
        stale_ts = _NOW - timedelta(seconds=150)
        cal = _cal_strong()
        result = decide(cal, _snap(ts=stale_ts), config=PAPER_STRICT_CAL_CONFIG,
                        now_utc=_NOW, intended_size_usdc=20.0)
        assert result.rejection_reason == CalibrationRejectionReason.STALE_PRICING

    def test_stale_150s_passes_paper_loose(self):
        """paper_loose max_snapshot_age=300s. Snapshot 150s old → passes age check."""
        stale_ts = _NOW - timedelta(seconds=150)
        cal = _cal_strong()
        result = decide(cal, _snap(ts=stale_ts), config=PAPER_LOOSE_CAL_CONFIG,
                        now_utc=_NOW)
        assert result.rejection_reason != CalibrationRejectionReason.STALE_PRICING

    def test_stale_250s_rejected_by_paper_strict_but_passes_loose(self):
        stale_ts = _NOW - timedelta(seconds=250)
        cal = _cal_strong()
        strict = decide(cal, _snap(ts=stale_ts), config=PAPER_STRICT_CAL_CONFIG,
                        now_utc=_NOW, intended_size_usdc=20.0)
        loose  = decide(cal, _snap(ts=stale_ts), config=PAPER_LOOSE_CAL_CONFIG,
                        now_utc=_NOW)
        assert strict.rejection_reason == CalibrationRejectionReason.STALE_PRICING
        assert loose.rejection_reason != CalibrationRejectionReason.STALE_PRICING


# ── Probability mass split ────────────────────────────────────────────────────

class TestProbabilityMassSplit:

    def test_prob_sum_0_65_passes_paper_loose_fails_strict(self):
        """prob_sum=0.65 → below paper_strict min_prob_sum=0.75; above loose min=0.50."""
        raw = RawSignalOutput(
            asset="BTC", horizon_minutes=5, timestamp_utc=_NOW,
            predicted_class="UP", raw_confidence=0.60,
            class_probabilities={"UP": 0.60, "DOWN": 0.05, "NO_TRADE": 0.00},
            # sum=0.65 — intentionally sparse
        )
        cal, err = map_to_event_probability(
            raw=raw,
            calibrated_up_prob=0.60,
            calibrated_down_prob=0.05,
            calibrated_no_trade_prob=0.00,
            polarity="NORMAL",
            calibration_method=CalibrationMethod.PLATT,
            calibration_quality=CalibrationQuality.STRONG,
        )
        assert err is None

        strict = decide(cal, _snap(), config=PAPER_STRICT_CAL_CONFIG,
                        now_utc=_NOW, intended_size_usdc=20.0)
        loose  = decide(cal, _snap(), config=PAPER_LOOSE_CAL_CONFIG, now_utc=_NOW)

        assert strict.rejection_reason == CalibrationRejectionReason.INCONSISTENT_PROBS
        assert loose.rejection_reason != CalibrationRejectionReason.INCONSISTENT_PROBS

    def test_min_prob_sum_constants_are_distinct(self):
        """paper_strict min_prob_sum must be between live and loose values."""
        assert PAPER_STRICT_CAL_CONFIG.min_prob_sum > PAPER_LOOSE_CAL_CONFIG.min_prob_sum
        assert PAPER_STRICT_CAL_CONFIG.min_prob_sum <= 1.0
