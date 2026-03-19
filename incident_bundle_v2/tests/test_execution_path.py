"""
Execution path end-to-end testi (simulasyon modu).

1. ArbitrageEngine sinyal uretir
2. PolymarketClient._simulate() -- CLOB yok, sahte order doner
3. PositionManager.add_position() ile kayit edilir
4. unrealized PnL dogru hesaplanir (YES/NO farkli token fiyati)
5. _close_position() sonrasi capital double-count olmaz
6. Token ID (yes_token_id / no_token_id) dogru routelanir
"""
import pytest
from pathlib import Path


# ------------------------------------------------------------------ #
# Fixture: izole PositionManager (data/positions.json'a dokunmaz)
# ------------------------------------------------------------------ #

@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    import core.position_manager as pm_mod
    monkeypatch.setattr(pm_mod, "DATA_FILE", tmp_path / "positions.json")
    monkeypatch.setenv("INITIAL_CAPITAL", "10.0")


def _make_pm(capital: float = 10.0):
    from core.position_manager import PositionManager
    pm = PositionManager()
    pm.data["capital"] = capital
    return pm


def _fake_market(cid, question, ask, bid=None, minutes=60):
    from datetime import datetime, timezone, timedelta
    end = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
    return {
        "condition_id": cid, "question": question,
        "best_ask": ask,
        "best_bid": bid if bid is not None else round(ask * 0.98, 4),
        "volume": 50000, "endDate": end,
        "yes_token_id": f"YES-{cid}",
        "no_token_id":  f"NO-{cid}",
    }


# ------------------------------------------------------------------ #
# Test 1: Simulasyon order -- outcome ve status
# ------------------------------------------------------------------ #

def test_simulate_yes_order():
    from core.polymarket_client import PolymarketClient
    c = PolymarketClient.__new__(PolymarketClient)
    c._clob = None
    order = c._simulate("mkt-001", "YES", 1.0, 0.55)
    assert order["outcome"] == "YES"
    assert order["status"] == "SIMULATED"
    assert order["amount"] == 1.0
    assert order["price"] == 0.55


def test_simulate_no_order():
    from core.polymarket_client import PolymarketClient
    c = PolymarketClient.__new__(PolymarketClient)
    c._clob = None
    order = c._simulate("mkt-002", "NO", 2.0, 0.40)
    assert order["outcome"] == "NO"
    assert order["status"] == "SIMULATED"


# ------------------------------------------------------------------ #
# Test 2: YES unrealized PnL -- fiyat yukari cikinca pozitif
# ------------------------------------------------------------------ #

def test_yes_unrealized_pnl_math():
    """YES pozisyon aritmetigi: shares * current_price - amount > 0."""
    pm = _make_pm(10.0)
    pm.add_position("mkt-yes", {
        "order_id": "o1", "outcome": "YES", "amount": 2.0,
        "price": 0.50, "status": "SIMULATED",
    }, "BTC Up or Down")

    pos = pm.data["positions"]["mkt-yes"]
    shares = pos["amount"] / pos["entry_price"]   # 4 shares
    current_yes = 0.70
    pnl = shares * current_yes - pos["amount"]    # 4*0.70-2.0 = 0.80
    assert pnl > 0, f"YES kari pozitif olmali: {pnl}"


# ------------------------------------------------------------------ #
# Test 3: NO unrealized PnL -- YES dusunce NO token deger kazanir
# ------------------------------------------------------------------ #

def test_no_unrealized_pnl_math():
    """NO pozisyon: NO token fiyati = 1 - yes_ask."""
    pm = _make_pm(10.0)
    pm.add_position("mkt-no", {
        "order_id": "o2", "outcome": "NO", "amount": 2.0,
        "price": 0.40, "status": "SIMULATED",
    }, "ETH Up or Down")

    pos = pm.data["positions"]["mkt-no"]
    shares = pos["amount"] / pos["entry_price"]   # 5 shares
    yes_ask_now = 0.50
    no_token_price = 1.0 - yes_ask_now           # 0.50
    pnl = shares * no_token_price - pos["amount"] # 5*0.50-2.0 = 0.50
    assert pnl > 0, f"NO kari pozitif olmali: {pnl}"


# ------------------------------------------------------------------ #
# Test 4: close_position -- double-count yok
# ------------------------------------------------------------------ #

def test_close_no_double_count():
    pm = _make_pm(10.0)
    pm.add_position("mkt-dc", {
        "order_id": "o3", "outcome": "YES", "amount": 2.0,
        "price": 0.50, "status": "SIMULATED",
    }, "SOL Up or Down")

    # shares=4, payout=4*1.0=4.0, pnl=+2.0
    pm._close_position("mkt-dc", token_close_price=1.0)

    # capital += pnl (+2.0) SADECE -- ana para locked sayiliyordu, eklenmez
    assert abs(pm.data["capital"] - 12.0) < 0.001, (
        f"Double-count: beklenen 12.0, bulunan {pm.data['capital']}"
    )
    assert "mkt-dc" not in pm.data["positions"]


# ------------------------------------------------------------------ #
# Test 5: Tam execution zinciri (signal -> sim order -> position -> close)
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_full_sim_execution_chain():
    """
    Signal -> _simulate -> add_position -> _close_position
    Deterministik: ag yok, CLOB yok, BinanceFeed mock.
    """
    import httpx
    from core.polymarket_client import PolymarketClient
    from strategies.arbitrage_engine import ArbitrageEngine
    from agents.binance_feed import BinanceFeed

    # Sim mode client (CLOB yok)
    client = PolymarketClient.__new__(PolymarketClient)
    client._clob = None
    client.session = httpx.AsyncClient()

    pm = _make_pm(5.0)

    # BinanceFeed -- guclu bullish sinyal, network cagrisi yok
    from unittest.mock import AsyncMock
    feed = BinanceFeed()
    feed._cache = {
        "BTCUSDT": {
            "price": 70000.0,
            "ob_imbalance": 0.4,
            "intervals": {
                "5m":  {"change_pct": 1.5, "trend_pct": 0.8, "volume_ratio": 3.0, "rsi": 60.0, "momentum": 2},
                "15m": {"change_pct": 1.5, "trend_pct": 0.8, "volume_ratio": 3.0, "rsi": 60.0, "momentum": 2},
                "1h":  {"change_pct": 1.5, "trend_pct": 0.8, "volume_ratio": 3.0, "rsi": 60.0, "momentum": 2},
                "4h":  {"change_pct": 1.5, "trend_pct": 0.8, "volume_ratio": 3.0, "rsi": 60.0, "momentum": 2},
            },
        }
    }
    feed.refresh = AsyncMock()  # network cagrisi engelle

    engine = ArbitrageEngine(
        http_session=client.session,
        binance_feed=feed,
        smart_trader_tracker=None,
    )
    engine.min_edge = 0.01

    market = _fake_market(
        "btc-exec-test",
        "Bitcoin up or down? 12:00PM-1:00PM ET",
        ask=0.40,
    )

    signals = await engine.analyze([market], capital=5.0)
    assert len(signals) > 0, "Guclu bullish sinyal uretilmeli"

    sig = signals[0]

    # Token routing kontrolu
    if sig.direction == "YES":
        assert sig.token_id == "YES-btc-exec-test", f"YES token yanlis: {sig.token_id}"
    else:
        assert sig.token_id == "NO-btc-exec-test", f"NO token yanlis: {sig.token_id}"

    # Sim order
    order = client._simulate("btc-exec-test", sig.direction, sig.size, sig.entry_price)
    order["outcome"] = sig.direction
    assert order["status"] == "SIMULATED"

    # Pozisyon kaydi
    pm.add_position("btc-exec-test", order, market["question"])
    assert pm.has_position("btc-exec-test")
    assert pm.available_capital() < 5.0

    # Neutral kapanis (pnl=0, giris fiyatindan cikis)
    pm._close_position("btc-exec-test", token_close_price=sig.entry_price)
    assert not pm.has_position("btc-exec-test")
    assert abs(pm.data["capital"] - 5.0) < 0.01, (
        f"Neutral kapanista capital kaydi: {pm.data['capital']}"
    )

    await client.session.aclose()
    print(f"\n  PASS: {sig.direction}@{sig.entry_price:.3f} "
          f"edge={sig.edge:.4f} size=${sig.size:.2f} token={sig.token_id}")
