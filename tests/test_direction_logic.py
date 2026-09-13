"""
ArbitrageEngine yön kararı testleri.
Gerçek API çağrısı yok — BinanceFeed ve SmartTrader mock'lanır.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import MagicMock, patch


def make_engine(min_edge=0.04):
    from strategies.arbitrage_engine import ArbitrageEngine
    eng = ArbitrageEngine(
        http_session=MagicMock(),
        binance_feed=None,
        smart_trader_tracker=None,
    )
    eng.min_edge = min_edge
    return eng


def make_market(yes_price: float, yes_token="yes_tok", no_token="no_tok",
                no_best_ask=None, no_best_bid=None):
    m = {
        "condition_id": "cid1",
        "question": "Will bitcoin up or down in 5 minutes?",
        "best_ask": yes_price,
        "best_bid": yes_price - 0.01,
        "yes_token_id": yes_token,
        "no_token_id": no_token,
        "endDate": "2099-01-01T00:00:00Z",
    }
    if no_best_ask is not None:
        m["no_best_ask"] = no_best_ask
    if no_best_bid is not None:
        m["no_best_bid"] = no_best_bid
    return m


# ── Test 1: Bayesian > market_price → YES sinyali ────────────────────────────
@pytest.mark.asyncio
async def test_bullish_yields_yes_direction():
    eng = make_engine()
    market = make_market(yes_price=0.48)

    # Bayesian'ı 0.65 dön (48c piyasa, %65 tahmin → bullish, edge=0.17)
    with patch.object(eng.bayesian, "estimate") as mock_est:
        mock_est.return_value = MagicMock(probability=0.65, signal_strength=0.5)
        signal = await eng._evaluate_market(market, capital=50.0, z_score=0.0, signal_type="bayesian")

    assert signal is not None, "Bullish edge'de sinyal üretilmeli"
    assert signal.direction == "YES"
    assert signal.token_id == "yes_tok"
    assert signal.edge > 0


# ── Test 2: Bayesian < market_price → NO sinyali ─────────────────────────────
@pytest.mark.asyncio
async def test_bearish_yields_no_direction():
    eng = make_engine()
    # Provide real NO book — NO ask=0.35 (above CHEAP_ENTRY_BLOCK threshold,
    # low enough that even after PROB_CAP (P(NO)<=0.65) and cost adjustment,
    # edge clears OPT-5's effective_min_edge for NO, ~0.19 with BTC addon)
    market = make_market(yes_price=0.70, no_best_ask=0.35, no_best_bid=0.33)

    # Bayesian 0.20 → YES 70c'ta pahalı → NO al (35c'tan, strong NO edge)
    with patch.object(eng.bayesian, "estimate") as mock_est:
        mock_est.return_value = MagicMock(probability=0.20, signal_strength=0.5)
        signal = await eng._evaluate_market(market, capital=50.0, z_score=0.0, signal_type="bayesian")

    # NO direction OPT-5 adaptive min_edge (~0.19) + half-Kelly uygulanır
    # NO edge ≈ 0.65 (PROB_CAP tavanı) - 0.35 = 0.30 (güçlü, eşiğin üstünde → geçer)
    assert signal is not None, "Bearish edge'de sinyal üretilmeli"
    assert signal.direction == "NO"
    assert signal.token_id == "no_tok"
    assert signal.edge > 0


# ── Test 3: Nötr (edge yok) → None dönmeli ────────────────────────────────────
@pytest.mark.asyncio
async def test_neutral_no_signal():
    eng = make_engine(min_edge=0.04)
    market = make_market(yes_price=0.50)

    # Bayesian 0.52 → edge=0.02 < min_edge=0.04
    with patch.object(eng.bayesian, "estimate") as mock_est:
        mock_est.return_value = MagicMock(probability=0.52, signal_strength=0.5)
        signal = await eng._evaluate_market(market, capital=50.0, z_score=0.0, signal_type="bayesian")

    assert signal is None, "Yetersiz edge'de sinyal üretilmemeli"


# ── Test 4: NO sinyalinde entry_price NO token fiyatıyla uyumlu olmalı ───────
@pytest.mark.asyncio
async def test_no_signal_entry_price_is_no_price():
    eng = make_engine()
    # Provide real NO book — NO ask=0.35 (above CHEAP_ENTRY_BLOCK threshold,
    # clears OPT-5's effective_min_edge for NO after PROB_CAP + cost adjustment)
    market = make_market(yes_price=0.70, no_best_ask=0.35, no_best_bid=0.33)

    with patch.object(eng.bayesian, "estimate") as mock_est:
        mock_est.return_value = MagicMock(probability=0.20, signal_strength=0.5)  # bearish, strong NO edge
        signal = await eng._evaluate_market(market, capital=50.0, z_score=0.0, signal_type="bayesian")

    assert signal is not None
    assert signal.direction == "NO"
    # entry_price NO tarafına yakın olmalı (0.46 ± Stoikov ayarı)
    assert signal.entry_price < 0.55, (
        f"NO entry_price YES fiyatına yakın olmamalı: {signal.entry_price:.3f}"
    )


# ── Test 5: YES sinyalinde market_price referans olarak YES fiyatı ────────────
@pytest.mark.asyncio
async def test_yes_signal_market_price_is_yes_price():
    eng = make_engine()
    market = make_market(yes_price=0.48)

    with patch.object(eng.bayesian, "estimate") as mock_est:
        mock_est.return_value = MagicMock(probability=0.65, signal_strength=0.5)
        signal = await eng._evaluate_market(market, capital=50.0, z_score=0.0, signal_type="bayesian")

    assert signal is not None
    assert signal.direction == "YES"
    assert signal.market_price == pytest.approx(0.48)
    assert signal.token_id == "yes_tok"
