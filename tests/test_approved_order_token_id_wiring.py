"""
Regression test: `_execute_approved_orders()` must copy `token_id` onto the
saved position, same as the direct/automatic execution path already does.

Bug: `PolymarketClient.place_order()` returns a dict with only
`order_id, market_id, outcome, amount, price, status` — it never includes
`token_id` (see core/polymarket_client.py: both the live-order return at
line ~557 and the `_simulate()` return at line ~831). The direct execution
path in `agents/orchestrator.py` (per-cycle candidate loop) compensates for
this by explicitly doing `order["token_id"] = token_id or ""` right after
`place_order()`, before calling `add_position()`. `_execute_approved_orders()`
(the dashboard-approval execution path, which runs every cycle) was missing
that same line, so every position opened via that path was saved with
`token_id=""`.

`PositionManager.add_position()` stores `order.get("token_id", "")` verbatim,
and `PositionManager.update_positions()` uses `pos.get("token_id")` as the
primary key to fetch the real NO orderbook (tests/test_no_valuation_live_orderbook.py
locks in why this must be the position's own recorded token_id, since
`get_market()` never populates `no_token_id`). An empty token_id there
silently falls back to the stale Gamma-derived `1 - yes_ask` valuation —
reintroducing the exact two bugs already fixed in #33 (92a4589, "NO position
live valuation never queried the real orderbook") and #34 (5a9b829,
"unquoted NO position valued at 0.00 was force-closed as a full loss")
through this second, untested code path.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import agents.orchestrator as orchestrator_module
from agents.orchestrator import Orchestrator


def _make_orchestrator(placed_order: dict) -> tuple[Orchestrator, dict]:
    saved_positions = {}

    def fake_add_position(market_id, order, question, **kwargs):
        saved_positions[market_id] = order

    orch = Orchestrator.__new__(Orchestrator)
    orch.daily_stop_loss = 0.15
    orch.position_manager = SimpleNamespace(
        available_capital=lambda: 100.0,
        open_position_count=lambda: 0,
        has_position=lambda market_id: False,
        daily_loss_exceeded=lambda threshold: False,
        add_position=fake_add_position,
    )
    orch._process_lock = None
    orch.max_open_positions = 5
    orch._order_timestamps = []
    orch._max_orders_per_hour = 10
    orch._reentry_guard = SimpleNamespace(mark_traded=lambda market_id: None)
    orch._expiry_guard = None
    orch._entry_window_policy = None
    orch.client = SimpleNamespace(place_order=AsyncMock(return_value=dict(placed_order)))
    return orch, saved_positions


def _approved_order(oid: str, market_id: str, token_id: str) -> dict:
    return {
        "id": oid,
        "market_id": market_id,
        "token_id": token_id,
        "amount": 10.0,
        "entry_price": 0.5,
        "direction": "NO",
        "question": "Test market?",
    }


@pytest.mark.asyncio
async def test_execute_approved_orders_copies_token_id_onto_saved_position():
    # Mirrors the real PolymarketClient.place_order() return shape — no
    # token_id key, exactly as core/polymarket_client.py actually returns.
    placed_order = {
        "order_id": "abc123",
        "market_id": "0xtest",
        "outcome": "NO",
        "amount": 10.0,
        "price": 0.5,
        "status": "SIMULATED",
    }
    orch, saved_positions = _make_orchestrator(placed_order)

    with patch.object(
        orchestrator_module,
        "_get_approved_orders",
        return_value=[_approved_order("o1", "0xtest", "no-token-xyz")],
    ), patch.object(
        orchestrator_module,
        "check_live_gate",
        return_value=SimpleNamespace(passed=True, blockers=[]),
    ), patch.object(
        orchestrator_module, "_mark_order_executed"
    ):
        await orch._execute_approved_orders()

    orch.client.place_order.assert_awaited_once()
    assert "0xtest" in saved_positions
    assert saved_positions["0xtest"]["token_id"] == "no-token-xyz", (
        "approved-order execution path must copy the approved order's "
        "token_id onto the order dict before add_position(), same as the "
        "direct execution path — otherwise update_positions() can never "
        "find the real NO orderbook for this position"
    )
