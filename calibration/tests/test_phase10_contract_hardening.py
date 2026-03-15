"""
calibration/tests/test_phase10_contract_hardening.py

Phase 10: Contract Hardening — tüm sözleşme sertleştirme testleri.

Kapsam:
1. intended_size_usdc — live modda None geçmek ValueError fırlatmalı
2. intended_size_usdc — paper/default modda None → 20.0 USDC sessiz varsayılan
3. min_prob_sum — live modda 0.90 (PROB_LIVE_MIN_SUM); paper modda 0.50
4. CalibrationConfig.__post_init__ — geçersiz parametre değerleri ValueError fırlatır
5. LIVE_CAL_CONFIG.min_prob_sum == PROB_LIVE_MIN_SUM (config entegrasyon)
6. decide() live modda prob toplam 0.72 → REJECT (< 0.90)
7. decide() live modda prob toplam 0.91 → EXECUTE (>= 0.90)
8. decide() paper modda prob toplam 0.72 → geçer prob filtresi
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
    CalibrationConfig,
    CalibrationMethod,
    CalibrationQuality,
    CalibrationRejectionReason,
    MarketPricingSnapshot,
    RawSignalOutput,
    TradeDecisionType,
)

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


# ── Test yardımcıları ──────────────────────────────────────────────────────────

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
    age_seconds: float = 0,
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
    """Phase 10 (updated): live modda None → REJECT(MISSING_SIZE), exception değil."""

    def test_live_mode_none_returns_missing_size_reject(self):
        """Live modda intended_size_usdc=None → structured REJECT(MISSING_SIZE)."""
        from calibration.types import CalibrationRejectionReason, TradeDecisionType
        cal = _cal()
        pricing = _pricing()
        result = decide(cal, pricing, config=LIVE_CAL_CONFIG, now_utc=_NOW,
                        intended_size_usdc=None)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.MISSING_SIZE

    def test_live_mode_explicit_size_no_error(self):
        """Live modda explicit size geçilince REJECT(MISSING_SIZE) yok."""
        cal = _cal(up=0.92, down=0.05, no_trade=0.03)
        pricing = _pricing(ask_yes=0.44, bid_yes=0.42, ask_no=0.57, bid_no=0.55)
        result = decide(cal, pricing, config=LIVE_CAL_CONFIG, now_utc=_NOW,
                        intended_size_usdc=50.0)
        assert result is not None

    def test_paper_mode_none_uses_default_20(self):
        """Paper modda None geçilince 20.0 USDC varsayılan kullanılır — hata yok."""
        cal = _cal()
        pricing = _pricing()
        result = decide(cal, pricing, config=PAPER_CAL_CONFIG, now_utc=_NOW,
                        intended_size_usdc=None)
        assert result is not None
        assert result.intended_size_usdc_used == 20.0

    def test_default_mode_none_uses_default_20(self):
        """Default modda None → 20.0 USDC sessiz varsayılan."""
        from calibration.types import DEFAULT_CAL_CONFIG
        cal = _cal()
        pricing = _pricing()
        result = decide(cal, pricing, config=DEFAULT_CAL_CONFIG, now_utc=_NOW,
                        intended_size_usdc=None)
        assert result.intended_size_usdc_used == 20.0

    def test_explicit_size_propagates_to_audit_trail(self):
        """Geçilen intended_size_usdc audit trail'e yazılır."""
        cal = _cal()
        pricing = _pricing()
        result = decide(cal, pricing, config=PAPER_CAL_CONFIG, now_utc=_NOW,
                        intended_size_usdc=75.0)
        assert result.intended_size_usdc_used == 75.0

    def test_live_mode_reject_rationale_mentions_live(self):
        """REJECT rationale 'live' içermeli — caller için net."""
        from calibration.types import CalibrationRejectionReason
        cal = _cal()
        pricing = _pricing()
        result = decide(cal, pricing, config=LIVE_CAL_CONFIG, now_utc=_NOW,
                        intended_size_usdc=None)
        assert result.rejection_reason == CalibrationRejectionReason.MISSING_SIZE
        assert "live" in result.rationale.lower()


# ── 2. min_prob_sum contract ───────────────────────────────────────────────────

class TestMinProbSumContract:
    """Phase 10: live modda prob toplam ≥ 0.90 zorunlu."""

    def test_live_config_has_correct_min_prob_sum(self):
        """LIVE_CAL_CONFIG.min_prob_sum == PROB_LIVE_MIN_SUM."""
        assert LIVE_CAL_CONFIG.min_prob_sum == PROB_LIVE_MIN_SUM
        assert PROB_LIVE_MIN_SUM == 0.90

    def test_paper_config_has_default_min_prob_sum(self):
        """PAPER_CAL_CONFIG.min_prob_sum == PROB_MIN_SUM (0.50)."""
        assert PAPER_CAL_CONFIG.min_prob_sum == PROB_MIN_SUM
        assert PROB_MIN_SUM == 0.50

    def test_live_low_prob_sum_rejects(self):
        """Live modda prob toplam 0.72 → INCONSISTENT_PROBS reject."""
        # up=0.60, down=0.12, no_trade=0.00 → toplam=0.72 < 0.90
        cal = _cal(up=0.60, down=0.12, no_trade=0.00)
        pricing = _pricing()
        result = decide(cal, pricing, config=LIVE_CAL_CONFIG, now_utc=_NOW,
                        intended_size_usdc=20.0)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.INCONSISTENT_PROBS
        assert "0.72" in result.rationale or "boundary" in result.rationale.lower()

    def test_live_high_prob_sum_passes_boundary(self):
        """Live modda prob toplam 0.91 → prob boundary geçer."""
        # up=0.76, down=0.10, no_trade=0.05 → toplam=0.91 >= 0.90
        cal = _cal(up=0.76, down=0.10, no_trade=0.05)
        pricing = _pricing(ask_yes=0.44, bid_yes=0.42, ask_no=0.57, bid_no=0.55)
        result = decide(cal, pricing, config=LIVE_CAL_CONFIG, now_utc=_NOW,
                        intended_size_usdc=20.0)
        # INCONSISTENT_PROBS sebebiyle reddedilmemeli
        assert result.rejection_reason != CalibrationRejectionReason.INCONSISTENT_PROBS

    def test_paper_low_prob_sum_passes_boundary(self):
        """Paper modda prob toplam 0.72 → prob boundary geçer (0.50 eşik)."""
        # up=0.60, down=0.12, no_trade=0.00 → toplam=0.72 >= 0.50 (paper eşik)
        cal = _cal(up=0.60, down=0.12, no_trade=0.00)
        pricing = _pricing()
        result = decide(cal, pricing, config=PAPER_CAL_CONFIG, now_utc=_NOW,
                        intended_size_usdc=None)
        assert result.rejection_reason != CalibrationRejectionReason.INCONSISTENT_PROBS

    def test_live_exactly_at_threshold_passes(self):
        """Live modda prob toplam tam 0.90 → boundary geçer."""
        # up=0.80, down=0.10, no_trade=0.00 → toplam=0.90
        cal = _cal(up=0.80, down=0.10, no_trade=0.00)
        pricing = _pricing()
        result = decide(cal, pricing, config=LIVE_CAL_CONFIG, now_utc=_NOW,
                        intended_size_usdc=20.0)
        assert result.rejection_reason != CalibrationRejectionReason.INCONSISTENT_PROBS

    def test_live_just_below_threshold_rejects(self):
        """Live modda prob toplam 0.899 → INCONSISTENT_PROBS."""
        # up=0.80, down=0.099, no_trade=0.00 → toplam≈0.899
        cal = _cal(up=0.800, down=0.099, no_trade=0.000)
        pricing = _pricing()
        result = decide(cal, pricing, config=LIVE_CAL_CONFIG, now_utc=_NOW,
                        intended_size_usdc=20.0)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.INCONSISTENT_PROBS


# ── 3. CalibrationConfig.__post_init__ validation ─────────────────────────────

class TestCalibrationConfigPostInit:
    """Type-level constructor validation."""

    def test_negative_edge_threshold_raises(self):
        with pytest.raises(ValueError, match="min_execution_adjusted_edge"):
            CalibrationConfig(min_execution_adjusted_edge=-0.01)

    def test_confidence_out_of_range_raises(self):
        with pytest.raises(ValueError, match="min_calibrated_confidence"):
            CalibrationConfig(min_calibrated_confidence=1.5)

    def test_fee_negative_raises(self):
        with pytest.raises(ValueError, match="assumed_taker_fee_pct"):
            CalibrationConfig(assumed_taker_fee_pct=-0.01)

    def test_fee_above_1_raises(self):
        with pytest.raises(ValueError, match="assumed_taker_fee_pct"):
            CalibrationConfig(assumed_taker_fee_pct=1.5)

    def test_min_prob_sum_out_of_range_raises(self):
        with pytest.raises(ValueError, match="min_prob_sum"):
            CalibrationConfig(min_prob_sum=1.5)

    def test_valid_config_no_error(self):
        """Geçerli parametreler → ValueError yok."""
        cfg = CalibrationConfig(
            min_execution_adjusted_edge=0.02,
            min_calibrated_confidence=0.60,
            assumed_taker_fee_pct=0.01,
            min_prob_sum=0.85,
        )
        assert cfg.min_prob_sum == 0.85

    def test_live_config_valid(self):
        """LIVE_CAL_CONFIG __post_init__ geçer."""
        assert LIVE_CAL_CONFIG.min_execution_adjusted_edge == 0.03
        assert LIVE_CAL_CONFIG.min_prob_sum == PROB_LIVE_MIN_SUM

    def test_paper_config_valid(self):
        """PAPER_CAL_CONFIG __post_init__ geçer."""
        assert PAPER_CAL_CONFIG.min_prob_sum == PROB_MIN_SUM
