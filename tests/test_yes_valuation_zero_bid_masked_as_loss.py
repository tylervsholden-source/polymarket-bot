"""
Regression test: a YES position with a real bid of exactly 0.0 (the token has
effectively crashed to zero — everyone knows it's going to resolve NO) must be
valued at that real 0.0, not silently rewritten back to entry_price.

Bug (core/position_manager.py::PositionManager.update_positions()), YES branch:

    current_price = float(market.get("best_bid", pos["entry_price"]) or pos["entry_price"])

`client.get_market()` (core/polymarket_client.py) always normalizes best_bid to
a float, defaulting to 0.0 when Gamma has no quote — the dict key is therefore
never actually missing, so `market.get("best_bid", pos["entry_price"])` never
uses its default argument; it always returns the (possibly 0.0) value already
in the dict. `... or pos["entry_price"]` then treats ANY best_bid == 0.0 as
"unknown," conflating two different situations:

1. Gamma genuinely has no quote at all (best_bid AND best_ask both 0) — price
   really is unknown, entry_price is the right fallback.
2. Gamma has a REAL quote of exactly 0.0 on the bid side (best_ask > 0, or the
   book has simply gone to zero) — the YES token has crashed, and 0.0 is the
   correct, informative price.

In case 2 the old code silently substituted entry_price for a real near-total
loss. If CLOB resolution is then delayed past 45 minutes, the TIMEOUT_HEURISTIC
reads that stored current_price:

    elif _last_price is not None and _last_price < 0.20:
        self._close_position(market_id, 0.0)   # LOSS
    else:
        self._close_position_neutral(market_id)  # NEUTRAL

entry_price (e.g. 0.45) is neither > 0.80 nor < 0.20, so the position is
force-closed NEUTRAL (pnl = 0) instead of the LOSS it actually is — capital is
overstated, data["daily"]["pnl"] never reflects the loss (masking CLAUDE.md's
-15% daily stop-loss), and the trade is mislabeled NEUTRAL rather than LOSS in
`closed`, corrupting Kelly's streak tracking, AutonomousDecisionEngine, and
TradeAnalyzer, all of which trust `result`.

This mirrors the NO-side bug fixed in the 34th daily review (#60,
tests/test_no_valuation_no_quote_phantom_loss.py) — same 0-vs-missing
ambiguity, opposite (loss-masking rather than phantom-loss) failure mode.

Fix: only fall back to entry_price when there is truly no quote data at all
(best_bid AND best_ask both 0/absent); otherwise use the real best_bid,
including when it is genuinely 0.0.
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


def _client_with(market: dict) -> MagicMock:
    client = MagicMock()
    client.get_market = AsyncMock(return_value=market)
    client.get_orderbook = MagicMock(return_value=None)
    return client


def _pm_with_yes_position(age_minutes: float = 1.0):
    from core.position_manager import PositionManager

    pm = PositionManager()
    pm.data["capital"] = 100.0
    created = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    pm.data["positions"]["mkt1"] = {
        "order_id": "o1",
        "question": "Bitcoin Up or Down",
        "outcome": "YES",
        "amount": 4.0,
        "entry_price": 0.45,
        "status": "MATCHED",
        "token_id": "YES-TOKEN",
        "created_at": created.isoformat(),
    }
    pm._save()
    return pm


# ── 1. Root cause: a real zero bid must be valued at zero, not entry_price ──

@pytest.mark.asyncio
async def test_yes_position_valued_at_real_zero_bid_not_entry_price():
    """best_ask > 0 proves Gamma really is quoting this market — best_bid=0.0
    is a real quote (nobody bidding, token has crashed), not a missing one."""
    pm = _pm_with_yes_position()
    market = {
        "resolved": False,
        "closed": False,
        "best_ask": 0.02,
        "best_bid": 0.0,
        "end_date_iso": "2099-01-01T00:00:00Z",
    }
    await pm.update_positions(_client_with(market))

    pos = pm.data["positions"]["mkt1"]
    assert pos["current_price"] == pytest.approx(0.0), (
        f"YES position with a real best_bid=0.0 valued at {pos['current_price']} "
        "instead of 0.0 — a real near-total loss was masked as entry_price."
    )
    assert pos["unrealized_pnl"] == pytest.approx(-4.0), (
        f"unrealized_pnl={pos['unrealized_pnl']} — a crashed YES position must "
        "report its real paper loss, not 0.0."
    )


# ── 2. The money impact: 45-minute timeout heuristic must book the real LOSS ─

@pytest.mark.asyncio
async def test_zero_bid_yes_position_is_closed_as_loss_after_timeout(monkeypatch):
    """End-to-end: cycle 1 values the position at a real zero bid, cycle 2 hits
    the >45-minute resolution timeout. The stored current_price must drive a
    LOSS close, not a NEUTRAL one that hides the loss from capital/daily PnL."""
    import requests

    def _no_network(*args, **kwargs):
        raise RuntimeError("no network in tests")

    monkeypatch.setattr(requests, "get", _no_network)

    pm = _pm_with_yes_position(age_minutes=60.0)

    open_market = {
        "resolved": False,
        "closed": False,
        "best_ask": 0.02,
        "best_bid": 0.0,
        "end_date_iso": "2099-01-01T00:00:00Z",
    }
    await pm.update_positions(_client_with(open_market))

    closed_market = {
        "resolved": False,
        "closed": True,
        "best_ask": 0.02,
        "best_bid": 0.0,
        "end_date_iso": "2099-01-01T00:00:00Z",
    }
    await pm.update_positions(_client_with(closed_market))

    assert "mkt1" not in pm.data["positions"], "position should have been closed"
    closed = pm.data["closed"][-1]

    assert closed["result"] == "LOSS", (
        f"position closed as {closed['result']} (pnl={closed['pnl']}) — a YES "
        "position with a genuine zero bid must be booked as the LOSS it is, "
        "not NEUTRAL off a fabricated current_price of entry_price."
    )
    assert closed["pnl"] == pytest.approx(-4.0)
    assert pm.data["capital"] == pytest.approx(96.0), (
        f"capital={pm.data['capital']} — real loss must actually hit capital."
    )
    assert pm.data["daily"]["pnl"] == pytest.approx(-4.0), (
        "real loss must poison the daily PnL bucket that CLAUDE.md's -15% "
        "daily stop-loss is measured against."
    )


# ── 3. Non-regression: truly unquoted market still falls back to entry_price ─

@pytest.mark.asyncio
async def test_yes_position_falls_back_to_entry_price_when_truly_unquoted():
    """best_ask AND best_bid both 0 -> Gamma has no quote at all, entry_price
    remains the correct fallback (unchanged behavior)."""
    pm = _pm_with_yes_position()
    market = {
        "resolved": False,
        "closed": False,
        "best_ask": 0.0,
        "best_bid": 0.0,
        "end_date_iso": "2099-01-01T00:00:00Z",
    }
    await pm.update_positions(_client_with(market))

    pos = pm.data["positions"]["mkt1"]
    assert pos["current_price"] == pytest.approx(0.45)
    assert pos["unrealized_pnl"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_yes_position_uses_real_positive_bid_unchanged():
    """Non-regression: a normal positive best_bid still drives valuation
    exactly as before."""
    pm = _pm_with_yes_position()
    market = {
        "resolved": False,
        "closed": False,
        "best_ask": 0.60,
        "best_bid": 0.55,
        "end_date_iso": "2099-01-01T00:00:00Z",
    }
    await pm.update_positions(_client_with(market))

    assert pm.data["positions"]["mkt1"]["current_price"] == pytest.approx(0.55)
