"""
signal_bridge/tests/test_signal_bridge.py

signal_router.py ve trade_filter.py için birim testler + uçtan uca bridge testi.

Kapsam:
- route(): yönlendirme tablosu (UP+NORMAL→YES, UP+INVERTED→NO, DOWN+NORMAL→NO, DOWN+INVERTED→YES)
- route(): REJECT senaryoları (NO_TRADE, rejected match, AMBIGUOUS)
- apply_filters_with_candidate(): confidence, liquidity, spread, edge filtreleri
- Uçtan uca: signal → match → route → filter → TradeIntent
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta

from signal_bridge.bridge_config import BridgeConfig
from signal_bridge.market_matcher import best_match
from signal_bridge.signal_router import route
from signal_bridge.trade_filter import apply_filters_with_candidate
from signal_bridge.types import (
    Direction,
    DirectionalSignal,
    MarketMatchResult,
    Polarity,
    PolymarketCandidate,
    RejectionReason,
    TradeSide,
    TradeIntent,
)


# ── Yardımcı ─────────────────────────────────────────────────────────────────

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


def _signal(
    asset="BTC",
    horizon=15,
    direction=Direction.UP,
    confidence=0.70,
):
    return DirectionalSignal(
        asset=asset,
        horizon_minutes=horizon,
        timestamp_utc=_NOW,
        direction=direction,
        confidence=confidence,
    )


def _candidate(
    title="Bitcoin Up or Down - March 15, 12:10PM-12:25PM ET",
    description="",
    minutes_to_end=10,
    status="active",
    liquidity=5000.0,
    best_ask_yes=0.45,
    best_bid_yes=0.43,
    best_ask_no=0.57,
    best_bid_no=0.55,
    market_id="mkt-001",
):
    end = _NOW + timedelta(minutes=minutes_to_end)
    return PolymarketCandidate(
        market_id=market_id,
        title=title,
        description=description,
        end_time_utc=end,
        yes_token_id="yes-001",
        no_token_id="no-001",
        best_bid_yes=best_bid_yes,
        best_ask_yes=best_ask_yes,
        best_bid_no=best_bid_no,
        best_ask_no=best_ask_no,
        volume=10_000.0,
        liquidity=liquidity,
        status=status,
    )


def _valid_match(
    polarity=Polarity.NORMAL,
    candidate=None,
    tte=600,
    score=0.8,
):
    if candidate is None:
        candidate = _candidate()
    return MarketMatchResult(
        candidate=candidate,
        matched_asset="BTC",
        polarity=polarity,
        time_to_resolution_sec=tte,
        match_score=score,
        rejection_reason=None,
    )


def _rejected_match(reason=RejectionReason.ASSET_MISMATCH):
    return MarketMatchResult(
        candidate=_candidate(),
        matched_asset="",
        polarity=Polarity.AMBIGUOUS,
        time_to_resolution_sec=0,
        match_score=0.0,
        rejection_reason=reason,
    )


# ── route() — Yönlendirme tablosu ─────────────────────────────────────────────

class TestRouteDirectionMapping:
    def test_up_normal_yields_yes(self):
        sig = _signal(direction=Direction.UP)
        match = _valid_match(polarity=Polarity.NORMAL)
        intent = route(sig, match)
        assert intent.mapped_side == TradeSide.YES

    def test_up_inverted_yields_no(self):
        sig = _signal(direction=Direction.UP)
        match = _valid_match(polarity=Polarity.INVERTED)
        intent = route(sig, match)
        assert intent.mapped_side == TradeSide.NO

    def test_down_normal_yields_no(self):
        sig = _signal(direction=Direction.DOWN)
        match = _valid_match(polarity=Polarity.NORMAL)
        intent = route(sig, match)
        assert intent.mapped_side == TradeSide.NO

    def test_down_inverted_yields_yes(self):
        sig = _signal(direction=Direction.DOWN)
        match = _valid_match(polarity=Polarity.INVERTED)
        intent = route(sig, match)
        assert intent.mapped_side == TradeSide.YES


class TestRouteTokenId:
    def test_yes_side_uses_yes_token(self):
        c = _candidate()
        sig = _signal(direction=Direction.UP)
        match = _valid_match(polarity=Polarity.NORMAL, candidate=c)
        intent = route(sig, match)
        assert intent.token_id == c.yes_token_id

    def test_no_side_uses_no_token(self):
        c = _candidate()
        sig = _signal(direction=Direction.DOWN)
        match = _valid_match(polarity=Polarity.NORMAL, candidate=c)
        intent = route(sig, match)
        assert intent.token_id == c.no_token_id

    def test_yes_ask_price_set(self):
        c = _candidate(best_ask_yes=0.42)
        sig = _signal(direction=Direction.UP)
        match = _valid_match(polarity=Polarity.NORMAL, candidate=c)
        intent = route(sig, match)
        assert intent.ask_price == pytest.approx(0.42)

    def test_no_ask_price_set(self):
        c = _candidate(best_ask_no=0.58)
        sig = _signal(direction=Direction.DOWN)
        match = _valid_match(polarity=Polarity.NORMAL, candidate=c)
        intent = route(sig, match)
        assert intent.ask_price == pytest.approx(0.58)


class TestRouteRejections:
    def test_no_trade_signal_rejected(self):
        sig = _signal(direction=Direction.NO_TRADE)
        match = _valid_match()
        intent = route(sig, match)
        assert intent.mapped_side == TradeSide.REJECT
        assert intent.rejection_reason == RejectionReason.NO_TRADE_SIGNAL

    def test_rejected_match_propagates(self):
        sig = _signal()
        match = _rejected_match(RejectionReason.ASSET_MISMATCH)
        intent = route(sig, match)
        assert intent.mapped_side == TradeSide.REJECT
        assert intent.rejection_reason == RejectionReason.ASSET_MISMATCH

    def test_ambiguous_polarity_rejected(self):
        sig = _signal()
        match = _valid_match(polarity=Polarity.AMBIGUOUS)
        # _valid_match AMBIGUOUS polarity ile ama rejection_reason=None → route yakalamalı
        intent = route(sig, match)
        assert intent.mapped_side == TradeSide.REJECT
        assert intent.rejection_reason == RejectionReason.AMBIGUOUS_WORDING

    def test_market_id_preserved_on_reject(self):
        sig = _signal(direction=Direction.NO_TRADE)
        c = _candidate(market_id="mkt-xyz")
        match = _valid_match(candidate=c)
        intent = route(sig, match)
        assert intent.market_id == "mkt-xyz"

    def test_confidence_preserved(self):
        sig = _signal(confidence=0.65)
        match = _valid_match()
        intent = route(sig, match)
        assert intent.confidence == pytest.approx(0.65)


class TestRouteRationale:
    def test_rationale_contains_direction(self):
        sig = _signal(direction=Direction.UP)
        match = _valid_match(polarity=Polarity.NORMAL)
        intent = route(sig, match)
        assert "UP" in intent.rationale

    def test_rationale_contains_polarity(self):
        sig = _signal()
        match = _valid_match(polarity=Polarity.NORMAL)
        intent = route(sig, match)
        assert "NORMAL" in intent.rationale

    def test_rationale_contains_yes_or_no(self):
        sig = _signal(direction=Direction.UP)
        match = _valid_match(polarity=Polarity.NORMAL)
        intent = route(sig, match)
        assert "YES" in intent.rationale


# ── apply_filters_with_candidate() ───────────────────────────────────────────

class TestApplyFilters:
    def setup_method(self):
        self.cfg = BridgeConfig()

    def _intent(self, sig=None, ask_price=0.45, market_id="mkt-001"):
        if sig is None:
            sig = _signal()
        c = _candidate()
        m = _valid_match(candidate=c)
        i = route(sig, m)
        # ask_price'ı override et
        from dataclasses import replace
        return replace(i, ask_price=ask_price), c

    def test_all_pass_returns_yes(self):
        intent, c = self._intent()
        result = apply_filters_with_candidate(intent, c, self.cfg)
        assert result.mapped_side == TradeSide.YES

    def test_all_pass_edge_computed(self):
        # edge = conf - ask - fee = 0.70 - 0.45 - 0.01 = 0.24
        intent, c = self._intent()
        result = apply_filters_with_candidate(intent, c, self.cfg)
        assert result.expected_edge == pytest.approx(0.70 - 0.45 - 0.01, abs=1e-6)

    def test_low_confidence_rejected(self):
        sig = _signal(confidence=0.50)  # < 0.58 min
        intent, c = self._intent(sig=sig)
        result = apply_filters_with_candidate(intent, c, self.cfg)
        assert result.mapped_side == TradeSide.REJECT
        assert result.rejection_reason == RejectionReason.LOW_CONFIDENCE

    def test_low_liquidity_rejected(self):
        c = _candidate(liquidity=500.0)  # < 1000 min
        m = _valid_match(candidate=c)
        intent = route(_signal(), m)
        result = apply_filters_with_candidate(intent, c, self.cfg)
        assert result.mapped_side == TradeSide.REJECT
        assert result.rejection_reason == RejectionReason.LOW_LIQUIDITY

    def test_high_spread_yes_side_rejected(self):
        # YES intent: YES-spread = ask_yes - bid_yes = 0.15 > 0.05
        c = _candidate(best_ask_yes=0.55, best_bid_yes=0.40)
        m = _valid_match(polarity=Polarity.NORMAL, candidate=c)
        intent = route(_signal(direction=Direction.UP), m)   # UP+NORMAL → YES
        assert intent.mapped_side == TradeSide.YES
        result = apply_filters_with_candidate(intent, c, self.cfg)
        assert result.mapped_side == TradeSide.REJECT
        assert result.rejection_reason == RejectionReason.HIGH_SPREAD
        assert "YES-spread" in result.rationale

    def test_high_spread_no_side_rejected(self):
        # NO intent: NO-spread = ask_no - bid_no = 0.15 > 0.05
        # YES spread dar olsa bile NO-spread geniş ise reject
        c = _candidate(
            best_ask_yes=0.44, best_bid_yes=0.42,   # YES-spread=0.02 (dar)
            best_ask_no=0.60,  best_bid_no=0.45,    # NO-spread=0.15 (geniş)
        )
        m = _valid_match(polarity=Polarity.NORMAL, candidate=c)
        intent = route(_signal(direction=Direction.DOWN), m)  # DOWN+NORMAL → NO
        assert intent.mapped_side == TradeSide.NO
        result = apply_filters_with_candidate(intent, c, self.cfg)
        assert result.mapped_side == TradeSide.REJECT
        assert result.rejection_reason == RejectionReason.HIGH_SPREAD
        assert "NO-spread" in result.rationale

    def test_yes_spread_ok_no_spread_ok_passes(self):
        # Her iki taraf da dar spread → geçer
        c = _candidate(
            best_ask_yes=0.44, best_bid_yes=0.43,
            best_ask_no=0.57,  best_bid_no=0.56,
        )
        m = _valid_match(polarity=Polarity.NORMAL, candidate=c)
        intent = route(_signal(direction=Direction.UP, confidence=0.72), m)
        result = apply_filters_with_candidate(intent, c, self.cfg)
        assert result.mapped_side == TradeSide.YES

    def test_negative_edge_rejected(self):
        # conf=0.60, ask=0.58, fee=0.01 → edge=0.01 < 0.02 min
        sig = _signal(confidence=0.60)
        c = _candidate(best_ask_yes=0.58, best_bid_yes=0.54)
        m = _valid_match(candidate=c)
        intent = route(sig, m)
        result = apply_filters_with_candidate(intent, c, self.cfg)
        assert result.mapped_side == TradeSide.REJECT
        assert result.rejection_reason == RejectionReason.NEGATIVE_EDGE

    def test_already_rejected_intent_passes_through(self):
        sig = _signal(direction=Direction.NO_TRADE)
        c = _candidate()
        m = _valid_match(candidate=c)
        intent = route(sig, m)
        # REJECT intentini filtreye ver — dokunmadan döner
        result = apply_filters_with_candidate(intent, c, self.cfg)
        assert result.mapped_side == TradeSide.REJECT
        assert result.rejection_reason == RejectionReason.NO_TRADE_SIGNAL

    def test_confidence_boundary_exact_min_passes(self):
        sig = _signal(confidence=0.58)  # tam eşik = geçer (>=)
        c = _candidate(best_ask_yes=0.35, best_bid_yes=0.33)  # yeterli edge için
        m = _valid_match(candidate=c)
        intent = route(sig, m)
        result = apply_filters_with_candidate(intent, c, self.cfg)
        # edge = 0.58 - 0.35 - 0.01 = 0.22 ≥ 0.02 → geçer
        assert result.mapped_side == TradeSide.YES

    def test_rationale_updated_on_pass(self):
        intent, c = self._intent()
        result = apply_filters_with_candidate(intent, c, self.cfg)
        assert "edge=" in result.rationale
        assert "liq=" in result.rationale


# ── Uçtan uca bridge testi ────────────────────────────────────────────────────

class TestEndToEndBridge:
    """
    signal → best_match → route → apply_filters_with_candidate
    """

    def setup_method(self):
        self.cfg = BridgeConfig()

    def test_full_pipeline_up_normal_to_yes(self):
        sig = _signal(direction=Direction.UP, confidence=0.72)
        c = _candidate(
            title="Bitcoin Up or Down - March 15, 12:10PM-12:25PM ET",
            minutes_to_end=10,
            best_ask_yes=0.44,
            best_bid_yes=0.42,
            liquidity=8000.0,
        )
        match = best_match(sig, [c], now_utc=_NOW, config=self.cfg)
        assert match is not None

        intent = route(sig, match)
        assert intent.mapped_side == TradeSide.YES

        final = apply_filters_with_candidate(intent, match.candidate, self.cfg)
        assert final.mapped_side == TradeSide.YES
        assert final.expected_edge is not None
        assert final.expected_edge > 0

    def test_full_pipeline_down_normal_to_no(self):
        sig = _signal(direction=Direction.DOWN, confidence=0.72)
        c = _candidate(minutes_to_end=10)
        match = best_match(sig, [c], now_utc=_NOW, config=self.cfg)
        assert match is not None
        intent = route(sig, match)
        final = apply_filters_with_candidate(intent, match.candidate, self.cfg)
        assert final.mapped_side == TradeSide.NO

    def test_full_pipeline_no_trade_rejects(self):
        sig = _signal(direction=Direction.NO_TRADE)
        c = _candidate(minutes_to_end=10)
        match = best_match(sig, [c], now_utc=_NOW, config=self.cfg)
        # NO_TRADE sinyali için best_match sonucu önemli değil —
        # route zaten REJECT edecek
        if match is not None:
            intent = route(sig, match)
            assert intent.mapped_side == TradeSide.REJECT
            assert intent.rejection_reason == RejectionReason.NO_TRADE_SIGNAL
        # best_match None dönebilir (NO_TRADE ile eşleşmiyor olabilir) — her iki durum geçerli

    def test_full_pipeline_no_candidate_returns_none(self):
        sig = _signal()
        match = best_match(sig, [], now_utc=_NOW, config=self.cfg)
        assert match is None

    def test_eth_signal_matches_eth_market(self):
        sig = _signal(asset="ETH", direction=Direction.UP, confidence=0.75)
        c = _candidate(
            title="Ethereum Up or Down - March 15, 12:10PM-12:25PM ET",
            minutes_to_end=8,
            best_ask_yes=0.42,
            best_bid_yes=0.40,
            liquidity=6000.0,
        )
        match = best_match(sig, [c], now_utc=_NOW, config=self.cfg)
        assert match is not None
        assert match.matched_asset == "ETH"

        intent = route(sig, match)
        final = apply_filters_with_candidate(intent, match.candidate, self.cfg)
        assert final.mapped_side == TradeSide.YES
