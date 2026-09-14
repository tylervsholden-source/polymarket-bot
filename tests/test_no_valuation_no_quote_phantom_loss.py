"""
Regression test: a NO position must not be marked worthless (current_price=0.00)
when NO price source is available at all — and must therefore not be booked as a
full LOSS by the 45-minute resolution-timeout heuristic.

Bug (core/position_manager.py::PositionManager.update_positions()):

    yes_ask = float(market.get("best_ask", 0) or 0) or (1.0 - float(market.get("best_bid", 0) or 0))
    ...
    if outcome == "NO":
        no_tid = pos.get("token_id") or (market or {}).get("no_token_id")
        no_book = client.get_orderbook(no_tid) if no_tid else None
        if no_book and no_book["best_bid"] > 0:
            current_price = no_book["best_bid"]
        else:
            current_price = round(1.0 - yes_ask, 4) if yes_ask > 0 else pos["entry_price"]

`client.get_market()` (core/polymarket_client.py) always normalizes
`best_ask`/`best_bid` to a float, defaulting to 0.0 when Gamma returns no
quote at all. When BOTH are 0 — routine for these 5-minute crypto up/down
markets once the book empties out near expiry, and also whenever Gamma is
lagging — the `or` chain degenerates to `0 or (1.0 - 0)` = **1.0**: a
fabricated "YES costs 100 cents" price, not a real quote.

The NO branch then computes `round(1.0 - 1.0, 4)` = **0.00**. The branch's own
"price unknown" guard (`else pos["entry_price"]`, which is exactly what the YES
branch a few lines below already does via
`float(market.get("best_bid", pos["entry_price"]) or pos["entry_price"])`)
never fires, because `yes_ask > 0` is True — it is 1.0.

Consequences, in order of severity:

1. `pos["current_price"] = 0.0` and `pos["unrealized_pnl"] = -amount` are
   written to data/positions.json — the position is shown as a total loss
   while it is still open and may well be winning.
2. Much worse: when CLOB resolution is delayed past 45 minutes (a case this
   file explicitly codes for — see WAITING_RESOLUTION / STALE_UNRESOLVED /
   FORCE_CLOSE_TIMEOUT), the timeout heuristic reads that same stored value:

       _last_price = pos.get("current_price")
       ...
       elif _last_price is not None and _last_price < 0.20:
           self._close_position(market_id, 0.0)   # booked as a full LOSS

   0.0 < 0.20, so the position is closed at 0.0 — a **phantom full loss**
   (`pnl = -amount`) instead of the NEUTRAL close (`pnl = 0`) that the
   "price is uncertain" fallback right below it was written to produce.

That phantom loss is real money in the accounting: it subtracts from
`data["capital"]`, from `data["daily"]["pnl"]` (the denominator/numerator of
CLAUDE.md's non-negotiable daily -15% stop-loss), and it is recorded as
`result="LOSS"`, which then feeds the Kelly streak multiplier
(strategies/kelly_criterion.py::update_streak), AutonomousDecisionEngine's
loss-streak defense, WalkForwardValidator's win rate and the ML classifier's
training labels.

Note this is NOT the bug fixed in the 14th daily review (#33,
tests/test_no_valuation_live_orderbook.py): that one made the *orderbook
lookup* use pos["token_id"]. This is the fallback used when that orderbook
lookup legitimately comes back empty/unavailable (no CLOB credentials — the
documented no-API-key mode — a transient CLOB error, or simply an empty bid
side near expiry).

Fix: only use the `1 - best_bid` synthetic when there actually is a YES bid;
otherwise leave yes_ask at 0 so the existing `else pos["entry_price"]`
"unknown price" fallback does its job, symmetrically with the YES branch.
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
        # No "H:MMAM-H:MMPM" pattern on purpose: keeps update_positions()'
        # question-derived TIME_EXPIRED check out of this test.
        "question": "Bitcoin Up or Down",
        "outcome": "NO",
        "amount": 4.0,
        "entry_price": 0.45,
        "status": "MATCHED",
        "token_id": "NO-TOKEN",
        "created_at": created.isoformat(),
    }
    pm._save()
    return pm


# ── 1. Root cause: valuation with no price source anywhere ──────────────────

@pytest.mark.asyncio
async def test_no_position_not_valued_at_zero_when_no_quote_available():
    """Gamma returns no quotes (bestAsk=bestBid=0) and the CLOB NO book is
    unavailable. The position's price is simply UNKNOWN — it must fall back to
    entry_price (what the YES branch already does), not to 0.00."""
    pm = _pm_with_no_position()
    market = {
        "resolved": False,
        "closed": False,
        "best_ask": 0.0,
        "best_bid": 0.0,
        "end_date_iso": "2099-01-01T00:00:00Z",
    }
    client = _client_with(market, orderbook_by_token={})  # get_orderbook -> None

    await pm.update_positions(client)

    pos = pm.data["positions"]["mkt1"]
    assert pos["current_price"] == pytest.approx(0.45), (
        f"NO position valued at {pos['current_price']} with NO price source "
        "available — `0 or (1.0 - 0)` fabricated yes_ask=1.0, so 1-yes_ask=0.00 "
        "marked an open position as totally worthless."
    )
    assert pos["unrealized_pnl"] == pytest.approx(0.0), (
        f"unrealized_pnl={pos['unrealized_pnl']} — a position with no available "
        "quote must not report a full paper loss."
    )


# ── 2. The money impact: 45-minute timeout heuristic books a phantom LOSS ────

@pytest.mark.asyncio
async def test_unquoted_no_position_is_not_closed_as_full_loss_after_timeout(monkeypatch):
    """End-to-end: cycle 1 values the position with no quote available, cycle 2
    hits the >45-minute resolution timeout. The stored current_price decides
    between a NEUTRAL close (pnl 0) and a full LOSS (pnl = -amount)."""
    import requests

    def _no_network(*args, **kwargs):
        raise RuntimeError("no network in tests")

    monkeypatch.setattr(requests, "get", _no_network)

    pm = _pm_with_no_position(age_minutes=60.0)

    # Cycle 1 — market still open, but nobody is quoting it.
    open_market = {
        "resolved": False,
        "closed": False,
        "best_ask": 0.0,
        "best_bid": 0.0,
        "end_date_iso": "2099-01-01T00:00:00Z",
    }
    await pm.update_positions(_client_with(open_market, orderbook_by_token={}))

    # Cycle 2 — market closed, CLOB resolution unavailable, position is 60
    # minutes old -> FORCE_CLOSE_TIMEOUT / TIMEOUT_HEURISTIC branch.
    closed_market = {
        "resolved": False,
        "closed": True,
        "best_ask": 0.0,
        "best_bid": 0.0,
        "end_date_iso": "2099-01-01T00:00:00Z",
    }
    await pm.update_positions(_client_with(closed_market, orderbook_by_token={}))

    assert "mkt1" not in pm.data["positions"], "position should have been closed"
    closed = pm.data["closed"][-1]

    assert closed["result"] == "NEUTRAL", (
        f"position closed as {closed['result']} (pnl={closed['pnl']}) — an "
        "unresolvable position whose price was never actually quoted must be "
        "force-closed NEUTRAL, not booked as a full loss off a fabricated "
        "current_price of 0.00."
    )
    assert closed["pnl"] == pytest.approx(0.0)
    assert pm.data["capital"] == pytest.approx(100.0), (
        f"capital dropped to {pm.data['capital']} — phantom loss hit real "
        "capital accounting."
    )
    assert pm.data["daily"]["pnl"] == pytest.approx(0.0), (
        "phantom loss also poisoned the daily PnL bucket that CLAUDE.md's "
        "-15% daily stop-loss is measured against."
    )


# ── 3. Non-regression: normal paths must be untouched ───────────────────────

@pytest.mark.asyncio
async def test_synthetic_no_price_still_used_when_a_real_yes_bid_exists():
    """A genuine YES bid is still a valid price source: yes_ask = 1-0.55 = 0.45
    -> NO current_price = 1-0.45 = 0.55. Unchanged by the fix."""
    pm = _pm_with_no_position()
    market = {
        "resolved": False,
        "closed": False,
        "best_ask": 0.0,
        "best_bid": 0.55,
        "end_date_iso": "2099-01-01T00:00:00Z",
    }
    await pm.update_positions(_client_with(market, orderbook_by_token={}))

    assert pm.data["positions"]["mkt1"]["current_price"] == pytest.approx(0.55)


@pytest.mark.asyncio
async def test_real_no_orderbook_still_wins_over_any_fallback():
    """Non-regression for the 14th daily review (#33): when the live NO
    orderbook is available it remains the price source."""
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

    client.get_orderbook.assert_called_with("NO-TOKEN")
    assert pm.data["positions"]["mkt1"]["current_price"] == pytest.approx(0.62)


@pytest.mark.asyncio
async def test_quoted_yes_ask_still_drives_no_valuation():
    """Non-regression: a real Gamma best_ask keeps driving the synthetic NO
    price exactly as before (1 - 0.02 = 0.98)."""
    pm = _pm_with_no_position()
    market = {
        "resolved": False,
        "closed": False,
        "best_ask": 0.02,
        "best_bid": 0.0,
        "end_date_iso": "2099-01-01T00:00:00Z",
    }
    await pm.update_positions(_client_with(market, orderbook_by_token={}))

    assert pm.data["positions"]["mkt1"]["current_price"] == pytest.approx(0.98)
