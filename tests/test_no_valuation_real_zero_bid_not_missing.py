"""
Regression test: a NO position must be valued at a real, live orderbook's
best_bid even when that best_bid is genuinely 0.0 (nobody bidding — the NO
token is worth close to nothing) — it must not be treated the same as "no
book at all" and fall back to a stale synthetic price or entry_price.

Bug (core/position_manager.py::PositionManager.update_positions()):

    no_book = client.get_orderbook(no_tid) if no_tid else None
    if no_book and no_book["best_bid"] > 0:
        current_price = no_book["best_bid"]
    else:
        current_price = round(1.0 - yes_ask, 4) if yes_ask > 0 else pos["entry_price"]

`client.get_orderbook()` (core/polymarket_client.py) returns a real dict
`{"best_ask": ..., "best_bid": ...}` whenever the CLOB book was actually
fetched, defaulting best_bid to 0.0 when the bid side is genuinely empty —
a perfectly legitimate real quote. `no_book["best_bid"] > 0` conflates that
real "0.0" with `no_book is None`, discarding real market data.

This is the same "real zero vs. missing quote" ambiguity already fixed for
the YES branch a few lines below (37th daily review, commit a0e3d10):
`current_price = _yes_bid_raw if (_yes_bid_raw > 0 or _yes_ask_raw > 0) else
pos["entry_price"]` — trusts the quote whenever either side of the book is
real, not just when best_bid happens to be > 0. That fix was never mirrored
onto the NO branch's own real-orderbook check.

Consequence: if the fallback also can't resolve a price (yes_ask == 0 too,
"routine... once the book empties out near expiry" per the 34th daily
review), a NO position that the real market prices near-worthless is
instead valued at entry_price (zero unrealized P&L). If CLOB resolution is
then delayed past 45 minutes, the stored current_price feeds
TIMEOUT_HEURISTIC and — being neither > 0.80 nor < 0.20 — force-closes the
position NEUTRAL instead of LOSS, crediting capital it should not have and
poisoning data["daily"]["pnl"] (CLAUDE.md's -15% daily stop-loss bucket).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    import core.position_manager as pm_mod
    monkeypatch.setattr(pm_mod, "DATA_FILE", tmp_path / "positions.json")
    monkeypatch.setenv("INITIAL_CAPITAL", "100.0")


def _client_with(market: dict, orderbook_by_token: dict | None = None) -> MagicMock:
    client = MagicMock()
    client.get_market = AsyncMock(return_value=market)
    client.get_orderbook = MagicMock(
        side_effect=lambda tid: (orderbook_by_token or {}).get(tid)
    )
    return client


def _pm_with_no_position(age_minutes: float = 1.0):
    from core.position_manager import PositionManager

    pm = PositionManager()
    pm.data["capital"] = 100.0
    created = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    pm.data["positions"]["mkt1"] = {
        "order_id": "o1",
        "question": "Bitcoin Up or Down",
        "outcome": "NO",
        "amount": 3.5,
        "entry_price": 0.35,
        "status": "MATCHED",
        "token_id": "NO-TOKEN",
        "created_at": created.isoformat(),
    }
    pm._save()
    return pm


@pytest.mark.asyncio
async def test_real_live_book_with_zero_bid_is_trusted_not_treated_as_missing():
    """Real NO orderbook: best_bid=0.0 (nobody bidding) but best_ask=0.01 (a
    real, live quote — the book was actually fetched). This must value the
    position near-zero, not fall back to entry_price."""
    pm = _pm_with_no_position()
    market = {
        "resolved": False,
        "closed": False,
        "best_ask": 0.0,
        "best_bid": 0.0,
        "end_date_iso": "2099-01-01T00:00:00Z",
    }
    client = _client_with(market, {"NO-TOKEN": {"best_bid": 0.0, "best_ask": 0.01}})

    await pm.update_positions(client)

    pos = pm.data["positions"]["mkt1"]
    assert pos["current_price"] == pytest.approx(0.0), (
        f"current_price={pos['current_price']} — a real live book with a "
        "genuine zero bid was discarded in favor of a stale/synthetic "
        "fallback."
    )
    assert pos["unrealized_pnl"] == pytest.approx(-3.5), (
        f"unrealized_pnl={pos['unrealized_pnl']} — an open NO position the "
        "real market prices near-worthless must not report zero paper loss."
    )


@pytest.mark.asyncio
async def test_real_zero_bid_survives_timeout_as_loss_not_neutral(monkeypatch):
    """End-to-end: cycle 1 values the position off a real book with a genuine
    zero bid, cycle 2 hits the >45-minute resolution timeout. Must book LOSS,
    not a phantom NEUTRAL close that credits capital back."""
    import requests

    def _no_network(*args, **kwargs):
        raise RuntimeError("no network in tests")

    monkeypatch.setattr(requests, "get", _no_network)

    pm = _pm_with_no_position(age_minutes=60.0)

    open_market = {
        "resolved": False,
        "closed": False,
        "best_ask": 0.0,
        "best_bid": 0.0,
        "end_date_iso": "2099-01-01T00:00:00Z",
    }
    await pm.update_positions(
        _client_with(open_market, {"NO-TOKEN": {"best_bid": 0.0, "best_ask": 0.01}})
    )

    closed_market = {
        "resolved": False,
        "closed": True,
        "best_ask": 0.0,
        "best_bid": 0.0,
        "end_date_iso": "2099-01-01T00:00:00Z",
    }
    await pm.update_positions(
        _client_with(closed_market, {"NO-TOKEN": {"best_bid": 0.0, "best_ask": 0.01}})
    )

    assert "mkt1" not in pm.data["positions"], "position should have been closed"
    closed = pm.data["closed"][-1]

    assert closed["result"] == "LOSS", (
        f"position closed as {closed['result']} (pnl={closed['pnl']}) — a "
        "position the real market priced near-zero must be booked a LOSS, "
        "not a phantom NEUTRAL close."
    )
    assert pm.data["capital"] == pytest.approx(96.5), (
        f"capital={pm.data['capital']} — a phantom NEUTRAL close wrongly "
        "credited the position's amount back."
    )


@pytest.mark.asyncio
async def test_no_book_none_still_falls_back_as_before():
    """Non-regression: when the book fetch genuinely fails (None), the
    existing fallback chain is untouched."""
    pm = _pm_with_no_position()
    market = {
        "resolved": False,
        "closed": False,
        "best_ask": 0.02,
        "best_bid": 0.0,
        "end_date_iso": "2099-01-01T00:00:00Z",
    }
    client = _client_with(market, orderbook_by_token={})  # get_orderbook -> None

    await pm.update_positions(client)

    assert pm.data["positions"]["mkt1"]["current_price"] == pytest.approx(0.98)


@pytest.mark.asyncio
async def test_real_nonzero_bid_still_wins_as_before():
    """Non-regression: a real non-zero best_bid keeps behaving exactly as
    before the fix."""
    pm = _pm_with_no_position()
    market = {
        "resolved": False,
        "closed": False,
        "best_ask": 0.0,
        "best_bid": 0.0,
        "end_date_iso": "2099-01-01T00:00:00Z",
    }
    client = _client_with(market, {"NO-TOKEN": {"best_bid": 0.62, "best_ask": 0.64}})

    await pm.update_positions(client)

    assert pm.data["positions"]["mkt1"]["current_price"] == pytest.approx(0.62)
