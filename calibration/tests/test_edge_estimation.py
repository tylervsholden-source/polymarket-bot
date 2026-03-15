"""
calibration/tests/test_edge_estimation.py

edge_estimator.py ve decision_policy.py birim testleri.

Kapsam:
- YES ve NO tarafı edge formülleri
- Side-aware spread kontrolü
- Maliyet sonrası edge azalması
- Yüksek güven ama zayıf net edge → REJECT
- Gross pozitif ama fee sonrası negatif → REJECT
- Stale pricing → REJECT
- Ambiguous mapping → probability_mapper engeller (kalibrasyon testine ait)
- Likidite → REJECT
- Reject on weak calibration flag
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta

from calibration.decision_policy import decide
from calibration.edge_estimator import estimate_both_edges, estimate_no_edge, estimate_yes_edge
from calibration.probability_mapper import map_to_event_probability
from calibration.types import (
    CalibrationConfig,
    CalibrationMethod,
    CalibrationQuality,
    CalibrationRejectionReason,
    MarketPricingSnapshot,
    RawSignalOutput,
    TradeDecisionType,
)

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


# ── Yardımcılar ───────────────────────────────────────────────────────────────

def _raw(predicted_class="UP", confidence=0.72):
    return RawSignalOutput(
        asset="BTC",
        horizon_minutes=15,
        timestamp_utc=_NOW,
        predicted_class=predicted_class,
        raw_confidence=confidence,
    )


def _pricing(
    ask_yes=0.44,
    bid_yes=0.42,
    ask_no=0.57,
    bid_no=0.55,
    liquidity=5000.0,
    age_seconds=0,
):
    ts = _NOW - timedelta(seconds=age_seconds)
    return MarketPricingSnapshot(
        market_id="mkt-001",
        ask_yes=ask_yes,
        bid_yes=bid_yes,
        ask_no=ask_no,
        bid_no=bid_no,
        liquidity=liquidity,
        spread_yes=round(ask_yes - bid_yes, 6),
        spread_no=round(ask_no - bid_no, 6),
        timestamp_utc=ts,
    )


def _cal_signal(
    up=0.72, down=0.18, no_trade=0.10, polarity="NORMAL",
    predicted_class="UP", quality=CalibrationQuality.UNKNOWN,
):
    raw = _raw(predicted_class=predicted_class, confidence=up)
    cal, err = map_to_event_probability(
        raw=raw,
        calibrated_up_prob=up,
        calibrated_down_prob=down,
        calibrated_no_trade_prob=no_trade,
        polarity=polarity,
        calibration_method=CalibrationMethod.IDENTITY,
        calibration_quality=quality,
    )
    assert err is None, f"Yardımcı başarısız: {err}"
    return cal


_CFG = CalibrationConfig()


# ── estimate_yes_edge ─────────────────────────────────────────────────────────

class TestEstimateYesEdge:
    def test_gross_ev_formula(self):
        # gross_EV = p - q = 0.72 - 0.44 = 0.28
        cal = _cal_signal(up=0.72)
        pr  = _pricing(ask_yes=0.44)
        e   = estimate_yes_edge(cal, pr, _CFG)
        assert e.gross_expected_value == pytest.approx(0.72 - 0.44, abs=1e-6)

    def test_net_ev_formula(self):
        # net_EV = gross - fee = 0.28 - 0.01 = 0.27
        cal = _cal_signal(up=0.72)
        pr  = _pricing(ask_yes=0.44)
        e   = estimate_yes_edge(cal, pr, _CFG)
        assert e.net_expected_value == pytest.approx(0.72 - 0.44 - 0.01, abs=1e-6)

    def test_edge_equals_execution_adjusted_ev(self):
        # expected_edge = execution_adjusted_ev (slippage dahil karar metriği)
        cal = _cal_signal(up=0.65)
        pr  = _pricing(ask_yes=0.50)
        e   = estimate_yes_edge(cal, pr, _CFG)
        assert e.expected_edge == pytest.approx(e.execution_adjusted_ev)

    def test_passes_gate_when_above_threshold(self):
        # edge = 0.72 - 0.44 - 0.01 = 0.27 > 0.02
        cal = _cal_signal(up=0.72)
        e   = estimate_yes_edge(cal, _pricing(ask_yes=0.44), _CFG)
        assert e.passes_edge_gate is True

    def test_fails_gate_when_below_threshold(self):
        # edge = 0.62 - 0.61 - 0.01 = 0.00 < 0.02
        cal = _cal_signal(up=0.62)
        e   = estimate_yes_edge(cal, _pricing(ask_yes=0.61), _CFG)
        assert e.passes_edge_gate is False

    def test_side_is_yes(self):
        cal = _cal_signal()
        e   = estimate_yes_edge(cal, _pricing(), _CFG)
        assert e.side_considered == "YES"

    def test_uses_ask_yes_not_ask_no(self):
        cal = _cal_signal(up=0.72)
        pr  = _pricing(ask_yes=0.44, ask_no=0.58)  # farklı fiyatlar
        e   = estimate_yes_edge(cal, pr, _CFG)
        # giriş fiyatı ask_yes olmalı
        assert e.market_entry_price == pytest.approx(0.44)

    def test_high_confidence_but_price_close_gives_low_edge(self):
        # Senaryo 4: conf=0.85, ask=0.84
        # expected_edge = 0.85 - 0.84 - 0.01 - 0.005 = -0.005 → fails gate
        cal = _cal_signal(up=0.85, down=0.05, no_trade=0.05)  # sum=0.95 ✓
        e   = estimate_yes_edge(cal, _pricing(ask_yes=0.84, bid_yes=0.82), _CFG)
        assert e.expected_edge == pytest.approx(0.85 - 0.84 - 0.01 - 0.005, abs=1e-6)
        assert e.passes_edge_gate is False


# ── estimate_no_edge ──────────────────────────────────────────────────────────

class TestEstimateNoEdge:
    def test_gross_ev_formula_no(self):
        # DOWN + NORMAL → eff_no = down_prob
        # gross_EV = p_no - ask_no
        cal = _cal_signal(up=0.20, down=0.65, predicted_class="DOWN")
        pr  = _pricing(ask_no=0.45)
        e   = estimate_no_edge(cal, pr, _CFG)
        # eff_no_prob = down_prob = 0.65 (DOWN + NORMAL)
        assert e.gross_expected_value == pytest.approx(0.65 - 0.45, abs=1e-6)

    def test_net_ev_includes_cost(self):
        cal = _cal_signal(up=0.20, down=0.65, predicted_class="DOWN")
        pr  = _pricing(ask_no=0.45)
        e   = estimate_no_edge(cal, pr, _CFG)
        assert e.net_expected_value == pytest.approx(0.65 - 0.45 - 0.01, abs=1e-6)

    def test_side_is_no(self):
        cal = _cal_signal(up=0.20, down=0.65, predicted_class="DOWN")
        e   = estimate_no_edge(cal, _pricing(), _CFG)
        assert e.side_considered == "NO"

    def test_uses_ask_no_not_ask_yes(self):
        cal = _cal_signal(up=0.20, down=0.65, predicted_class="DOWN")
        pr  = _pricing(ask_yes=0.44, ask_no=0.57)
        e   = estimate_no_edge(cal, pr, _CFG)
        assert e.market_entry_price == pytest.approx(0.57)

    def test_no_side_spread_in_diagnostics(self):
        cal = _cal_signal(up=0.20, down=0.65, predicted_class="DOWN")
        pr  = _pricing(ask_no=0.57, bid_no=0.53)
        e   = estimate_no_edge(cal, pr, _CFG)
        assert "spread_no" in e.diagnostics


# ── Cost-aware edge ──────────────────────────────────────────────────────────

class TestCostAwareEdge:
    def test_gross_positive_net_negative_fails(self):
        # gross = 0.62 - 0.61 = 0.01 > 0 ama net = 0.01 - 0.01 = 0.00 < 0.02
        cal = _cal_signal(up=0.62)
        e   = estimate_yes_edge(cal, _pricing(ask_yes=0.61), _CFG)
        assert e.gross_expected_value > 0
        assert e.passes_edge_gate is False

    def test_zero_fee_higher_edge(self):
        # Fee sıfır olsa edge daha yüksek
        cfg_no_fee = CalibrationConfig(assumed_taker_fee_pct=0.0)
        cal = _cal_signal(up=0.62)
        e_with_fee    = estimate_yes_edge(cal, _pricing(ask_yes=0.61), _CFG)
        e_without_fee = estimate_yes_edge(cal, _pricing(ask_yes=0.61), cfg_no_fee)
        assert e_without_fee.expected_edge > e_with_fee.expected_edge

    def test_custom_fee_reduces_edge(self):
        # expected_edge = execution_adjusted_ev = p - q - fee - slippage
        cfg_high_fee = CalibrationConfig(assumed_taker_fee_pct=0.05, assumed_slippage_pct=0.005)
        cal = _cal_signal(up=0.72)
        e   = estimate_yes_edge(cal, _pricing(ask_yes=0.44), cfg_high_fee)
        # edge = 0.72 - 0.44 - 0.05 - 0.005 = 0.225
        assert e.expected_edge == pytest.approx(0.72 - 0.44 - 0.05 - 0.005, abs=1e-6)


# ── estimate_both_edges ───────────────────────────────────────────────────────

class TestEstimateBothEdges:
    def test_returns_yes_first_no_second(self):
        # Tuple sırası: (yes_edge, no_edge) — sıra tersine dönerse sessiz bug
        cal = _cal_signal(up=0.72, down=0.18)
        yes_edge, no_edge = estimate_both_edges(cal, _pricing(), _CFG)
        assert yes_edge.side_considered == "YES"
        assert no_edge.side_considered == "NO"

    def test_yes_uses_ask_yes_no_uses_ask_no(self):
        cal = _cal_signal(up=0.72, down=0.18, predicted_class="UP")  # sum=1.0 ✓
        pr  = _pricing(ask_yes=0.44, ask_no=0.57)
        yes_edge, no_edge = estimate_both_edges(cal, pr, _CFG)
        assert yes_edge.market_entry_price == pytest.approx(0.44)
        assert no_edge.market_entry_price  == pytest.approx(0.57)


# ── DecisionPolicy ────────────────────────────────────────────────────────────

class TestDecisionPolicy:
    def test_execute_yes_on_positive_edge(self):
        # UP + NORMAL + strong edge
        cal = _cal_signal(up=0.72, down=0.18)
        pr  = _pricing(ask_yes=0.44, ask_no=0.57)
        d   = decide(cal, pr, _CFG, now_utc=_NOW)
        assert d.decision == TradeDecisionType.EXECUTE_YES

    def test_execute_no_on_down_signal(self):
        # NORMAL polarity: eff_yes=up_prob=0.20, eff_no=down_prob=0.65
        # YES edge: 0.20 - 0.44 - 0.01 = -0.25 ✗
        # NO edge:  0.65 - 0.57 - 0.01 =  0.07 ✓
        # → EXECUTE_NO
        cal = _cal_signal(up=0.20, down=0.65, predicted_class="DOWN")
        pr  = _pricing(ask_yes=0.44, ask_no=0.57)
        d   = decide(cal, pr, _CFG, now_utc=_NOW)
        assert d.decision == TradeDecisionType.EXECUTE_NO

    def test_reject_when_bridge_intent_edge_negative(self):
        # UP + NORMAL → intent=YES. eff_yes=0.57 (>= min_conf), ask=0.57 → edge=-0.01 < 0.02
        # bid values kept low so bid_sum << 1.0 (avoids Phase 12 hard bid_sum guard)
        cal = _cal_signal(up=0.57, down=0.30, no_trade=0.13, predicted_class="UP")
        pr  = _pricing(ask_yes=0.57, bid_yes=0.53, ask_no=0.52, bid_no=0.48)
        d   = decide(cal, pr, _CFG, now_utc=_NOW)
        assert d.decision == TradeDecisionType.REJECT
        assert d.rejection_reason == CalibrationRejectionReason.NEGATIVE_EDGE

    def test_reject_stale_pricing(self):
        cal = _cal_signal(up=0.72)
        pr  = _pricing(age_seconds=400)  # > 300s max
        d   = decide(cal, pr, _CFG, now_utc=_NOW)
        assert d.decision == TradeDecisionType.REJECT
        assert d.rejection_reason == CalibrationRejectionReason.STALE_PRICING

    def test_fresh_pricing_not_stale(self):
        cal = _cal_signal(up=0.72)
        pr  = _pricing(age_seconds=60)  # < 300s
        d   = decide(cal, pr, _CFG, now_utc=_NOW)
        assert d.decision != TradeDecisionType.REJECT or \
               d.rejection_reason != CalibrationRejectionReason.STALE_PRICING

    def test_reject_low_liquidity(self):
        cal = _cal_signal(up=0.72)
        pr  = _pricing(liquidity=500.0)  # < 1000 min
        d   = decide(cal, pr, _CFG, now_utc=_NOW)
        assert d.decision == TradeDecisionType.REJECT
        assert d.rejection_reason == CalibrationRejectionReason.LOW_LIQUIDITY

    def test_reject_high_yes_spread(self):
        # YES spread geniş → YES tarafı reddedilmeli
        # NO spread dar ama eff_no düşük → NO tarafı da reddedilmeli
        cal = _cal_signal(up=0.72, down=0.10)  # eff_no=0.10 < min_conf=0.55
        pr  = _pricing(ask_yes=0.55, bid_yes=0.40)  # YES spread=0.15 > 0.05
        d   = decide(cal, pr, _CFG, now_utc=_NOW)
        # eff_yes_prob=0.72, ask_yes=0.55, edge=0.16 ama spread fazla → reject YES
        # eff_no_prob=0.10 < min_conf → reject NO
        assert d.decision == TradeDecisionType.REJECT

    def test_reject_on_weak_calibration_flag(self):
        cfg = CalibrationConfig(reject_on_weak_calibration=True)
        cal = _cal_signal(up=0.72, quality=CalibrationQuality.WEAK)
        pr  = _pricing()
        d   = decide(cal, pr, cfg, now_utc=_NOW)
        assert d.decision == TradeDecisionType.REJECT
        assert d.rejection_reason == CalibrationRejectionReason.WEAK_CALIBRATION

    def test_no_reject_on_weak_calibration_when_flag_false(self):
        cfg = CalibrationConfig(reject_on_weak_calibration=False)
        cal = _cal_signal(up=0.72, quality=CalibrationQuality.WEAK)
        pr  = _pricing()
        d   = decide(cal, pr, cfg, now_utc=_NOW)
        # Zayıf kalibrasyon ama flag=False → devam eder
        assert d.rejection_reason != CalibrationRejectionReason.WEAK_CALIBRATION

    def test_edge_estimate_in_decision(self):
        cal = _cal_signal(up=0.72)
        d   = decide(cal, _pricing(), _CFG, now_utc=_NOW)
        assert d.edge_estimate is not None

    def test_calibrated_signal_in_decision(self):
        cal = _cal_signal(up=0.72)
        d   = decide(cal, _pricing(), _CFG, now_utc=_NOW)
        assert d.calibrated_signal is not None

    def test_rationale_is_not_empty(self):
        cal = _cal_signal(up=0.72)
        d   = decide(cal, _pricing(), _CFG, now_utc=_NOW)
        assert len(d.rationale) > 10

    def test_picks_higher_edge_side(self):
        # NORMAL: eff_yes=up_prob=0.72, eff_no=down_prob=0.18
        # YES edge pozitif ve büyük → EXECUTE_YES beklenir.
        # (Exact value execution_realism slippage modeline bağlı — sadece direction test edilir.)
        cal = _cal_signal(up=0.72, down=0.18, no_trade=0.10)  # sum=1.0 ✓
        pr  = _pricing(ask_yes=0.44, ask_no=0.50, bid_no=0.48)
        d = decide(cal, pr, _CFG, now_utc=_NOW)
        assert d.decision == TradeDecisionType.EXECUTE_YES
        assert d.edge_estimate.expected_edge > 0.20  # büyük pozitif edge beklenir

    def test_decision_deterministic(self):
        cal = _cal_signal(up=0.72)
        pr  = _pricing()
        d1  = decide(cal, pr, _CFG, now_utc=_NOW)
        d2  = decide(cal, pr, _CFG, now_utc=_NOW)
        assert d1.decision == d2.decision
        assert d1.rationale == d2.rationale

    # ── Bridge-intent-only ────────────────────────────────────────────────────

    def test_bridge_intent_does_not_flip_to_opposite_side(self):
        # UP + NORMAL → intent=YES. YES edge negatif → REJECT.
        # NO tarafı ucuz olsa bile sistem geçmez — bridge intent izlenir.
        cal = _cal_signal(up=0.57, down=0.30, no_trade=0.13, predicted_class="UP")
        pr  = _pricing(ask_yes=0.57, bid_yes=0.55, ask_no=0.50)
        d   = decide(cal, pr, _CFG, now_utc=_NOW)
        assert d.decision == TradeDecisionType.REJECT

    def test_bridge_intent_yes_side_selected(self):
        assert _cal_signal(up=0.72, predicted_class="UP").bridge_intent_side == "YES"

    def test_bridge_intent_no_side_selected(self):
        assert _cal_signal(up=0.20, down=0.65, predicted_class="DOWN").bridge_intent_side == "NO"

    def test_bridge_intent_inverted_up_is_no(self):
        cal = _cal_signal(up=0.72, down=0.18, predicted_class="UP", polarity="INVERTED")
        assert cal.bridge_intent_side == "NO"

    def test_bridge_intent_inverted_down_is_yes(self):
        cal = _cal_signal(up=0.20, down=0.65, predicted_class="DOWN", polarity="INVERTED")
        assert cal.bridge_intent_side == "YES"

    # ── UNKNOWN calibration policy ────────────────────────────────────────────

    def test_reject_on_unknown_calibration_when_flag_set(self):
        cfg = CalibrationConfig(reject_on_unknown_calibration=True)
        cal = _cal_signal(up=0.72, quality=CalibrationQuality.UNKNOWN)
        d   = decide(cal, _pricing(), cfg, now_utc=_NOW)
        assert d.decision == TradeDecisionType.REJECT
        assert d.rejection_reason == CalibrationRejectionReason.UNKNOWN_CALIBRATION

    def test_no_reject_on_unknown_when_flag_false(self):
        cfg = CalibrationConfig(reject_on_unknown_calibration=False)
        cal = _cal_signal(up=0.72, quality=CalibrationQuality.UNKNOWN)
        d   = decide(cal, _pricing(), cfg, now_utc=_NOW)
        assert d.rejection_reason != CalibrationRejectionReason.UNKNOWN_CALIBRATION

    # ── Execution-adjusted EV ────────────────────────────────────────────────

    def test_passes_gate_uses_execution_adjusted_not_net_ev(self):
        # net_ev = 0.72 - 0.686 - 0.01 = 0.024  (> min_edge=0.022 → eski gate geçerdi)
        # exec_adj = 0.024 - 0.005 = 0.019       (< min_edge=0.022 → yeni gate reddeder)
        cfg = CalibrationConfig(min_execution_adjusted_edge=0.022, assumed_slippage_pct=0.005)
        cal = _cal_signal(up=0.72)
        e   = estimate_yes_edge(cal, _pricing(ask_yes=0.686), cfg)
        assert e.net_expected_value == pytest.approx(0.024, abs=1e-3)
        assert e.execution_adjusted_ev == pytest.approx(0.019, abs=1e-3)
        assert e.passes_edge_gate is False  # exec_adj < threshold

    def test_execution_adjusted_ev_in_edge_estimate(self):
        # net_ev = 0.72 - 0.44 - 0.01 = 0.27; exec_adj = 0.27 - 0.005 = 0.265
        cfg = CalibrationConfig(assumed_slippage_pct=0.005)
        cal = _cal_signal(up=0.72)
        e   = estimate_yes_edge(cal, _pricing(ask_yes=0.44), cfg)
        assert e.execution_adjusted_ev == pytest.approx(0.72 - 0.44 - 0.01 - 0.005, abs=1e-6)

    def test_execution_adjusted_ev_less_than_net_ev(self):
        cal = _cal_signal(up=0.72)
        e   = estimate_yes_edge(cal, _pricing(ask_yes=0.44), _CFG)
        assert e.execution_adjusted_ev < e.net_expected_value

    def test_execution_adjusted_ev_in_decision_rationale(self):
        cal = _cal_signal(up=0.72)
        d   = decide(cal, _pricing(), _CFG, now_utc=_NOW)
        assert "exec_adj_ev" in d.rationale
