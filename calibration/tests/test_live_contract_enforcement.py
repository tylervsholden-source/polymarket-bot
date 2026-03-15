"""
calibration/tests/test_live_contract_enforcement.py

Phase 10: Live mode contract enforcement.

Covers:
1. Live mode rejects when intended_size_usdc is None (ValueError)
2. Live mode accepts explicit intended_size_usdc
3. Paper mode: None → 20.0 USDC fallback (documented, not silent)
4. Default mode: None → 20.0 USDC fallback
5. Invalid bridge_intent_side rejected at object boundary (ValueError)
6. Invalid horizon rejected at DirectionalSignal construction (ValueError)
7. Live mode + missing class_probabilities → REJECT (not ValueError)
8. Live mode + weak calibration → REJECT
9. intended_size_usdc propagates to audit trail in live mode
10. Paper mode does not raise ValueError on missing size
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from calibration.decision_policy import decide
from calibration.probability_mapper import map_to_event_probability
from calibration.types import (
    LIVE_CAL_CONFIG,
    PAPER_CAL_CONFIG,
    DEFAULT_CAL_CONFIG,
    CalibrationConfig,
    CalibrationMethod,
    CalibrationQuality,
    CalibrationRejectionReason,
    MarketPricingSnapshot,
    RawSignalOutput,
    TradeDecisionType,
)
from signal_bridge.types import DirectionalSignal, Direction

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _raw(
    predicted_class: str = "UP",
    confidence: float = 0.75,
    horizon: int = 5,
    up: float = 0.75,
    down: float = 0.15,
    no_trade: float = 0.10,
    with_class_probs: bool = True,
) -> RawSignalOutput:
    return RawSignalOutput(
        asset="BTC",
        horizon_minutes=horizon,
        timestamp_utc=_NOW,
        predicted_class=predicted_class,
        raw_confidence=confidence,
        class_probabilities=(
            {"UP": up, "DOWN": down, "NO_TRADE": no_trade}
            if with_class_probs else None
        ),
    )


def _cal(
    up: float = 0.75,
    down: float = 0.15,
    no_trade: float = 0.10,
    polarity: str = "NORMAL",
    horizon: int = 5,
    quality: CalibrationQuality = CalibrationQuality.STRONG,
    predicted_class: str = "UP",
    with_class_probs: bool = True,
):
    r = _raw(
        predicted_class=predicted_class,
        confidence=up,
        horizon=horizon,
        up=up,
        down=down,
        no_trade=no_trade,
        with_class_probs=with_class_probs,
    )
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


def _pricing(
    ask_yes: float = 0.44,
    bid_yes: float = 0.42,
    ask_no: float = 0.57,
    bid_no: float = 0.55,
    liquidity: float = 5000.0,
    age_seconds: float = 0.0,
) -> MarketPricingSnapshot:
    ts = _NOW - timedelta(seconds=age_seconds)
    return MarketPricingSnapshot(
        market_id="mkt-test",
        ask_yes=ask_yes,
        bid_yes=bid_yes,
        ask_no=ask_no,
        bid_no=bid_no,
        liquidity=liquidity,
        timestamp_utc=ts,
    )


# ── 1. intended_size_usdc contract ────────────────────────────────────────────

class TestIntendedSizeContract:

    def test_live_none_returns_missing_size_reject(self):
        """Kanıt #1: Live modda intended_size_usdc=None → REJECT(MISSING_SIZE), exception değil."""
        from calibration.types import CalibrationRejectionReason, TradeDecisionType
        cal = _cal()
        result = decide(cal, _pricing(), config=LIVE_CAL_CONFIG, now_utc=_NOW,
                        intended_size_usdc=None)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.MISSING_SIZE
        assert "live" in result.rationale.lower()
        assert "intended_size_usdc" in result.rationale

    def test_live_explicit_size_no_error(self):
        """Live modda explicit size geçilince ValueError yok."""
        cal = _cal(up=0.92, down=0.05, no_trade=0.03)
        result = decide(cal, _pricing(), config=LIVE_CAL_CONFIG, now_utc=_NOW,
                        intended_size_usdc=50.0)
        assert result is not None

    def test_live_zero_size_still_accepted(self):
        """Sıfır size teknik olarak geçerli (ValueError değil — karar katmanına geçer)."""
        cal = _cal()
        # ValueError yok (0.0 explicit geçildi)
        result = decide(cal, _pricing(), config=LIVE_CAL_CONFIG, now_utc=_NOW,
                        intended_size_usdc=0.0)
        assert result is not None

    def test_paper_none_uses_20_usdc_default(self):
        """Paper modda None → 20.0 USDC (belgelenmiş geri dönüş, ValueError yok)."""
        cal = _cal()
        result = decide(cal, _pricing(), config=PAPER_CAL_CONFIG, now_utc=_NOW,
                        intended_size_usdc=None)
        assert result is not None
        assert result.intended_size_usdc_used == 20.0

    def test_default_mode_none_uses_20_usdc(self):
        """Default modda None → 20.0 USDC."""
        cal = _cal()
        result = decide(cal, _pricing(), config=DEFAULT_CAL_CONFIG, now_utc=_NOW,
                        intended_size_usdc=None)
        assert result.intended_size_usdc_used == 20.0

    def test_explicit_size_in_live_propagates(self):
        """Live modda geçilen boyut audit trail'e yazılır."""
        cal = _cal(up=0.92, down=0.05, no_trade=0.03)
        result = decide(cal, _pricing(), config=LIVE_CAL_CONFIG, now_utc=_NOW,
                        intended_size_usdc=75.0)
        assert result.intended_size_usdc_used == 75.0

    def test_custom_live_config_also_enforces_size(self):
        """mode='live' olan herhangi bir config intended_size_usdc zorunlu kılar."""
        from calibration.types import CalibrationRejectionReason, TradeDecisionType
        cfg = CalibrationConfig(mode="live")
        cal = _cal()
        result = decide(cal, _pricing(), config=cfg, now_utc=_NOW, intended_size_usdc=None)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.MISSING_SIZE


# ── 2. Invalid bridge_intent_side ─────────────────────────────────────────────

class TestBridgeIntentSideContract:

    def test_invalid_side_raises_at_construction(self):
        """
        Kanıt #2: bridge_intent_side='INVALID' → ValueError at object construction.
        Object sınırında yakalanır — decide() çağrısından önce.
        """
        import dataclasses
        cal = _cal()
        with pytest.raises(ValueError, match="bridge_intent_side"):
            dataclasses.replace(cal, bridge_intent_side="INVALID")

    def test_empty_side_raises(self):
        import dataclasses
        cal = _cal()
        with pytest.raises(ValueError, match="bridge_intent_side"):
            dataclasses.replace(cal, bridge_intent_side="")

    def test_lowercase_side_raises(self):
        import dataclasses
        cal = _cal()
        with pytest.raises(ValueError, match="bridge_intent_side"):
            dataclasses.replace(cal, bridge_intent_side="yes")

    def test_valid_yes_accepted(self):
        """'YES' geçerli — ValueError yok."""
        cal = _cal(predicted_class="UP", polarity="NORMAL")
        assert cal.bridge_intent_side == "YES"

    def test_valid_no_accepted(self):
        """'NO' geçerli — ValueError yok."""
        cal = _cal(predicted_class="DOWN", polarity="NORMAL",
                   up=0.15, down=0.75, no_trade=0.10)
        assert cal.bridge_intent_side == "NO"


# ── 3. Invalid horizon at DirectionalSignal ────────────────────────────────────

class TestHorizonContractAtSignal:

    def test_unsupported_horizon_raises_at_directional_signal(self):
        """DirectionalSignal.__post_init__ rejects unsupported horizon."""
        with pytest.raises(ValueError, match="horizon_minutes"):
            DirectionalSignal(
                asset="BTC",
                horizon_minutes=30,  # not in {5, 15}
                timestamp_utc=_NOW,
                direction=Direction.UP,
                confidence=0.72,
            )

    def test_horizon_1_raises(self):
        with pytest.raises(ValueError):
            DirectionalSignal(
                asset="BTC", horizon_minutes=1, timestamp_utc=_NOW,
                direction=Direction.UP, confidence=0.72,
            )

    def test_horizon_60_raises(self):
        with pytest.raises(ValueError):
            DirectionalSignal(
                asset="BTC", horizon_minutes=60, timestamp_utc=_NOW,
                direction=Direction.UP, confidence=0.72,
            )

    def test_horizon_5_accepted(self):
        """5m signal accepted."""
        sig = DirectionalSignal(
            asset="BTC", horizon_minutes=5, timestamp_utc=_NOW,
            direction=Direction.UP, confidence=0.72,
        )
        assert sig.horizon_minutes == 5

    def test_horizon_15_accepted(self):
        """15m signal accepted."""
        sig = DirectionalSignal(
            asset="BTC", horizon_minutes=15, timestamp_utc=_NOW,
            direction=Direction.UP, confidence=0.72,
        )
        assert sig.horizon_minutes == 15


# ── 4. Live mode rejects on calibration quality / class probs ─────────────────

class TestLiveModeCalibrationGuards:

    def test_live_rejects_unknown_calibration(self):
        """Live modda UNKNOWN kalibrasyon → REJECT."""
        cal = _cal(quality=CalibrationQuality.UNKNOWN)
        result = decide(cal, _pricing(), config=LIVE_CAL_CONFIG, now_utc=_NOW,
                        intended_size_usdc=20.0)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.UNKNOWN_CALIBRATION

    def test_live_rejects_weak_calibration(self):
        """Live modda WEAK kalibrasyon → REJECT."""
        cal = _cal(quality=CalibrationQuality.WEAK)
        result = decide(cal, _pricing(), config=LIVE_CAL_CONFIG, now_utc=_NOW,
                        intended_size_usdc=20.0)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.WEAK_CALIBRATION

    def test_live_rejects_missing_class_probabilities(self):
        """Live modda class_probabilities=None → REJECT (require_class_probabilities=True)."""
        cal = _cal(with_class_probs=False, quality=CalibrationQuality.STRONG)
        result = decide(cal, _pricing(), config=LIVE_CAL_CONFIG, now_utc=_NOW,
                        intended_size_usdc=20.0)
        assert result.decision == TradeDecisionType.REJECT

    def test_paper_accepts_unknown_calibration(self):
        """Paper modda UNKNOWN kalibrasyon kabul edilir."""
        cal = _cal(quality=CalibrationQuality.UNKNOWN)
        result = decide(cal, _pricing(), config=PAPER_CAL_CONFIG, now_utc=_NOW)
        assert result.rejection_reason != CalibrationRejectionReason.UNKNOWN_CALIBRATION
