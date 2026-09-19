"""
Regression test: `_execute_approved_orders()` must copy the signal's `edge`
onto the saved position, same as the direct/automatic execution path already
does — the same "wiring" bug class already fixed for `token_id` in this exact
function (see tests/test_approved_order_token_id_wiring.py).

Bug (agents/orchestrator.py::Orchestrator._execute_approved_orders()):

    self.position_manager.add_position(market_id, order, question, signal_price=price)

`control_plane/approval_queue.py`'s `enqueue()` already stores the signal's
real edge on every queue entry:

    entry = {
        ...
        "edge": order_request.get("edge", 0),
        ...
    }

but `_execute_approved_orders()` never read `order_req.get("edge")` back out,
so every position opened through this dashboard-approval path was saved with
NO "edge" key at all (not even 0.0 — genuinely absent), unlike the direct
execution path a few hundred lines up in the same file, which always passes
`edge=signal.edge` to `add_position()`.

Concrete failure scenario: a signal with a genuine 0.22 edge gets approved
and executed through this path. `PositionManager.add_position()` only writes
`pos["edge"]` `if edge is not None`, so the saved position (and later, the
closed trade) has no "edge" key. When `TradeAnalyzer.analyze_trade()` runs on
that closed trade (`signal_data=trade`), `signal_data.get("edge", 0)` silently
defaults to 0 — even though the trade actually had a 0.22 edge. This corrupts
`_determine_root_cause()` (every loss gets blamed on "INSUFFICIENT_EDGE"/
"NO_THIN_EDGE" regardless of the real edge) and `_match_pattern()` (every
loss matches "LOW_EDGE_LOSS", no win can ever match "HIGH_EDGE_WIN"),
breaking the post-trade learning loop CLAUDE.md documents for this path.

Fix: read `order_req.get("edge")` and pass it through to `add_position()`.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def _make_orchestrator(placed_order: dict) -> tuple:
    saved_positions = {}
    add_position_kwargs = {}

    def fake_add_position(market_id, order, question, **kwargs):
        saved_positions[market_id] = order
        add_position_kwargs[market_id] = kwargs

    import agents.orchestrator as orchestrator_module
    orch = orchestrator_module.Orchestrator.__new__(orchestrator_module.Orchestrator)
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
    return orch, saved_positions, add_position_kwargs


def _approved_order(oid: str, market_id: str, token_id: str, edge: float) -> dict:
    return {
        "id": oid,
        "market_id": market_id,
        "token_id": token_id,
        "amount": 10.0,
        "entry_price": 0.5,
        "direction": "NO",
        "question": "Test market?",
        "edge": edge,
    }


@pytest.mark.asyncio
async def test_execute_approved_orders_copies_edge_onto_saved_position():
    # Mirrors the real PolymarketClient.place_order() return shape — no
    # "edge" key, exactly as core/polymarket_client.py actually returns.
    placed_order = {
        "order_id": "abc123",
        "market_id": "0xtest",
        "outcome": "NO",
        "amount": 10.0,
        "price": 0.5,
        "status": "SIMULATED",
    }
    orch, saved_positions, add_position_kwargs = _make_orchestrator(placed_order)

    import agents.orchestrator as orchestrator_module
    with patch.object(
        orchestrator_module,
        "_get_approved_orders",
        return_value=[_approved_order("o1", "0xtest", "no-token-xyz", edge=0.22)],
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
    assert add_position_kwargs["0xtest"].get("edge") == pytest.approx(0.22), (
        "approved-order execution path must copy the approved order's real "
        "edge onto the position, same as the direct execution path — "
        "otherwise TradeAnalyzer permanently sees edge=0 for every trade "
        "opened through this path, regardless of the real edge at entry"
    )


@pytest.mark.asyncio
async def test_missing_edge_key_falls_back_to_none_not_zero():
    """An older/malformed queue entry with no "edge" key at all must not be
    coerced into a fake 0.0 — that's indistinguishable from a genuine
    zero-edge trade. add_position() already treats None specially (it only
    writes pos["edge"] when the value is not None), so the wiring must
    preserve that distinction rather than defaulting to 0."""
    placed_order = {
        "order_id": "abc123",
        "market_id": "0xtest2",
        "outcome": "NO",
        "amount": 10.0,
        "price": 0.5,
        "status": "SIMULATED",
    }
    orch, saved_positions, add_position_kwargs = _make_orchestrator(placed_order)

    order_no_edge = _approved_order("o2", "0xtest2", "no-token-xyz", edge=0.0)
    del order_no_edge["edge"]

    import agents.orchestrator as orchestrator_module
    with patch.object(
        orchestrator_module, "_get_approved_orders", return_value=[order_no_edge]
    ), patch.object(
        orchestrator_module,
        "check_live_gate",
        return_value=SimpleNamespace(passed=True, blockers=[]),
    ), patch.object(
        orchestrator_module, "_mark_order_executed"
    ):
        await orch._execute_approved_orders()

    assert add_position_kwargs["0xtest2"].get("edge") is None
