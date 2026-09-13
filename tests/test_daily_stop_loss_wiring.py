"""
Regression test for the daily -15% stop-loss gate wiring in Orchestrator.

Bug: `check_live_gate`'s `daily_loss_exceeded` parameter was hardcoded to
`False` at both live-order call sites in `agents/orchestrator.py` (the
per-cycle candidate loop and `_execute_approved_orders`), annotated
"devre dışı — kullanıcı talebi (2026-03-21)". This directly contradicted
CLAUDE.md's unmodifiable core rule ("Günlük stop-loss: -%15 → bot o gün
durur") — `PositionManager.daily_loss_exceeded()` was fully implemented
and unit-tested in isolation (tests/test_position_manager.py) but never
consulted by the live order path, so a -15% day never actually stopped
the bot.

This locks in that `_execute_approved_orders` passes the live
`position_manager.daily_loss_exceeded(self.daily_stop_loss)` result into
`check_live_gate`, so a future accidental hardcoded-False regression
fails a test instead of only being caught by inspection.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import agents.orchestrator as orchestrator_module
from agents.orchestrator import Orchestrator


def _make_orchestrator(daily_loss_exceeded: bool) -> Orchestrator:
    orch = Orchestrator.__new__(Orchestrator)
    orch.daily_stop_loss = 0.15
    orch.position_manager = SimpleNamespace(
        available_capital=lambda: 100.0,
        open_position_count=lambda: 0,
        has_position=lambda market_id: False,
        daily_loss_exceeded=lambda threshold: daily_loss_exceeded,
        add_position=lambda *a, **k: None,
    )
    orch._process_lock = None
    orch.max_open_positions = 5
    orch._order_timestamps = []
    orch._max_orders_per_hour = 10
    orch._reentry_guard = SimpleNamespace(mark_traded=lambda market_id: None)
    orch._expiry_guard = None
    orch._entry_window_policy = None
    orch.client = SimpleNamespace(place_order=AsyncMock(return_value=None))
    return orch


def _approved_order(oid: str, market_id: str) -> dict:
    return {
        "id": oid,
        "market_id": market_id,
        "token_id": "tok",
        "amount": 10.0,
        "entry_price": 0.5,
        "direction": "YES",
        "question": "Test market?",
    }


@pytest.mark.asyncio
async def test_daily_stop_loss_exceeded_reaches_live_gate():
    orch = _make_orchestrator(daily_loss_exceeded=True)
    captured = {}

    def fake_check_live_gate(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(passed=False, blockers=["daily_stop"])

    with patch.object(orchestrator_module, "_get_approved_orders", return_value=[_approved_order("o1", "0xtest")]), \
         patch.object(orchestrator_module, "check_live_gate", side_effect=fake_check_live_gate), \
         patch.object(orchestrator_module, "_block_order_execution") as mock_block:
        await orch._execute_approved_orders()

    assert captured["daily_loss_exceeded"] is True
    mock_block.assert_called_once()
    orch.client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_daily_stop_loss_not_exceeded_reaches_live_gate():
    orch = _make_orchestrator(daily_loss_exceeded=False)
    orch.client.place_order = AsyncMock(return_value={"id": "order-1"})
    captured = {}

    def fake_check_live_gate(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(passed=True, blockers=[])

    with patch.object(orchestrator_module, "_get_approved_orders", return_value=[_approved_order("o2", "0xtest2")]), \
         patch.object(orchestrator_module, "check_live_gate", side_effect=fake_check_live_gate), \
         patch.object(orchestrator_module, "_mark_order_executed") as mock_mark:
        await orch._execute_approved_orders()

    assert captured["daily_loss_exceeded"] is False
    orch.client.place_order.assert_called_once()
    mock_mark.assert_called_once_with("o2")
