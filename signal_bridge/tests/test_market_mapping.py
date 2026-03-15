"""
signal_bridge/tests/test_market_mapping.py

market_matcher.py için birim testler.

Kapsam:
- _match_asset: doğru asset, yanlış asset, ambiguous
- _detect_polarity: "Up or Down", normal keywords, inverted keywords, ambiguous
- _check_timing / _timing_score: window içi/dışı
- match_markets: sıralama, rejection filtreleri
- best_match: None döndürme
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta

from signal_bridge.bridge_config import BridgeConfig
from signal_bridge.market_matcher import (
    _match_asset,
    _detect_polarity,
    _check_timing,
    _timing_score,
    match_markets,
    best_match,
)
from signal_bridge.types import (
    Direction,
    DirectionalSignal,
    Polarity,
    PolymarketCandidate,
    RejectionReason,
)


# ── Yardımcı ─────────────────────────────────────────────────────────────────

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


def _signal(asset="BTC", horizon=15, direction=Direction.UP, confidence=0.70):
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
):
    end = _NOW + timedelta(minutes=minutes_to_end)
    return PolymarketCandidate(
        market_id="mkt-001",
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


# ── _match_asset ──────────────────────────────────────────────────────────────

class TestMatchAsset:
    def test_btc_match(self):
        assert _match_asset("bitcoin up or down", "BTC") == "BTC"

    def test_btc_keyword_btc(self):
        assert _match_asset("btc price prediction", "BTC") == "BTC"

    def test_eth_match(self):
        assert _match_asset("will ethereum go above", "ETH") == "ETH"

    def test_case_insensitive(self):
        # _match_asset text'i lowercase bekler; _evaluate içinde .lower() uygulanır
        assert _match_asset("bitcoin up or down", "BTC") == "BTC"

    def test_wrong_asset_returns_none(self):
        # ETH sinyali ama BTC marketi
        assert _match_asset("bitcoin up or down", "ETH") is None

    def test_ambiguous_two_assets_returns_none(self):
        # Hem bitcoin hem ethereum — ambiguous
        assert _match_asset("bitcoin ethereum comparison", "BTC") is None

    def test_unknown_asset_keyword_fallback(self):
        # Bilinmeyen asset → kendi lowercase'ini ara
        assert _match_asset("ada up or down", "ADA") == "ADA"

    def test_no_keyword_returns_none(self):
        assert _match_asset("gold price prediction", "BTC") is None

    def test_sol_match(self):
        assert _match_asset("solana up or down", "SOL") == "SOL"

    def test_xrp_ripple_match(self):
        assert _match_asset("ripple (xrp) prediction", "XRP") == "XRP"


# ── _detect_polarity ──────────────────────────────────────────────────────────

class TestDetectPolarity:
    def test_up_or_down_normal(self):
        assert _detect_polarity("Bitcoin Up or Down - March 15") == Polarity.NORMAL

    def test_up_or_down_case_insensitive(self):
        assert _detect_polarity("Bitcoin UP OR DOWN - March 15") == Polarity.NORMAL

    def test_above_normal(self):
        assert _detect_polarity("Will BTC be above $80,000?") == Polarity.NORMAL

    def test_higher_normal(self):
        assert _detect_polarity("Will BTC trade higher by noon?") == Polarity.NORMAL

    def test_below_inverted(self):
        assert _detect_polarity("Will BTC fall below $70,000?") == Polarity.INVERTED

    def test_drop_inverted(self):
        assert _detect_polarity("Will BTC drop 5% today?") == Polarity.INVERTED

    def test_both_keywords_ambiguous(self):
        # "above" ve "below" ikisi birden → AMBIGUOUS
        assert _detect_polarity("Will BTC go above or below $80,000?") == Polarity.AMBIGUOUS

    def test_no_keyword_ambiguous(self):
        assert _detect_polarity("What will BTC do today?") == Polarity.AMBIGUOUS

    def test_up_or_down_overrides_below(self):
        # "up or down" içeriyorsa → NORMAL (öncelik)
        assert _detect_polarity("BTC up or down, below $80k?") == Polarity.NORMAL

    def test_rise_normal(self):
        assert _detect_polarity("Will ETH rise above 3000?") == Polarity.NORMAL

    def test_decline_inverted(self):
        assert _detect_polarity("Will SOL decline this week?") == Polarity.INVERTED


# ── _check_timing ──────────────────────────────────────────────────────────────

class TestCheckTiming:
    def setup_method(self):
        self.cfg = BridgeConfig()

    def test_within_window_15m(self):
        # 10 dakika → 600s → [120, 1800] içinde → None
        assert _check_timing(600, 15, self.cfg) is None

    def test_too_close_15m(self):
        # 1 dakika → 60s < 120 → TIMING_TOO_CLOSE
        assert _check_timing(60, 15, self.cfg) == RejectionReason.TIMING_TOO_CLOSE

    def test_too_far_15m(self):
        # 31 dakika → 1860s > 1800 → TIMING_TOO_FAR
        assert _check_timing(1860, 15, self.cfg) == RejectionReason.TIMING_TOO_FAR

    def test_within_window_5m(self):
        # 5 dakika → 300s → [60, 600] içinde → None
        assert _check_timing(300, 5, self.cfg) is None

    def test_too_close_5m(self):
        assert _check_timing(30, 5, self.cfg) == RejectionReason.TIMING_TOO_CLOSE

    def test_too_far_5m(self):
        assert _check_timing(700, 5, self.cfg) == RejectionReason.TIMING_TOO_FAR

    def test_unknown_horizon_rejects(self):
        # Bilinmeyen horizon → sessiz geçme değil, UNSUPPORTED_HORIZON ile reddet
        assert _check_timing(300, 30, self.cfg) == RejectionReason.UNSUPPORTED_HORIZON

    def test_exact_min_boundary(self):
        # Tam min değer → geçerli
        assert _check_timing(120, 15, self.cfg) is None

    def test_exact_max_boundary(self):
        # Tam max değer → geçerli
        assert _check_timing(1800, 15, self.cfg) is None


# ── _timing_score ──────────────────────────────────────────────────────────────

class TestTimingScore:
    def setup_method(self):
        self.cfg = BridgeConfig()

    def test_midpoint_scores_one(self):
        # 15m: min=120, max=1800 → midpoint=960
        score = _timing_score(960, 15, self.cfg)
        assert abs(score - 1.0) < 1e-6

    def test_boundary_scores_zero(self):
        score = _timing_score(120, 15, self.cfg)
        assert abs(score - 0.0) < 1e-6

    def test_score_between_zero_one(self):
        score = _timing_score(600, 15, self.cfg)
        assert 0.0 <= score <= 1.0

    def test_unknown_horizon_returns_half(self):
        score = _timing_score(300, 99, self.cfg)
        assert score == 0.5


# ── match_markets ─────────────────────────────────────────────────────────────

class TestMatchMarkets:
    def setup_method(self):
        self.cfg = BridgeConfig()
        self.sig = _signal()

    def test_valid_candidate_first(self):
        c = _candidate(minutes_to_end=10)
        results = match_markets(self.sig, [c], now_utc=_NOW, config=self.cfg)
        assert results[0].rejection_reason is None

    def test_inactive_market_rejected(self):
        c = _candidate(status="closed")
        results = match_markets(self.sig, [c], now_utc=_NOW, config=self.cfg)
        assert results[0].rejection_reason == RejectionReason.MARKET_INACTIVE

    def test_asset_mismatch_rejected(self):
        c = _candidate(title="Ethereum Up or Down - March 15, 12:10PM-12:25PM ET")
        results = match_markets(self.sig, [c], now_utc=_NOW, config=self.cfg)
        assert results[0].rejection_reason == RejectionReason.ASSET_MISMATCH

    def test_timing_too_far_rejected(self):
        c = _candidate(minutes_to_end=60)  # 3600s > 1800s max
        results = match_markets(self.sig, [c], now_utc=_NOW, config=self.cfg)
        assert results[0].rejection_reason == RejectionReason.TIMING_TOO_FAR

    def test_ambiguous_wording_rejected(self):
        # BTC marketi ama polarity tespit edilemiyor ("above"/"below" yok)
        c = _candidate(title="Bitcoin - what will happen today?", minutes_to_end=10)
        results = match_markets(self.sig, [c], now_utc=_NOW, config=self.cfg)
        assert results[0].rejection_reason == RejectionReason.AMBIGUOUS_WORDING

    def test_valid_sorted_by_score_desc(self):
        # İki geçerli candidate — daha iyi skorlu öne gelmeli
        c1 = _candidate(title="Bitcoin Up or Down - March 15, 12:10PM-12:25PM ET", minutes_to_end=10)
        c2 = _candidate(title="Bitcoin Up or Down - March 15, 12:12PM-12:27PM ET", minutes_to_end=20)
        # c1: 600s, c2: 1200s — her ikisi de geçerli ama farklı skor
        results = match_markets(self.sig, [c1, c2], now_utc=_NOW, config=self.cfg)
        valid = [r for r in results if r.rejection_reason is None]
        assert len(valid) == 2
        assert valid[0].match_score >= valid[1].match_score

    def test_empty_candidates(self):
        results = match_markets(self.sig, [], now_utc=_NOW, config=self.cfg)
        assert results == []

    def test_now_utc_defaults(self):
        # now_utc=None → datetime.now() kullanılır — crash etmemeli
        c = _candidate(minutes_to_end=10)
        # Kısa vadeli candidate; "şu an" bilinmediği için sonuç belirsiz ama crash olmaz
        results = match_markets(self.sig, [c], now_utc=None, config=self.cfg)
        assert isinstance(results, list)


# ── best_match ────────────────────────────────────────────────────────────────

class TestBestMatch:
    def setup_method(self):
        self.cfg = BridgeConfig()
        self.sig = _signal()

    def test_returns_best_valid(self):
        c = _candidate(minutes_to_end=10)
        result = best_match(self.sig, [c], now_utc=_NOW, config=self.cfg)
        assert result is not None
        assert result.rejection_reason is None

    def test_returns_none_when_no_valid(self):
        c = _candidate(status="closed")
        result = best_match(self.sig, [c], now_utc=_NOW, config=self.cfg)
        assert result is None

    def test_returns_none_on_empty(self):
        assert best_match(self.sig, [], now_utc=_NOW, config=self.cfg) is None
