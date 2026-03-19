"""
Entegrasyon testi: Bitstamp + SmartTraderTracker + ArbitrageEngine

Network testleri (test_bitstamp_live) canlı ağ gerektirir.
Ağ yoksa otomatik skip yapar — CI/offline ortamlarda test suite yine deterministik geçer.
"""
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock
import pytest


# ------------------------------------------------------------------ #
# Canlı ağ testi (skip if offline)
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_bitstamp_live():
    """Bitstamp API'den gercek veri ceker. Ag yoksa skip."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as s:
            r = await s.get("https://www.bitstamp.net/api/v2/ticker/btcusd/")
            if r.status_code != 200:
                pytest.skip("Bitstamp API erisilemez")
    except Exception:
        pytest.skip("Network erisimi yok — test atlanıyor")

    from agents.binance_feed import BinanceFeed
    feed = BinanceFeed()
    await feed.refresh({"BTCUSDT", "XRPUSDT", "DOGEUSDT"})
    sig = feed.get_signal("BTCUSDT", "1h")
    assert sig["price"] > 10000, f"BTC fiyati yanlis: {sig['price']}"
    assert 0 <= sig["rsi"] <= 100
    print(f"  BTC: ${sig['price']:.0f} RSI={sig['rsi']:.0f} chg={sig['change_pct']:+.2f}%")


# ------------------------------------------------------------------ #
# SmartTraderTracker — network, skip if offline
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_smart_trader_refresh():
    """SmartTraderTracker API'den pozisyon ceker. Ag yoksa skip."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as s:
            r = await s.get("https://data-api.polymarket.com/positions",
                            params={"user": "0x0000000000000000000000000000000000000000", "limit": "1"})
            if r.status_code not in (200, 404):
                pytest.skip("Polymarket data API erisilemez")
    except Exception:
        pytest.skip("Network erisimi yok — test atlanıyor")

    from agents.smart_trader_tracker import SmartTraderTracker, TOP_TRADERS
    tracker = SmartTraderTracker()
    assert len(tracker._traders) == len(TOP_TRADERS)

    await tracker.refresh()
    print(f"  {len(tracker._positions)} market izleniyor")


# ------------------------------------------------------------------ #
# SmartTraderTracker sinyal mantığı — network'süz, deterministik
# ------------------------------------------------------------------ #

def test_smart_trader_signal_logic():
    """SmartTraderTracker sinyal hesabini test eder."""
    from agents.smart_trader_tracker import SmartTraderTracker
    tracker = SmartTraderTracker()

    # Bos cache -> nötr
    sig = tracker.get_signal("no-such-id")
    assert sig["signal"] == 0.0
    assert sig["total_traders"] == 0

    # 2 long, 1 short
    tracker._positions["test-cid"] = {
        "Theo4": 500.0,    # long, efficiency 0.51
        "BetTom42": 200.0, # long, efficiency 0.50
        "RepTrump": -100.0 # short, efficiency 0.54
    }
    sig = tracker.get_signal("test-cid")
    assert sig["signal"] > 0
    assert "Theo4" in sig["buyers"]
    assert "RepTrump" in sig["sellers"]
    assert sig["total_traders"] == 3
    print(f"  2 long, 1 short -> signal={sig['signal']:+.3f}")

    # Hepsi short
    tracker._positions["test-cid2"] = {
        "Theo4": -300.0,
        "BetTom42": -100.0,
    }
    sig2 = tracker.get_signal("test-cid2")
    assert sig2["signal"] < 0
    assert len(sig2["sellers"]) == 2
    assert len(sig2["buyers"]) == 0
    print(f"  2 short -> signal={sig2['signal']:+.3f}")


# ------------------------------------------------------------------ #
# ArbitrageEngine — BinanceFeed mock ile deterministik
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_arbitrage_engine_with_smart_trader():
    """ArbitrageEngine + SmartTraderTracker — BinanceFeed mocklaniyor."""
    from agents.binance_feed import BinanceFeed
    from agents.smart_trader_tracker import SmartTraderTracker
    from strategies.arbitrage_engine import ArbitrageEngine

    feed    = BinanceFeed()
    tracker = SmartTraderTracker()
    engine  = ArbitrageEngine(binance_feed=feed, smart_trader_tracker=tracker)

    # BinanceFeed.refresh() mock'la — ağ çağrısı yapma
    feed._data = {
        "BTCUSDT": {
            "price": 68000.0, "change_pct": 0.5, "volatility": 0.002,
            "ob_imbalance": 0.1, "rsi": 55.0, "volume_ratio": 1.2,
        },
        "XRPUSDT": {
            "price": 1.40, "change_pct": -0.3, "volatility": 0.003,
            "ob_imbalance": -0.1, "rsi": 45.0, "volume_ratio": 0.9,
        },
        "DOGEUSDT": {
            "price": 0.095, "change_pct": 0.1, "volatility": 0.004,
            "ob_imbalance": 0.0, "rsi": 50.0, "volume_ratio": 1.0,
        },
    }
    feed._last_refresh = 9e18  # refresh'i zorla atla

    in_70min = (datetime.now(timezone.utc) + timedelta(minutes=70)).isoformat()
    in_45min = (datetime.now(timezone.utc) + timedelta(minutes=45)).isoformat()

    fake_markets = [
        {
            "condition_id": "test-btc-001",
            "question": "Bitcoin up or down? 12:00PM - 1:00PM ET",
            "best_ask": 0.62, "best_bid": 0.60,
            "volume": 50000, "endDate": in_70min,
            "yes_token_id": "y-btc-001", "no_token_id": "n-btc-001",
        },
        {
            "condition_id": "test-xrp-002",
            "question": "XRP up or down? 12:30PM - 1:30PM ET",
            "best_ask": 0.35, "best_bid": 0.33,
            "volume": 20000, "endDate": in_70min,
            "yes_token_id": "y-xrp-002", "no_token_id": "n-xrp-002",
        },
        {
            "condition_id": "test-doge-003",
            "question": "Dogecoin up or down? 12:00PM - 12:05PM ET",
            "best_ask": 0.88, "best_bid": 0.86,
            "volume": 15000, "endDate": in_45min,
            "yes_token_id": "y-doge-003", "no_token_id": "n-doge-003",
        },
    ]

    # Smart money BTC markette long
    tracker._positions["test-btc-001"] = {"Theo4": 1000.0, "BetTom42": 500.0}
    tracker._last_refresh = 9e18

    signals = await engine.analyze(fake_markets, capital=1.75)
    print(f"  Uretilen sinyal: {len(signals)}")
    for s in signals:
        print(f"  [{s.signal_type}] {s.market['question'][:55]}")
        print(f"    dir={s.direction} P={s.bayesian_prob:.3f} edge={s.edge:.4f} ${s.size:.2f}")
        # token_id dogru set edilmeli
        assert s.token_id, f"token_id bos — direction={s.direction}"
        if s.direction == "YES":
            assert s.token_id.startswith("y-"), f"YES token yanlis: {s.token_id}"
        else:
            assert s.token_id.startswith("n-"), f"NO token yanlis: {s.token_id}"

    assert isinstance(signals, list)
    print("  PASS: engine.analyze() hatasiz tamamlandi, token_id dogru")
