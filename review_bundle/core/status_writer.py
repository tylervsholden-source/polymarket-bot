"""
Status Writer — Bot durumunu data/status.json'a yazar.
Web dashboard bu dosyayı /api/status üzerinden okur.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta

_TZ_TR = timezone(timedelta(hours=3))  # Turkey time (UTC+3)


def _now_tr() -> str:
    return datetime.now(_TZ_TR).strftime("%H:%M:%S")

_STATUS: dict = {
    "updated": "",
    "running": False,
    "capital": 0.0,
    "initial_capital": 500.0,
    "pnl": 0.0,
    "pnl_pct": 0.0,
    "cycle": 0,
    "scanned": 0,
    "candidates": 0,
    "open_positions": 0,
    "max_positions": 5,
    "next_cycle_in": "—",
    "positions": {},
    "closed": [],
    "decisions": [],
    "orders": [],        # live placed orders feed
    "indices": {},
    "crypto": {},
    "whale": {},
    "btc_arb": {},
    "signal_mode": os.getenv("SIGNAL_MODE", "ai"),
}

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_STATUS_FILE = os.path.join(_DATA_DIR, "status.json")


def update(**kwargs):
    _STATUS.update(kwargs)
    _STATUS["updated"] = _now_tr()


def add_decision(
    market: str,
    category: str,
    prob: float,
    price: float,
    edge: float,
    confidence: str,
    action: str,
    reasoning: str = "",
    size: float = 0.0,
):
    entry = {
        "time": _now_tr(),
        "market": market[:60],
        "category": category,
        "prob": round(prob, 3),
        "price": round(price, 3),
        "edge": round(edge, 3),
        "confidence": confidence,
        "action": action,
        "reasoning": reasoning[:120],
        "size": round(size, 2),
    }
    _STATUS["decisions"].insert(0, entry)
    _STATUS["decisions"] = _STATUS["decisions"][:80]  # son 80 karar


def add_order(
    market: str,
    outcome: str,
    amount: float,
    price: float,
    order_id: str,
    status: str,
    edge: float = 0.0,
):
    oid = order_id if len(order_id) <= 20 else (order_id.__getitem__(slice(0, 20)) + "...")
    entry = {
        "time": _now_tr(),
        "market": market[:55],
        "outcome": outcome,
        "amount": int(amount * 100) / 100,
        "price": int(price * 1000) / 1000,
        "order_id": oid,
        "status": status,
        "edge": int(edge * 1000) / 1000,
    }
    _STATUS["orders"].insert(0, entry)
    _STATUS["orders"] = _STATUS["orders"][:30]  # son 30 emir


def update_indices(data: dict):
    """market_watcher._data → status"""
    _STATUS["indices"] = {
        name: {
            "price": d.get("price", 0),
            "change_pct": d.get("change_pct", 0),
            "updated": d.get("updated", ""),
        }
        for name, d in data.items()
    }


def update_crypto(data: dict):
    """CoinGecko crypto fiyatları → status"""
    _STATUS["crypto"] = data


def save():
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        with open(_STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(_STATUS, f, ensure_ascii=False)
    except Exception:
        pass
