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
    market = make_market(yes_price=0.40)

    # Bayesian'ı 0.60 dön (40c piyasa, %60 tahmin → bullish, edge=0.20)
    with patch.object(eng.bayesian, "estimate") as mock_est:
        mock_est.return_value = MagicMock(probability=0.60, signal_strength=0.5)
        signal = await eng._evaluate_market(market, capital=50.0, z_score=0.0, signal_type="bayesian")

    assert signal is not None, "Bullish edge'de sinyal üretilmeli"
    assert signal.direction == "YES"
    assert signal.token_id == "yes_tok"
    assert signal.edge > 0


# ── Test 2: Bayesian < market_price → NO sinyali ─────────────────────────────
@pytest.mark.asyncio
async def test_bearish_yields_no_direction():
    eng = make_engine()
    # Provide real NO book so NO side is tradable (not synthetic/missing)
    market = make_market(yes_price=0.70, no_best_ask=0.30, no_best_bid=0.28)

    # Bayesian 0.45 → YES 70c'ta pahalı → NO al (30c'tan, edge≈0.25)
    with patch.object(eng.bayesian, "estimate") as mock_est:
        mock_est.return_value = MagicMock(probability=0.45, signal_strength=0.5)
        signal = await eng._evaluate_market(market, capital=50.0, z_score=0.0, signal_type="bayesian")

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
    # Provide real NO book for NO direction to be eligible
    market = make_market(yes_price=0.80, no_best_ask=0.20, no_best_bid=0.18)

    with patch.object(eng.bayesian, "estimate") as mock_est:
        mock_est.return_value = MagicMock(probability=0.35, signal_strength=0.5)  # bearish, edge≈0.45
        signal = await eng._evaluate_market(market, capital=50.0, z_score=0.0, signal_type="bayesian")

    assert signal is not None
    assert signal.direction == "NO"
    # entry_price NO tarafına yakın olmalı (0.20 ± Stoikov ayarı)
    assert signal.entry_price < 0.40, (
        f"NO entry_price YES fiyatına yakın olmamalı: {signal.entry_price:.3f}"
    )


# ── Test 5: YES sinyalinde market_price referans olarak YES fiyatı ────────────
@pytest.mark.asyncio
async def test_yes_signal_market_price_is_yes_price():
    eng = make_engine()
    market = make_market(yes_price=0.35)

    with patch.object(eng.bayesian, "estimate") as mock_est:
        mock_est.return_value = MagicMock(probability=0.65, signal_strength=0.5)
        signal = await eng._evaluate_market(market, capital=50.0, z_score=0.0, signal_type="bayesian")

    assert signal is not None
    assert signal.direction == "YES"
    assert signal.market_price == pytest.approx(0.35)
    assert signal.token_id == "yes_tok"
