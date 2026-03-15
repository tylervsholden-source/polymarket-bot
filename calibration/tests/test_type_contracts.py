"""
calibration/tests/test_type_contracts.py

Phase 10: Type-level contract enforcement.

Proves that invalid objects:
  1. Cannot be silently constructed with bad values
  2. Fail deterministically with ValueError — not silently or at trade time

Covers:
  RawSignalOutput:
    - invalid predicted_class → ValueError
    - raw_confidence out of [0,1] → ValueError
    - valid values → no error

  CalibratedSignal:
    - invalid bridge_intent_side → ValueError
    - effective_yes_prob out of [0,1] → ValueError
    - effective_no_prob out of [0,1] → ValueError

  CalibrationConfig:
    - negative edge threshold → ValueError
    - confidence out of [0,1] → ValueError
    - negative fee → ValueError
    - fee >= 1 → ValueError
    - min_prob_sum out of [0,1] → ValueError

  DirectionalSignal:
    - unsupported horizon → ValueError (Layer 1)

  MarketPricingSnapshot:
    - spread_yes/no derived from ask-bid (cannot be spoofed)
"""
from __future__ import annotations

from datetime import datetime, timezone

import dataclasses
import pytest

from calibration.types import (
    CalibrationConfig,
    CalibrationMethod,
    CalibrationQuality,
    MarketPricingSnapshot,
    RawSignalOutput,
    SUPPORTED_HORIZONS,
)
from calibration.probability_mapper import map_to_event_probability
from signal_bridge.types import Direction, DirectionalSignal

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _valid_raw(**kwargs) -> RawSignalOutput:
    defaults = dict(
        asset="BTC", horizon_minutes=5, timestamp_utc=_NOW,
        predicted_class="UP", raw_confidence=0.72,
    )
    defaults.update(kwargs)
    return RawSignalOutput(**defaults)


def _valid_cal(up=0.72, down=0.18, no_trade=0.10):
    raw = _valid_raw()
    cal, err = map_to_event_probability(
        raw=raw,
        calibrated_up_prob=up,
        calibrated_down_prob=down,
        calibrated_no_trade_prob=no_trade,
        polarity="NORMAL",
        calibration_method=CalibrationMethod.PLATT,
        calibration_quality=CalibrationQuality.STRONG,
    )
    assert err is None
    return cal


# ── RawSignalOutput type contracts ────────────────────────────────────────────

class TestRawSignalOutputContracts:

    def test_invalid_predicted_class_raises(self):
        """predicted_class='SIDEWAYS' → ValueError at construction."""
        with pytest.raises(ValueError, match="predicted_class"):
            RawSignalOutput(
                asset="BTC", horizon_minutes=5, timestamp_utc=_NOW,
                predicted_class="SIDEWAYS", raw_confidence=0.72,
            )

    def test_empty_predicted_class_raises(self):
        with pytest.raises(ValueError, match="predicted_class"):
            RawSignalOutput(
                asset="BTC", horizon_minutes=5, timestamp_utc=_NOW,
                predicted_class="", raw_confidence=0.72,
            )

    def test_lowercase_predicted_class_raises(self):
        with pytest.raises(ValueError, match="predicted_class"):
            RawSignalOutput(
                asset="BTC", horizon_minutes=5, timestamp_utc=_NOW,
                predicted_class="up", raw_confidence=0.72,
            )

    def test_confidence_above_1_raises(self):
        with pytest.raises(ValueError, match="raw_confidence"):
            RawSignalOutput(
                asset="BTC", horizon_minutes=5, timestamp_utc=_NOW,
                predicted_class="UP", raw_confidence=1.50,
            )

    def test_confidence_negative_raises(self):
        with pytest.raises(ValueError, match="raw_confidence"):
            RawSignalOutput(
                asset="BTC", horizon_minutes=5, timestamp_utc=_NOW,
                predicted_class="UP", raw_confidence=-0.01,
            )

    def test_valid_up_accepted(self):
        raw = _valid_raw(predicted_class="UP", raw_confidence=0.72)
        assert raw.predicted_class == "UP"

    def test_valid_down_accepted(self):
        raw = _valid_raw(predicted_class="DOWN", raw_confidence=0.65)
        assert raw.predicted_class == "DOWN"

    def test_valid_no_trade_accepted(self):
        raw = _valid_raw(predicted_class="NO_TRADE", raw_confidence=0.40)
        assert raw.predicted_class == "NO_TRADE"

    def test_confidence_zero_accepted(self):
        raw = _valid_raw(raw_confidence=0.0)
        assert raw.raw_confidence == 0.0

    def test_confidence_one_accepted(self):
        raw = _valid_raw(raw_confidence=1.0)
        assert raw.raw_confidence == 1.0


# ── CalibratedSignal type contracts ───────────────────────────────────────────

class TestCalibratedSignalContracts:

    def test_invalid_bridge_intent_side_raises(self):
        """bridge_intent_side='MAYBE' → ValueError at construction (via replace)."""
        cal = _valid_cal()
        with pytest.raises(ValueError, match="bridge_intent_side"):
            dataclasses.replace(cal, bridge_intent_side="MAYBE")

    def test_empty_bridge_intent_side_raises(self):
        cal = _valid_cal()
        with pytest.raises(ValueError, match="bridge_intent_side"):
            dataclasses.replace(cal, bridge_intent_side="")

    def test_reject_bridge_intent_side_raises(self):
        """'REJECT' is not a valid bridge_intent_side (TradeSide.REJECT from bridge)."""
        cal = _valid_cal()
        with pytest.raises(ValueError, match="bridge_intent_side"):
            dataclasses.replace(cal, bridge_intent_side="REJECT")

    def test_effective_yes_prob_above_1_raises(self):
        cal = _valid_cal()
        with pytest.raises(ValueError, match="effective_yes_prob"):
            dataclasses.replace(cal, effective_yes_prob=1.50)

    def test_effective_yes_prob_negative_raises(self):
        cal = _valid_cal()
        with pytest.raises(ValueError, match="effective_yes_prob"):
            dataclasses.replace(cal, effective_yes_prob=-0.10)

    def test_effective_no_prob_above_1_raises(self):
        cal = _valid_cal()
        with pytest.raises(ValueError, match="effective_no_prob"):
            dataclasses.replace(cal, effective_no_prob=1.20)

    def test_valid_yes_side_accepted(self):
        cal = _valid_cal()
        assert cal.bridge_intent_side == "YES"

    def test_valid_no_side_accepted(self):
        raw = _valid_raw(predicted_class="DOWN", raw_confidence=0.65)
        cal, err = map_to_event_probability(
            raw=raw,
            calibrated_up_prob=0.20,
            calibrated_down_prob=0.65,
            calibrated_no_trade_prob=0.15,
            polarity="NORMAL",
            calibration_method=CalibrationMethod.PLATT,
            calibration_quality=CalibrationQuality.STRONG,
        )
        assert err is None
        assert cal.bridge_intent_side == "NO"


# ── CalibrationConfig type contracts ──────────────────────────────────────────

class TestCalibrationConfigContracts:

    def test_negative_edge_raises(self):
        with pytest.raises(ValueError, match="min_execution_adjusted_edge"):
            CalibrationConfig(min_execution_adjusted_edge=-0.01)

    def test_zero_edge_allowed(self):
        """0.0 edge threshold is technically valid (no minimum floor)."""
        cfg = CalibrationConfig(min_execution_adjusted_edge=0.0)
        assert cfg.min_execution_adjusted_edge == 0.0

    def test_confidence_above_1_raises(self):
        with pytest.raises(ValueError, match="min_calibrated_confidence"):
            CalibrationConfig(min_calibrated_confidence=1.50)

    def test_confidence_negative_raises(self):
        with pytest.raises(ValueError, match="min_calibrated_confidence"):
            CalibrationConfig(min_calibrated_confidence=-0.10)

    def test_fee_negative_raises(self):
        with pytest.raises(ValueError, match="assumed_taker_fee_pct"):
            CalibrationConfig(assumed_taker_fee_pct=-0.01)

    def test_fee_above_1_raises(self):
        with pytest.raises(ValueError, match="assumed_taker_fee_pct"):
            CalibrationConfig(assumed_taker_fee_pct=1.50)

    def test_fee_zero_allowed(self):
        """Zero fee is valid for testing."""
        cfg = CalibrationConfig(assumed_taker_fee_pct=0.0)
        assert cfg.assumed_taker_fee_pct == 0.0

    def test_min_prob_sum_above_1_raises(self):
        with pytest.raises(ValueError, match="min_prob_sum"):
            CalibrationConfig(min_prob_sum=1.50)

    def test_min_prob_sum_negative_raises(self):
        with pytest.raises(ValueError, match="min_prob_sum"):
            CalibrationConfig(min_prob_sum=-0.10)

    def test_valid_config_constructed(self):
        cfg = CalibrationConfig(
            min_execution_adjusted_edge=0.03,
            min_calibrated_confidence=0.60,
            assumed_taker_fee_pct=0.01,
            min_prob_sum=0.90,
            mode="live",
        )
        assert cfg.mode == "live"
        assert cfg.min_prob_sum == 0.90


# ── DirectionalSignal type contracts ──────────────────────────────────────────

class TestDirectionalSignalContracts:

    @pytest.mark.parametrize("h", [1, 10, 30, 60, 240])
    def test_unsupported_horizon_raises(self, h):
        with pytest.raises(ValueError, match="horizon_minutes"):
            DirectionalSignal(
                asset="BTC", horizon_minutes=h,
                timestamp_utc=_NOW, direction=Direction.UP, confidence=0.72,
            )

    def test_supported_5_accepted(self):
        sig = DirectionalSignal(
            asset="BTC", horizon_minutes=5,
            timestamp_utc=_NOW, direction=Direction.UP, confidence=0.72,
        )
        assert sig.horizon_minutes == 5

    def test_supported_15_accepted(self):
        sig = DirectionalSignal(
            asset="BTC", horizon_minutes=15,
            timestamp_utc=_NOW, direction=Direction.DOWN, confidence=0.65,
        )
        assert sig.horizon_minutes == 15


# ── MarketPricingSnapshot: spread non-spoofable ───────────────────────────────

class TestMarketPricingSnapshotContracts:

    def test_spread_derived_from_ask_bid_ignores_passed_value(self):
        """
        spread_yes is always ask_yes - bid_yes.
        Any externally-passed spread_yes is overridden by __post_init__.
        Prevents spread injection attack.
        """
        snapshot = MarketPricingSnapshot(
            market_id="mkt",
            ask_yes=0.60, bid_yes=0.20,  # spread = 0.40
            ask_no=0.57,  bid_no=0.55,
            liquidity=5000.0,
            timestamp_utc=_NOW,
            spread_yes=0.01,  # attempted injection: ignored
        )
        assert snapshot.spread_yes == pytest.approx(0.40)

    def test_spread_no_derived_ignores_passed_value(self):
        snapshot = MarketPricingSnapshot(
            market_id="mkt",
            ask_yes=0.44, bid_yes=0.42,
            ask_no=0.60,  bid_no=0.20,  # spread = 0.40
            liquidity=5000.0,
            timestamp_utc=_NOW,
            spread_no=0.01,  # ignored
        )
        assert snapshot.spread_no == pytest.approx(0.40)

    def test_tight_spread_correctly_derived(self):
        snapshot = MarketPricingSnapshot(
            market_id="mkt",
            ask_yes=0.50, bid_yes=0.48,
            ask_no=0.52,  bid_no=0.50,
            liquidity=10000.0,
            timestamp_utc=_NOW,
        )
        assert snapshot.spread_yes == pytest.approx(0.02)
        assert snapshot.spread_no  == pytest.approx(0.02)
