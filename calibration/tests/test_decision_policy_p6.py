"""
calibration/tests/test_decision_policy_p6.py

Phase 6 + Phase 7 karar politikası testleri.

Kapsam:
- Pricing snapshot yapısal doğrulaması (fiyat [0,1] dışı, ask<bid, gelecek zaman)
- bridge_intent_side geçersiz değer
- class_probabilities zorunluluğu (live vs paper)
- Horizon-aware staleness
- Side-specific slippage (YES/NO ayrı)
- Horizon enforcement (SUPPORTED_HORIZONS)
- SUPPORTED_HORIZONS set davranışı
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from calibration.decision_policy import decide, _validate_pricing_snapshot, _effective_max_age
from calibration.probability_mapper import map_to_event_probability
from calibration.types import (
    CalibrationConfig,
    CalibrationMethod,
    CalibrationQuality,
    CalibrationRejectionReason,
    CalibratedSignal,
    LIVE_CAL_CONFIG,
    PAPER_CAL_CONFIG,
    SUPPORTED_HORIZONS,
    MarketPricingSnapshot,
    RawSignalOutput,
    TradeDecisionType,
)

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


# ── Yardımcılar ───────────────────────────────────────────────────────────────

def _raw(predicted_class="UP", confidence=0.72, horizon=15,
         class_probabilities=None):
    return RawSignalOutput(
        asset="BTC",
        horizon_minutes=horizon,
        timestamp_utc=_NOW,
        predicted_class=predicted_class,
        raw_confidence=confidence,
        class_probabilities=class_probabilities,
    )


def _pricing(ask_yes=0.44, bid_yes=0.42, ask_no=0.57, bid_no=0.55,
             liquidity=5000.0, age_seconds=0, ts_override=None):
    ts = ts_override if ts_override is not None else (_NOW - timedelta(seconds=age_seconds))
    return MarketPricingSnapshot(
        market_id="mkt-btc-001",
        ask_yes=ask_yes, bid_yes=bid_yes,
        ask_no=ask_no,   bid_no=bid_no,
        liquidity=liquidity,
        spread_yes=round(ask_yes - bid_yes, 6),
        spread_no=round(ask_no - bid_no, 6),
        timestamp_utc=ts,
    )


def _cal_signal(up=0.72, down=0.18, no_trade=0.10, polarity="NORMAL",
                quality=CalibrationQuality.STRONG,
                raw_kwargs=None):
    r = _raw(**(raw_kwargs or {}))
    cal, err = map_to_event_probability(
        raw=r,
        calibrated_up_prob=up,
        calibrated_down_prob=down,
        calibrated_no_trade_prob=no_trade,
        polarity=polarity,
        calibration_method=CalibrationMethod.PLATT,
        calibration_quality=quality,
    )
    assert err is None, f"map_to_event_probability başarısız: {err}"
    return cal


# ── 1. Pricing snapshot yapısal doğrulaması ──────────────────────────────────

class TestValidatePricingSnapshot:

    def test_valid_snapshot_returns_none(self):
        pr = _pricing()
        assert _validate_pricing_snapshot(pr, _NOW) is None

    def test_ask_yes_above_one_rejected(self):
        pr = _pricing(ask_yes=1.05, bid_yes=0.42)
        # spread negatif olur ama yapısal check daha önce yakalar
        result = _validate_pricing_snapshot(pr, _NOW)
        assert result is not None
        assert "ask_yes" in result

    def test_bid_yes_below_zero_rejected(self):
        pr = _pricing(bid_yes=-0.01)
        result = _validate_pricing_snapshot(pr, _NOW)
        assert result is not None
        assert "bid_yes" in result

    def test_ask_no_above_one_rejected(self):
        pr = _pricing(ask_no=1.10, bid_no=0.55)
        result = _validate_pricing_snapshot(pr, _NOW)
        assert result is not None
        assert "ask_no" in result

    def test_bid_no_below_zero_rejected(self):
        pr = _pricing(bid_no=-0.05)
        result = _validate_pricing_snapshot(pr, _NOW)
        assert result is not None
        assert "bid_no" in result

    def test_inverted_spread_yes_rejected(self):
        # ask_yes < bid_yes → ters spread
        pr = _pricing(ask_yes=0.40, bid_yes=0.45)
        result = _validate_pricing_snapshot(pr, _NOW)
        assert result is not None
        assert "ters spread" in result

    def test_inverted_spread_no_rejected(self):
        # ask_no < bid_no → ters spread
        pr = _pricing(ask_no=0.50, bid_no=0.55)
        result = _validate_pricing_snapshot(pr, _NOW)
        assert result is not None
        assert "ters spread" in result

    def test_future_timestamp_rejected(self):
        future_ts = _NOW + timedelta(seconds=30)
        pr = _pricing(ts_override=future_ts)
        result = _validate_pricing_snapshot(pr, _NOW)
        assert result is not None
        assert "gelecekte" in result

    def test_exact_boundary_zero_is_valid(self):
        # bid=0.0 is valid ([0,1] sınırı). ask_no gerçekçi tutulmalı —
        # binary sanity: ask_yes + ask_no ∈ [0.85, 1.15].
        pr = _pricing(ask_yes=0.44, bid_yes=0.0, ask_no=0.57, bid_no=0.0)
        assert _validate_pricing_snapshot(pr, _NOW) is None

    def test_exact_ask_equals_bid_is_valid(self):
        # ask == bid: sıfır spread — yapısal hata değil
        pr = _pricing(ask_yes=0.44, bid_yes=0.44)
        assert _validate_pricing_snapshot(pr, _NOW) is None


class TestInvalidPricingInDecide:

    def test_future_timestamp_causes_invalid_pricing_rejection(self):
        cal = _cal_signal()
        future_ts = _NOW + timedelta(seconds=10)
        pr = _pricing(ts_override=future_ts)
        d = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)
        assert d.decision == TradeDecisionType.REJECT
        assert d.rejection_reason == CalibrationRejectionReason.INVALID_PRICING

    def test_price_out_of_range_causes_invalid_pricing_rejection(self):
        cal = _cal_signal()
        pr = _pricing(ask_yes=1.05, bid_yes=0.42)
        d = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)
        assert d.decision == TradeDecisionType.REJECT
        assert d.rejection_reason == CalibrationRejectionReason.INVALID_PRICING

    def test_inverted_spread_causes_invalid_pricing_rejection(self):
        cal = _cal_signal()
        pr = _pricing(ask_yes=0.40, bid_yes=0.45)
        d = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)
        assert d.decision == TradeDecisionType.REJECT
        assert d.rejection_reason == CalibrationRejectionReason.INVALID_PRICING

    def test_invalid_pricing_checked_before_staleness(self):
        """Yapısal hata, yaş kontrolünden önce yakalanmalı."""
        cal = _cal_signal()
        # Hem gelecek zaman damgası hem de fiyat hatası: yapısal hata önce
        pr = _pricing(ask_yes=1.50, bid_yes=0.42,
                      ts_override=_NOW + timedelta(seconds=10))
        d = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)
        assert d.rejection_reason == CalibrationRejectionReason.INVALID_PRICING


# ── 2. bridge_intent_side geçersiz değer ─────────────────────────────────────

class TestInvalidBridgeIntentSide:

    def test_invalid_bridge_intent_side_rejects(self):
        """
        Phase 10: CalibratedSignal.__post_init__ now rejects invalid bridge_intent_side
        at construction time. dataclasses.replace() triggers __post_init__.
        Downstream manipulation/deserialization error is caught at object boundary.
        """
        import dataclasses
        base = _cal_signal()
        with pytest.raises(ValueError, match="bridge_intent_side"):
            dataclasses.replace(base, bridge_intent_side="MAYBE")

    def test_empty_bridge_intent_side_rejects(self):
        """Phase 10: empty string bridge_intent_side raises ValueError at construction."""
        import dataclasses
        base = _cal_signal()
        with pytest.raises(ValueError, match="bridge_intent_side"):
            dataclasses.replace(base, bridge_intent_side="")

    def test_valid_yes_bridge_intent_passes_guard(self):
        cal = _cal_signal(up=0.72, down=0.18)
        assert cal.bridge_intent_side == "YES"
        pr = _pricing(ask_yes=0.44)
        d = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)
        # Guard geçildi; başka bir sebeple reddedilmedi
        assert d.rejection_reason != CalibrationRejectionReason.AMBIGUOUS_MAPPING

    def test_valid_no_bridge_intent_passes_guard(self):
        cal = _cal_signal(up=0.20, down=0.65,
                          raw_kwargs={"predicted_class": "DOWN", "confidence": 0.65})
        assert cal.bridge_intent_side == "NO"
        pr = _pricing(ask_no=0.44, bid_no=0.42)
        d = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)
        assert d.rejection_reason != CalibrationRejectionReason.AMBIGUOUS_MAPPING


# ── 3. class_probabilities zorunluluğu ───────────────────────────────────────

class TestClassProbabilitiesRequirement:

    def test_live_config_rejects_missing_class_probabilities(self):
        """
        Live config: class_probabilities=None → REJECT.
        Ham güven proxy canlı modda kabul edilmez.
        """
        raw = _raw(class_probabilities=None)
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

        pr = _pricing(ask_yes=0.44)
        d = decide(cal, pr, LIVE_CAL_CONFIG, now_utc=_NOW, intended_size_usdc=20.0)

        assert d.decision == TradeDecisionType.REJECT
        assert d.rejection_reason == CalibrationRejectionReason.UNKNOWN_CALIBRATION
        assert "class_probabilities" in d.rationale

    def test_paper_config_allows_missing_class_probabilities(self):
        """
        Paper config: class_probabilities=None → geçer (ham güven proxy kabul edilir).
        """
        raw = _raw(class_probabilities=None)
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

        pr = _pricing(ask_yes=0.44)
        d = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)

        assert d.decision == TradeDecisionType.EXECUTE_YES

    def test_live_config_with_class_probabilities_passes_check(self):
        """
        Live config: class_probabilities sağlanmış → check geçilir.
        """
        raw = _raw(class_probabilities={"UP": 0.72, "DOWN": 0.18, "NO_TRADE": 0.10})
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

        pr = _pricing(ask_yes=0.44)
        # LIVE_CAL_CONFIG: reject_on_unknown_calibration=True, quality=STRONG → geçer
        # Ancak LIVE min_edge=0.03: 0.265 > 0.03 → geçer
        d = decide(cal, pr, LIVE_CAL_CONFIG, now_utc=_NOW, intended_size_usdc=20.0)

        # class_probabilities check geçildi — başka sebeple reddedilmemeli
        assert d.rejection_reason != CalibrationRejectionReason.UNKNOWN_CALIBRATION


# ── 4. Horizon-aware staleness ────────────────────────────────────────────────

class TestHorizonAwareStaleness:

    def test_effective_max_age_no_fraction_returns_config_value(self):
        cfg = CalibrationConfig(max_snapshot_age_seconds=300,
                                max_snapshot_age_horizon_fraction=None)
        assert _effective_max_age(cfg, horizon_minutes=15) == pytest.approx(300.0)

    def test_effective_max_age_fraction_scales_with_horizon(self):
        # 15dk horizon * 60 * 0.2 = 180s < 300s → 180s
        cfg = CalibrationConfig(max_snapshot_age_seconds=300,
                                max_snapshot_age_horizon_fraction=0.2)
        assert _effective_max_age(cfg, horizon_minutes=15) == pytest.approx(180.0)

    def test_effective_max_age_fraction_capped_by_config_max(self):
        # 60dk horizon * 60 * 0.2 = 720s > 300s → 300s (capped)
        cfg = CalibrationConfig(max_snapshot_age_seconds=300,
                                max_snapshot_age_horizon_fraction=0.2)
        assert _effective_max_age(cfg, horizon_minutes=60) == pytest.approx(300.0)

    def test_horizon_aware_staleness_rejects(self):
        """
        Fraction=0.2, horizon=15dk → max_age=180s.
        age=200s > 180s → STALE.
        Sabit eşik (300s) kullansaydık geçerdi: 200s < 300s.
        """
        cfg = CalibrationConfig(
            max_snapshot_age_seconds=300,
            max_snapshot_age_horizon_fraction=0.2,
        )
        raw = _raw(horizon=15)
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

        pr = _pricing(age_seconds=200)
        d = decide(cal, pr, cfg, now_utc=_NOW)

        assert d.decision == TradeDecisionType.REJECT
        assert d.rejection_reason == CalibrationRejectionReason.STALE_PRICING

    def test_horizon_aware_staleness_passes_under_derived_limit(self):
        """
        Fraction=0.2, horizon=15dk → max_age=180s.
        age=150s < 180s → STALE değil; başka sebeple geçer veya execute eder.
        """
        cfg = CalibrationConfig(
            max_snapshot_age_seconds=300,
            max_snapshot_age_horizon_fraction=0.2,
            min_execution_adjusted_edge=0.02,
        )
        raw = _raw(horizon=15)
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

        pr = _pricing(age_seconds=150, ask_yes=0.44)
        d = decide(cal, pr, cfg, now_utc=_NOW)

        assert d.rejection_reason != CalibrationRejectionReason.STALE_PRICING


# ── 5. Side-specific slippage ─────────────────────────────────────────────────

class TestSideSpecificSlippage:

    def test_side_specific_slippage_yes_uses_yes_rate(self):
        """
        assumed_slippage_yes_pct=0.02, assumed_slippage_pct=0.005 (fallback)
        YES edge = 0.72 - 0.44 - 0.01 - 0.02 = 0.25
        """
        cfg = CalibrationConfig(
            assumed_slippage_pct=0.005,
            assumed_slippage_yes_pct=0.02,
        )
        cal = _cal_signal(up=0.72, down=0.18)
        from calibration.edge_estimator import estimate_yes_edge
        edge = estimate_yes_edge(cal, _pricing(ask_yes=0.44), cfg)
        assert edge.expected_edge == pytest.approx(0.25, abs=1e-6)
        assert edge.diagnostics["slippage"] == pytest.approx(0.02)

    def test_side_specific_slippage_no_uses_no_rate(self):
        """
        assumed_slippage_no_pct=0.02, assumed_slippage_pct=0.005 (fallback)
        NO edge = 0.65 - 0.45 - 0.01 - 0.02 = 0.17
        """
        cfg = CalibrationConfig(
            assumed_slippage_pct=0.005,
            assumed_slippage_no_pct=0.02,
        )
        cal = _cal_signal(up=0.20, down=0.65)
        from calibration.edge_estimator import estimate_no_edge
        edge = estimate_no_edge(cal, _pricing(ask_no=0.45, bid_no=0.43), cfg)
        assert edge.expected_edge == pytest.approx(0.17, abs=1e-6)
        assert edge.diagnostics["slippage"] == pytest.approx(0.02)

    def test_yes_side_falls_back_to_shared_slippage(self):
        """assumed_slippage_yes_pct=None → assumed_slippage_pct kullanılır."""
        cfg = CalibrationConfig(
            assumed_slippage_pct=0.005,
            assumed_slippage_yes_pct=None,
        )
        cal = _cal_signal(up=0.72, down=0.18)
        from calibration.edge_estimator import estimate_yes_edge
        edge = estimate_yes_edge(cal, _pricing(ask_yes=0.44), cfg)
        assert edge.diagnostics["slippage"] == pytest.approx(0.005)

    def test_no_side_falls_back_to_shared_slippage(self):
        """assumed_slippage_no_pct=None → assumed_slippage_pct kullanılır."""
        cfg = CalibrationConfig(
            assumed_slippage_pct=0.005,
            assumed_slippage_no_pct=None,
        )
        cal = _cal_signal(up=0.20, down=0.65)
        from calibration.edge_estimator import estimate_no_edge
        edge = estimate_no_edge(cal, _pricing(ask_no=0.45, bid_no=0.43), cfg)
        assert edge.diagnostics["slippage"] == pytest.approx(0.005)

    def test_different_yes_no_slippage_produces_different_edges(self):
        """YES ve NO slippage farklı → her iki taraf farklı execution_adjusted_ev."""
        cfg = CalibrationConfig(
            assumed_slippage_yes_pct=0.001,
            assumed_slippage_no_pct=0.010,
        )
        cal = _cal_signal(up=0.72, down=0.18)
        from calibration.edge_estimator import estimate_yes_edge, estimate_no_edge
        yes_edge = estimate_yes_edge(cal, _pricing(ask_yes=0.44), cfg)
        no_edge  = estimate_no_edge( cal, _pricing(ask_no=0.57, bid_no=0.55), cfg)
        # YES slippage daha düşük → YES edge daha yüksek (fee ve fiyat sabit tutulursa)
        assert yes_edge.diagnostics["slippage"] < no_edge.diagnostics["slippage"]


# ── 6. Horizon enforcement ────────────────────────────────────────────────────

class TestHorizonEnforcement:

    def test_supported_horizons_contains_5_and_15(self):
        assert 5  in SUPPORTED_HORIZONS
        assert 15 in SUPPORTED_HORIZONS

    def test_unsupported_horizon_not_in_set(self):
        assert 60  not in SUPPORTED_HORIZONS
        assert 30  not in SUPPORTED_HORIZONS
        assert 1   not in SUPPORTED_HORIZONS
        assert 240 not in SUPPORTED_HORIZONS

    def test_unsupported_horizon_rejects(self):
        """60 dakikalık sinyal → UNSUPPORTED_HORIZON."""
        raw = _raw(horizon=60)
        cal, err = map_to_event_probability(
            raw=raw,
            calibrated_up_prob=0.72,
            calibrated_down_prob=0.18,
            calibrated_no_trade_prob=0.10,
            polarity="NORMAL",
            calibration_method=CalibrationMethod.PLATT,
            calibration_quality=CalibrationQuality.STRONG,
        )
        assert err is None  # mapper horizon'u kontrol etmez

        pr = _pricing(ask_yes=0.44)
        d = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)

        assert d.decision == TradeDecisionType.REJECT
        assert d.rejection_reason == CalibrationRejectionReason.UNSUPPORTED_HORIZON

    def test_horizon_5_accepted(self):
        """5 dakikalık sinyal → horizon kontrolü geçilir."""
        raw = _raw(horizon=5)
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
        pr = _pricing(ask_yes=0.44)
        d = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)
        assert d.rejection_reason != CalibrationRejectionReason.UNSUPPORTED_HORIZON

    def test_horizon_15_accepted(self):
        """15 dakikalık sinyal → horizon kontrolü geçilir."""
        cal = _cal_signal()  # default horizon=15
        pr = _pricing(ask_yes=0.44)
        d = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)
        assert d.rejection_reason != CalibrationRejectionReason.UNSUPPORTED_HORIZON

    def test_horizon_checked_before_pricing_validation(self):
        """
        Horizon kontrolü (adım 4), pricing validasyonundan (adım 5) önce gelir.
        Hem geçersiz horizon hem geçersiz pricing → horizon sebep olarak görünür.
        """
        raw = _raw(horizon=30)
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
        # Hem horizon geçersiz hem pricing yapısal hatalı
        pr = _pricing(ask_yes=1.50, bid_yes=0.42)
        d = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)
        assert d.rejection_reason == CalibrationRejectionReason.UNSUPPORTED_HORIZON

    def test_rejection_rationale_contains_supported_list(self):
        """Rationale, hangi horizon'ların desteklendiğini göstermelidir."""
        raw = _raw(horizon=60)
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
        pr = _pricing(ask_yes=0.44)
        d = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)
        assert "60" in d.rationale
        assert d.rationale is not None
