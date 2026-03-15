"""
calibration/tests/test_integration.py

Tam kalibrasyon zinciri entegrasyon testi.

Zincir:
    RawSignalOutput (crypto direction)
    → map_to_event_probability (polarity + kalibrasyon)
    → MarketPricingSnapshot
    → decide (edge + filtreler)
    → TradeDecision

Her test uçtan uca akar; modüller ayrı ayrı mock'lanmaz.
"""
from __future__ import annotations

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


# ── Yardımcılar ───────────────────────────────────────────────────────────────

def _raw(predicted_class="UP", confidence=0.72, asset="BTC"):
    return RawSignalOutput(
        asset=asset,
        horizon_minutes=15,
        timestamp_utc=_NOW,
        predicted_class=predicted_class,
        raw_confidence=confidence,
    )


def _pricing(ask_yes=0.44, bid_yes=0.42, ask_no=0.57, bid_no=0.55,
             liquidity=5000.0, age_seconds=0):
    ts = _NOW - timedelta(seconds=age_seconds)
    return MarketPricingSnapshot(
        market_id="mkt-btc-001",
        ask_yes=ask_yes, bid_yes=bid_yes,
        ask_no=ask_no,   bid_no=bid_no,
        liquidity=liquidity,
        spread_yes=round(ask_yes - bid_yes, 6),
        spread_no=round(ask_no - bid_no, 6),
        timestamp_utc=ts,
    )


def _map(raw, up, down, no_trade=0.10, polarity="NORMAL",
         quality=CalibrationQuality.STRONG):
    return map_to_event_probability(
        raw=raw,
        calibrated_up_prob=up,
        calibrated_down_prob=down,
        calibrated_no_trade_prob=no_trade,
        polarity=polarity,
        calibration_method=CalibrationMethod.PLATT,
        calibration_quality=quality,
    )


# ── Zincir testleri ───────────────────────────────────────────────────────────

class TestIntegrationChain:

    def test_up_normal_strong_edge_executes_yes(self):
        """
        BTC ↑ sinyali + NORMAL polarity + yeterli edge → EXECUTE_YES
        Tam zincir: raw → map → price → decide
        """
        raw = _raw(predicted_class="UP", confidence=0.72)
        cal, err = _map(raw, up=0.72, down=0.18)
        assert err is None
        assert cal.bridge_intent_side == "YES"

        pr  = _pricing(ask_yes=0.44)
        d   = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)

        assert d.decision == TradeDecisionType.EXECUTE_YES
        assert d.edge_estimate is not None
        assert d.edge_estimate.passes_edge_gate is True
        # Realistic path: exact value depends on execution_realism slippage model.
        # Verify direction and positivity, not the constant-slippage formula.
        assert d.edge_estimate.expected_edge > 0.20

    def test_down_normal_strong_edge_executes_no(self):
        """
        BTC ↓ sinyali + NORMAL polarity + yeterli edge → EXECUTE_NO
        eff_no = down_prob (NORMAL); karar yalnızca NO tarafını değerlendirir.
        """
        raw = _raw(predicted_class="DOWN", confidence=0.65)
        cal, err = _map(raw, up=0.20, down=0.65)
        assert err is None
        assert cal.bridge_intent_side == "NO"

        pr  = _pricing(ask_no=0.45, bid_no=0.43)
        d   = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)

        assert d.decision == TradeDecisionType.EXECUTE_NO
        # Realistic path: exact value depends on execution_realism slippage model.
        assert d.edge_estimate.expected_edge > 0.14

    def test_up_inverted_executes_no(self):
        """
        BTC ↑ sinyali + INVERTED polarity → intent=NO
        Piyasa "UP = NO" şeklinde yapılandırılmış (örn. Will BTC fall below X?).
        """
        raw = _raw(predicted_class="UP", confidence=0.72)
        cal, err = _map(raw, up=0.72, down=0.18, polarity="INVERTED")
        assert err is None
        assert cal.bridge_intent_side == "NO"
        # INVERTED'da eff_no = up_prob = 0.72
        assert cal.effective_no_prob == pytest.approx(0.72)

        pr  = _pricing(ask_no=0.44, bid_no=0.42)
        d   = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)

        assert d.decision == TradeDecisionType.EXECUTE_NO

    def test_weak_edge_rejects(self):
        """
        Güçlü sinyal ama piyasa fiyatı yakın → yeterli execution-adjusted edge yok → REJECT.
        """
        raw = _raw(predicted_class="UP", confidence=0.62)
        cal, err = _map(raw, up=0.62, down=0.20)
        assert err is None

        # ask_yes yakın → execution_adjusted_ev < min_execution_adjusted_edge
        # bid_yes yakın tutarak spread < max_spread_yes (0.05)
        # ask_no = 0.41 → ask_yes + ask_no = 1.02 (binary sanity geçer)
        pr = _pricing(ask_yes=0.61, bid_yes=0.60, ask_no=0.41, bid_no=0.39)
        d  = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)

        assert d.decision == TradeDecisionType.REJECT
        assert d.rejection_reason == CalibrationRejectionReason.NEGATIVE_EDGE

    def test_live_config_rejects_unknown_calibration(self):
        """
        Live config: UNKNOWN kalibrasyon → otomatik REJECT.
        Aynı sinyal paper config'de geçer.
        """
        raw = _raw(predicted_class="UP", confidence=0.72)
        cal, err = _map(raw, up=0.72, down=0.18, quality=CalibrationQuality.UNKNOWN)
        assert err is None

        pr = _pricing(ask_yes=0.44)

        d_live  = decide(cal, pr, LIVE_CAL_CONFIG,  now_utc=_NOW, intended_size_usdc=20.0)
        d_paper = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)

        assert d_live.decision == TradeDecisionType.REJECT
        assert d_live.rejection_reason == CalibrationRejectionReason.UNKNOWN_CALIBRATION
        assert d_paper.decision == TradeDecisionType.EXECUTE_YES

    def test_stale_pricing_rejects_chain(self):
        """
        Fiyat snapshot'ı bayat → chain fiyat kalitesi kontrolünde durur.
        """
        raw = _raw(predicted_class="UP", confidence=0.72)
        cal, err = _map(raw, up=0.72, down=0.18)
        assert err is None

        pr = _pricing(age_seconds=400)  # > 300s max
        d  = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)

        assert d.decision == TradeDecisionType.REJECT
        assert d.rejection_reason == CalibrationRejectionReason.STALE_PRICING

    def test_no_trade_signal_never_reaches_decision(self):
        """
        NO_TRADE sinyali probability_mapper'da durur — decide'a hiç ulaşmaz.
        """
        raw = _raw(predicted_class="NO_TRADE")
        cal, err = map_to_event_probability(
            raw=raw,
            calibrated_up_prob=0.30,
            calibrated_down_prob=0.30,
            calibrated_no_trade_prob=0.40,
            polarity="NORMAL",
            calibration_method=CalibrationMethod.IDENTITY,
            calibration_quality=CalibrationQuality.UNKNOWN,
        )
        assert cal is None
        assert err == CalibrationRejectionReason.NO_TRADE_SIGNAL

    def test_execution_adjusted_ev_is_actual_gate(self):
        """
        net_ev eşiği geçiyor ama execution_adjusted_ev geçmiyor → REJECT.
        Slippage gerçekten karar metriğine girmiş.
        """
        # net_ev  = 0.72 - 0.686 - 0.01  = 0.024  (> threshold=0.022 → eski gate geçerdi)
        # exec_adj = 0.024 - 0.005        = 0.019  (< threshold=0.022 → yeni gate reddeder)
        cfg = CalibrationConfig(
            min_execution_adjusted_edge=0.022,
            assumed_slippage_pct=0.005,
        )
        raw = _raw(predicted_class="UP", confidence=0.72)
        cal, err = _map(raw, up=0.72, down=0.18, quality=CalibrationQuality.STRONG)
        assert err is None

        # ask_no gerçekçi tutularak binary sanity geçilir: ask_yes+ask_no ∈ [0.85,1.15]
        pr = _pricing(ask_yes=0.686, bid_yes=0.680, ask_no=0.32, bid_no=0.30)
        d  = decide(cal, pr, cfg, now_utc=_NOW)

        assert d.decision == TradeDecisionType.REJECT
        assert d.rejection_reason == CalibrationRejectionReason.NEGATIVE_EDGE
        # net_ev = 0.72 - 0.686 - 0.01 = 0.024 (threshold=0.022'yi teorik geçer)
        assert d.edge_estimate.net_expected_value == pytest.approx(0.024, abs=1e-3)
        # executable_ev realistic slippage dahil — threshold altında kalmalı
        assert d.edge_estimate.execution_adjusted_ev < cfg.min_execution_adjusted_edge
