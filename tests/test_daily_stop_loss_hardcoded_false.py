"""
Regression test: the live-order gate hardcoded `daily_loss_exceeded=False`
instead of asking `PositionManager.daily_loss_exceeded()`, completely
disabling CLAUDE.md's non-negotiable "Günlük stop-loss: -%15 → bot o gün
durur" rule.

Bug (agents/orchestrator.py):

    daily_stop = False  # devre dışı — kullanıcı talebi (2026-03-21)
    ...
    gate_result = check_live_gate(..., daily_loss_exceeded=daily_stop, ...)

This landed on `main` via a large unreviewed direct push ("full bot
update") that overwrote agents/orchestrator.py with an older code lineage
predating daily-review fix #15 ("wire daily -15% stop-loss into live gate,
was hardcoded to False"), silently reintroducing that exact bug. With this
hardcode in place, a real -15% (or worse) daily loss never blocks a single
new order: neither the direct-execution path in `_cycle()` nor the
dashboard-approval path in `_execute_approved_orders()` ever sees the
breach, no matter how much capital has actually been lost that day.

Fix: both call sites now pass
`self.position_manager.daily_loss_exceeded(self.daily_stop_loss)`.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def tmp_positions_file(tmp_path, monkeypatch):
    import core.position_manager as pm_mod
    monkeypatch.setattr(pm_mod, "DATA_FILE", tmp_path / "positions.json")
    monkeypatch.setenv("INITIAL_CAPITAL", "1000.0")


def _make_orchestrator(capital: float, daily_pnl: float):
    from agents.orchestrator import Orchestrator
    from core.position_manager import PositionManager

    orch = Orchestrator.__new__(Orchestrator)
    orch.position_manager = PositionManager()
    orch.position_manager.data["capital"] = capital
    orch.position_manager.data["daily"] = {"date": str(date.today()), "pnl": daily_pnl}
    orch.position_manager._save()

    orch.daily_stop_loss = 0.15
    orch._process_lock = MagicMock()
    orch._reentry_guard = MagicMock()
    orch._expiry_guard = MagicMock()
    orch._entry_window_policy = MagicMock()
    orch.max_open_positions = 5
    orch._order_timestamps = []
    orch._max_orders_per_hour = 100
    orch.client = MagicMock()
    return orch


@pytest.mark.asyncio
async def test_execute_approved_orders_sees_real_daily_stop_loss_breach():
    """Capital started the day at $1000 and has already dropped to $800
    (daily.pnl=-200, a genuine -20% day) -- well past the -15% threshold.
    The dashboard-approval execution path must forward that real breach
    into the live gate, not a hardcoded False."""
    orch = _make_orchestrator(capital=800.0, daily_pnl=-200.0)

    captured: dict = {}

    def fake_check_live_gate(**kwargs):
        captured["daily_loss_exceeded"] = kwargs["daily_loss_exceeded"]
        from control_plane.types import LiveGateResult, LiveGateCheck
        return LiveGateResult(
            passed=False,
            checks=[LiveGateCheck(name="DAILY_STOP_LOSS", passed=False, reason="DAILY_STOP_LOSS")],
        )

    fake_order = {
        "id": "order-1",
        "market_id": "mkt-1",
        "token_id": "tok-1",
        "amount": 10.0,
        "entry_price": 0.5,
        "direction": "YES",
        "question": "Test market?",
        "end_date_iso": "",
    }

    with patch("agents.orchestrator._get_approved_orders", return_value=[fake_order]), \
         patch("agents.orchestrator._mark_order_executed"), \
         patch("agents.orchestrator._block_order_execution"), \
         patch("agents.orchestrator.check_live_gate", side_effect=fake_check_live_gate):
        await orch._execute_approved_orders()

    assert captured["daily_loss_exceeded"] is True, (
        "_execute_approved_orders() must pass the real "
        "PositionManager.daily_loss_exceeded() result into the live gate, "
        "not a hardcoded False -- otherwise a genuine -15% daily breach "
        "never blocks dashboard-approved orders."
    )
    # And the order must actually have been blocked, not executed.
    orch.client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_execute_approved_orders_does_not_falsely_block_when_within_limit():
    """Symmetry: a day well within the -15% budget must not be hardcoded
    to True either -- confirms the fix reads the real state, not just
    flips the hardcode the other way."""
    orch = _make_orchestrator(capital=990.0, daily_pnl=-10.0)

    captured: dict = {}

    def fake_check_live_gate(**kwargs):
        captured["daily_loss_exceeded"] = kwargs["daily_loss_exceeded"]
        from control_plane.types import LiveGateResult
        return LiveGateResult(passed=True, checks=[])

    fake_order = {
        "id": "order-2",
        "market_id": "mkt-2",
        "token_id": "tok-2",
        "amount": 10.0,
        "entry_price": 0.5,
        "direction": "YES",
        "question": "Test market 2?",
        "end_date_iso": "",
    }
    async def fake_place_order(**kwargs):
        return {"order_id": "clob-1", "amount": 10.0, "price": 0.5, "status": "matched"}
    orch.client.place_order = fake_place_order

    with patch("agents.orchestrator._get_approved_orders", return_value=[fake_order]), \
         patch("agents.orchestrator._mark_order_executed"), \
         patch("agents.orchestrator._block_order_execution"), \
         patch("agents.orchestrator.check_live_gate", side_effect=fake_check_live_gate):
        await orch._execute_approved_orders()

    assert captured["daily_loss_exceeded"] is False
