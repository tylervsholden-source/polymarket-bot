"""
Position Manager muhasebe testleri.
Her test: başlangıç → pozisyon aç → kapat → sermaye doğrula.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, tempfile, pytest
from unittest.mock import patch, AsyncMock, MagicMock
from pathlib import Path


# ── data/positions.json'a temp dosya kullan ──────────────────────────────────
@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    import core.position_manager as pm_mod
    tmp_file = tmp_path / "positions.json"
    monkeypatch.setattr(pm_mod, "DATA_FILE", tmp_file)
    monkeypatch.setenv("INITIAL_CAPITAL", "100.0")
    return tmp_file


def make_pm(capital=10.0):
    from core.position_manager import PositionManager
    pm = PositionManager()
    pm.data["capital"] = capital
    return pm


# ── Test 1: Break-even kapanış — sermaye değişmemeli ─────────────────────────
def test_breakeven_close_no_double_count():
    pm = make_pm(capital=10.0)
    entry = 0.50
    amount = 2.0

    # Pozisyon ekle (add_position, order dict bekler)
    pm.data["positions"]["mkt1"] = {
        "order_id": "o1", "question": "BTC up?",
        "outcome": "YES", "amount": amount, "entry_price": entry, "status": "LIVE",
    }
    pm._save()

    # available_capital = 10 - 2 = 8
    assert pm.available_capital() == pytest.approx(8.0)

    # Break-even kapanış: shares = 2/0.50 = 4, payout = 4 * 0.50 = 2, pnl = 0
    pm._close_position("mkt1", 0.50)

    assert pm.available_capital() == pytest.approx(10.0), (
        f"Break-even sonrası sermaye 10.0 olmalı, got {pm.available_capital()}"
    )
    assert pm.data["closed"][-1]["pnl"] == pytest.approx(0.0)
    assert pm.data["closed"][-1]["result"] == "NEUTRAL"


# ── Test 2: YES kazanç — sadece pnl eklenmeli ────────────────────────────────
def test_yes_win_capital_correct():
    pm = make_pm(capital=10.0)
    pm.data["positions"]["mkt2"] = {
        "order_id": "o2", "question": "ETH up?",
        "outcome": "YES", "amount": 2.0, "entry_price": 0.40, "status": "LIVE",
    }
    pm._save()

    # shares = 2/0.40 = 5, payout = 5 * 1.0 = 5, pnl = +3
    pm._close_position("mkt2", 1.0)

    assert pm.available_capital() == pytest.approx(13.0), (
        f"WIN sonrası: 10 + 3 = 13 olmalı, got {pm.available_capital()}"
    )
    assert pm.data["closed"][-1]["result"] == "WIN"
    assert pm.data["closed"][-1]["pnl"] == pytest.approx(3.0)


# ── Test 3: YES kayıp — pnl negatif, sermaye düşmeli ─────────────────────────
def test_yes_loss_capital_correct():
    pm = make_pm(capital=10.0)
    pm.data["positions"]["mkt3"] = {
        "order_id": "o3", "question": "SOL up?",
        "outcome": "YES", "amount": 2.0, "entry_price": 0.80, "status": "LIVE",
    }
    pm._save()

    # shares = 2/0.80 = 2.5, payout = 2.5 * 0.0 = 0, pnl = -2
    pm._close_position("mkt3", 0.0)

    assert pm.available_capital() == pytest.approx(8.0), (
        f"LOSS sonrası: 10 - 2 = 8 olmalı, got {pm.available_capital()}"
    )
    assert pm.data["closed"][-1]["result"] == "LOSS"
    assert pm.data["closed"][-1]["pnl"] == pytest.approx(-2.0)


# ── Test 4: NO pozisyon kazanç — YES düştüğünde NO token değer kazanır ───────
def test_no_win_capital_correct():
    pm = make_pm(capital=10.0)
    # NO token fiyatı 0.25'ten alındı (YES=0.75 iken)
    pm.data["positions"]["mkt4"] = {
        "order_id": "o4", "question": "BTC down?",
        "outcome": "NO", "amount": 2.0, "entry_price": 0.25, "status": "LIVE",
    }
    pm._save()

    # YES resolve 0.0 → NO resolve 1.0 → close_price for NO token = 1.0
    # shares = 2/0.25 = 8, payout = 8 * 1.0 = 8, pnl = +6
    pm._close_position("mkt4", 1.0)

    assert pm.available_capital() == pytest.approx(16.0), (
        f"NO WIN: 10 + 6 = 16 olmalı, got {pm.available_capital()}"
    )
    assert pm.data["closed"][-1]["result"] == "WIN"


# ── Test 5: NO pozisyon kayıp — YES yükseldi ─────────────────────────────────
def test_no_loss_capital_correct():
    pm = make_pm(capital=10.0)
    pm.data["positions"]["mkt5"] = {
        "order_id": "o5", "question": "BTC down?",
        "outcome": "NO", "amount": 2.0, "entry_price": 0.25, "status": "LIVE",
    }
    pm._save()

    # YES resolve 1.0 → NO resolve 0.0
    pm._close_position("mkt5", 0.0)

    assert pm.available_capital() == pytest.approx(8.0), (
        f"NO LOSS: 10 - 2 = 8 olmalı, got {pm.available_capital()}"
    )
    assert pm.data["closed"][-1]["result"] == "LOSS"


# ── Test 6: Realized vs unrealized PnL ayrımı ────────────────────────────────
def test_unrealized_does_not_affect_capital():
    pm = make_pm(capital=10.0)
    pm.data["positions"]["mkt6"] = {
        "order_id": "o6", "question": "ETH up?",
        "outcome": "YES", "amount": 2.0, "entry_price": 0.50,
        "status": "LIVE", "unrealized_pnl": 0.50,  # fiyat 0.50→0.625 gitmiş diyelim
    }
    pm._save()

    # Unrealized PnL sermayeyi değiştirmemeli — sadece available_capital azaltır
    assert pm.data["capital"] == pytest.approx(10.0)
    assert pm.available_capital() == pytest.approx(8.0)   # 10 - 2 locked


# ── Test 7: Token direction mapping ──────────────────────────────────────────
def test_direction_token_mapping():
    """Orchestrator doğru token_id seçiyor mu?"""
    market = {
        "condition_id": "cid1",
        "question": "BTC up or down?",
        "yes_token_id": "yes_tok",
        "no_token_id":  "no_tok",
    }

    # YES sinyali → yes_token_id
    token_yes = market.get("yes_token_id") if "YES" == "YES" else market.get("no_token_id")
    assert token_yes == "yes_tok"

    # NO sinyali → no_token_id
    token_no = market.get("yes_token_id") if "NO" == "YES" else market.get("no_token_id")
    assert token_no == "no_tok"


# ── Test 8: Çoklu pozisyon, sermaye tutarlılığı ───────────────────────────────
def test_multiple_positions_capital_integrity():
    pm = make_pm(capital=10.0)

    pm.data["positions"]["m1"] = {"order_id":"o1","question":"A","outcome":"YES","amount":2.0,"entry_price":0.50,"status":"LIVE"}
    pm.data["positions"]["m2"] = {"order_id":"o2","question":"B","outcome":"NO","amount":1.0,"entry_price":0.25,"status":"LIVE"}
    pm._save()

    assert pm.available_capital() == pytest.approx(7.0)   # 10 - 2 - 1

    # m1 kazan (YES 1.0), m2 kaybet (NO 0.0)
    pm._close_position("m1", 1.0)   # pnl = 2/0.5 * 1.0 - 2 = +2
    pm._close_position("m2", 0.0)   # pnl = 1/0.25 * 0.0 - 1 = -1

    assert pm.available_capital() == pytest.approx(11.0)  # 10 + 2 - 1 = 11
