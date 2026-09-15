"""
Regression test (42nd daily review): Orchestrator._cycle()'s bond-scan phase
(Phase B) placed real bond orders without ever checking `self._is_live_trading()`
— the exact master live-trading switch that gates every other real-order path
in the same method.

Bug (agents/orchestrator.py, end of Orchestrator._cycle()):

    # Phase A: Market Making — refresh two-sided quotes (every cycle)
    if self._maker_enabled and self._maker_engine and self._is_live_trading():
        ...
    # Phase B: Bond scan — every 5th cycle (~5 min)
    if self._bond_enabled and self._bond_scanner and self._cycle_count % 5 == 0:
        try:
            await self._bond_cycle()
        ...

Phase A (market making) explicitly requires `self._is_live_trading()` before
touching real capital. Phase B (bond scanning), right below it, does not —
it only checks that the bond feature flag is on and it is a multiple-of-5
cycle. `Orchestrator._bond_cycle()` calls
`self.client.place_passive_order(...)` directly (no `check_live_gate()` call
at all — unlike the directional order path and `_execute_approved_orders()`,
which both re-check `live_trading` via `check_live_gate`'s own
`live_trading` check in addition to the outer `_is_live_trading()` guard).

Net effect: with `BOND_ENABLED=true`, once real CLOB credentials are wired
up (`self.client._clob` set, so `place_passive_order` is not simulated),
setting `control.json`'s `live_trading` to `false` — the dashboard's pause /
master kill switch, also tripped indirectly by `LIVE_TRADING_ENABLED=false`
or a stale/failing readiness verdict — silently fails to stop the bond
strategy from continuing to place real orders every 5th cycle while
`simulation_running` stays true. This is the same trade-safety class as
CLAUDE.md's "Günlük stop-loss: -%15 → bot o gün durur" rule: the entire
point of the live/sim distinction is that flipping it off stops real money
from moving, and Phase B was the one order-placing path in `_cycle()` that
did not honor it.

This test locks in that Phase B is gated by the same `self._is_live_trading()`
check as Phase A, by driving a minimally-mocked `Orchestrator._cycle()` and
asserting `_bond_cycle()` is (not) invoked according to the live-trading flag.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import agents.orchestrator as orchestrator_module
from agents.orchestrator import Orchestrator


def _make_orchestrator(*, is_live_trading: bool, is_simulation_running: bool = True) -> Orchestrator:
    """Minimal Orchestrator wired up just enough to drive `_cycle()` down to
    Phase A/B, with a single pre-filtered candidate that produces zero
    coordinator signals (so the heavy signal-execution loop is a no-op and
    only the hybrid-strategy phases at the end of `_cycle()` matter)."""
    orch = Orchestrator.__new__(Orchestrator)

    orch._is_live_trading = lambda: is_live_trading
    orch._is_simulation_running = lambda: is_simulation_running
    orch._read_control = lambda: {"min_bet": 1.0}

    orch._cycle_count = 4  # -> 5 after increment, so `% 5 == 0` fires
    orch._order_timestamps = []
    orch._consecutive_losses = 0
    orch._max_consecutive_losses = 3
    orch._consecutive_wins_per_coin = {}
    orch._last_loss_slots = set()

    orch.position_manager = SimpleNamespace(
        data={"capital": 100.0, "closed": [], "positions": {}},
        update_positions=AsyncMock(),
        open_position_count=lambda: 0,
        available_capital=lambda: 100.0,
        locked_capital=lambda: 0.0,
        pool_position_count=lambda pool: 0,
    )
    orch.max_open_positions = 5
    orch.min_market_volume = 5000.0
    orch.daily_stop_loss = 0.15
    orch.interval = 60

    fake_market = {"condition_id": "bond-gate-test-market", "question": "Bitcoin Up or Down"}
    orch.client = SimpleNamespace(
        get_active_markets=AsyncMock(return_value=[fake_market]),
        get_orderbook=lambda token_id: None,
    )
    # Bypass the real keyword/time/price filtering — feed the fake market
    # straight through as the only candidate.
    orch._pre_filter = lambda markets: list(markets)

    fake_coord_result = SimpleNamespace(
        signal_result=SimpleNamespace(signals=[]),
        approved_signals=[],
        summary=lambda: "no signals",
    )
    orch.coordinator = SimpleNamespace(run_cycle=AsyncMock(return_value=fake_coord_result))

    orch._record_shadow_decisions = lambda *a, **k: None
    orch._sim_trades = []

    orch.arb_engine = SimpleNamespace(kelly=SimpleNamespace(update_streak=lambda closed: None))
    orch._walk_forward = SimpleNamespace(validate=lambda closed: {"recommendation": "OK", "test_wr": 0.0})

    orch.latency_arb = SimpleNamespace(_running=False)

    # Hybrid strategy flags: maker disabled (not under test), bond enabled.
    orch._maker_enabled = False
    orch._maker_engine = None
    orch._bond_enabled = True
    orch._bond_scanner = SimpleNamespace(MAX_POSITIONS=3)  # just needs to be truthy
    orch._bond_cycle = AsyncMock()

    orch._finalize_cycle = AsyncMock()

    return orch


@pytest.mark.asyncio
async def test_bond_cycle_not_invoked_when_live_trading_disabled():
    """control.json live_trading=false (bot paused) must stop bond orders too."""
    orch = _make_orchestrator(is_live_trading=False, is_simulation_running=True)

    with patch.object(orchestrator_module, "_get_approved_orders", return_value=[]), \
         patch.object(orchestrator_module, "_cleanup_expired_orders"):
        await orch._cycle()

    orch._bond_cycle.assert_not_called()


@pytest.mark.asyncio
async def test_bond_cycle_invoked_when_live_trading_enabled():
    """Sanity check: the fix must not disable legitimate bond trading."""
    orch = _make_orchestrator(is_live_trading=True, is_simulation_running=True)

    with patch.object(orchestrator_module, "_get_approved_orders", return_value=[]), \
         patch.object(orchestrator_module, "_cleanup_expired_orders"):
        await orch._cycle()

    orch._bond_cycle.assert_called_once()
