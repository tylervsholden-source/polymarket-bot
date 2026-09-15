"""
Regression test (44th daily review): Orchestrator._cycle()'s Phase A
(market making) placed real GTC orders via MakerEngine.refresh_quotes() ->
client.place_passive_order() without ever checking the daily -15%
stop-loss, the process lock, or the account-wide max-open-positions cap —
the exact same class of bug the 43rd daily review (PR #75) found and fixed
in Phase B (bond scan).

Bug (agents/orchestrator.py, Phase A of Orchestrator._cycle(), pre-fix):

    if self._maker_enabled and self._maker_engine and self._is_live_trading():
        maker_capital = self.position_manager.pool_available("maker")
        maker_stats = await self._maker_engine.refresh_quotes(...)

Unlike Phase B's `_bond_cycle()` (post-43rd-review) and
`_execute_approved_orders()`, this dispatch only checked the master
live/sim switch — never `daily_loss_exceeded()`, `self._process_lock.is_mine()`,
or `open_position_count() >= self.max_open_positions`. A losing day tripping
the daily -15% stop-loss halts every other real-order path immediately, but
with `MAKER_ENABLED=true` (and `MAKER_CAPITAL_PCT` > 0) maker quotes would
keep refreshing — placing real two-sided GTC orders — every single cycle.

Fix: the same three guards used in `_bond_cycle()` were added directly to
Phase A's dispatch condition in `_cycle()`.

This test drives `Orchestrator._cycle()` down to the hybrid-strategy phases
(mirroring tests/test_bond_cycle_live_trading_gate.py's fixture) with bond
disabled and maker enabled, and asserts `MakerEngine.refresh_quotes()` is
(not) invoked according to each guard.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import agents.orchestrator as orchestrator_module
from agents.orchestrator import Orchestrator


def _make_orchestrator(
    *,
    daily_loss_exceeded: bool = False,
    process_lock_is_mine: bool = True,
    open_position_count: int = 0,
    max_open_positions: int = 5,
) -> Orchestrator:
    """Minimal Orchestrator wired up just enough to drive `_cycle()` down to
    Phase A/B, with a single pre-filtered candidate that produces zero
    coordinator signals (so the heavy signal-execution loop is a no-op and
    only the hybrid-strategy phases at the end of `_cycle()` matter)."""
    orch = Orchestrator.__new__(Orchestrator)

    orch._is_live_trading = lambda: True
    orch._is_simulation_running = lambda: True
    orch._read_control = lambda: {"min_bet": 1.0}

    orch._cycle_count = 0  # -> 1 after increment, so bond's `% 5 == 0` does NOT fire
    orch._order_timestamps = []
    orch._consecutive_losses = 0
    orch._max_consecutive_losses = 3
    orch._consecutive_wins_per_coin = {}
    orch._last_loss_slots = set()

    orch.position_manager = SimpleNamespace(
        data={"capital": 100.0, "closed": [], "positions": {}},
        update_positions=AsyncMock(),
        open_position_count=lambda: open_position_count,
        available_capital=lambda: 100.0,
        locked_capital=lambda: 0.0,
        pool_position_count=lambda pool: 0,
        pool_available=lambda pool: 100.0,
        daily_loss_exceeded=lambda threshold: daily_loss_exceeded,
    )
    orch.max_open_positions = max_open_positions
    orch.min_market_volume = 5000.0
    orch.daily_stop_loss = 0.15
    orch.interval = 60
    orch._process_lock = SimpleNamespace(is_mine=lambda: process_lock_is_mine)

    fake_market = {"condition_id": "maker-gate-test-market", "question": "Bitcoin Up or Down"}
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

    # Hybrid strategy flags: maker enabled (under test), bond disabled.
    orch._maker_enabled = True
    orch._maker_engine = SimpleNamespace(refresh_quotes=AsyncMock(return_value={"placed": 0, "cancelled": 0, "skipped": 0}))
    orch._bond_enabled = False
    orch._bond_scanner = None

    orch._finalize_cycle = AsyncMock()

    return orch


@pytest.mark.asyncio
async def test_maker_cycle_skips_when_daily_stop_loss_exceeded():
    orch = _make_orchestrator(daily_loss_exceeded=True)

    with patch.object(orchestrator_module, "_get_approved_orders", return_value=[]), \
         patch.object(orchestrator_module, "_cleanup_expired_orders"):
        await orch._cycle()

    orch._maker_engine.refresh_quotes.assert_not_called()


@pytest.mark.asyncio
async def test_maker_cycle_skips_when_process_lock_not_mine():
    orch = _make_orchestrator(process_lock_is_mine=False)

    with patch.object(orchestrator_module, "_get_approved_orders", return_value=[]), \
         patch.object(orchestrator_module, "_cleanup_expired_orders"):
        await orch._cycle()

    orch._maker_engine.refresh_quotes.assert_not_called()


@pytest.mark.asyncio
async def test_maker_cycle_skips_when_account_wide_position_cap_reached():
    orch = _make_orchestrator(open_position_count=5, max_open_positions=5)

    with patch.object(orchestrator_module, "_get_approved_orders", return_value=[]), \
         patch.object(orchestrator_module, "_cleanup_expired_orders"):
        await orch._cycle()

    orch._maker_engine.refresh_quotes.assert_not_called()


@pytest.mark.asyncio
async def test_maker_cycle_still_quotes_when_all_gates_pass():
    """Sanity check: the fix must not disable legitimate maker quoting."""
    orch = _make_orchestrator(
        daily_loss_exceeded=False,
        process_lock_is_mine=True,
        open_position_count=0,
        max_open_positions=5,
    )

    with patch.object(orchestrator_module, "_get_approved_orders", return_value=[]), \
         patch.object(orchestrator_module, "_cleanup_expired_orders"):
        await orch._cycle()

    orch._maker_engine.refresh_quotes.assert_called_once()
