"""
Regression test (43rd daily review): Orchestrator._bond_cycle() placed real
bond orders via client.place_passive_order() without ever checking the daily
-15% stop-loss, the process lock, or the account-wide max-open-positions cap.

The 42nd daily review (commit 7f2a596) already found and fixed a sibling bug
in the same area — Phase B's dispatch in Orchestrator._cycle() was missing
the self._is_live_trading() guard that Phase A (market making) has — and
explicitly noted as a follow-up that _bond_cycle() itself still skipped the
daily stop-loss and process-lock checks that check_live_gate() enforces for
every other real-order path. This test picks up that follow-up directly.

Failure scenario before this fix: a losing day trips
position_manager.daily_loss_exceeded(0.15) — every other real-order path
stops immediately — but the 5th-cycle bond scan (while live_trading is on)
kept placing real bond orders, silently blowing through the daily
stop-loss the rest of the bot had just enforced. Likewise a second process
holding a stale lock, or a directional loop that filled the last of the 5
account-wide position slots earlier in the very same cycle, did not stop
bond orders from being placed.

A second, independent bug was observed transiently during this review: an
external commit briefly landed on `main` (and was later reverted) that
overwrote agents/orchestrator.py wholesale and dropped the 42nd review's
Phase A/B live-trading gate too. That kind of wholesale rewrite is exactly
what a full-`_cycle()` mock (fragile against unrelated changes elsewhere in
a ~2500-line method) would miss, so this file also adds a narrow structural
check that locks in just the shape of the Phase B dispatch condition,
independent of the rest of `_cycle()`.
"""
from __future__ import annotations

import inspect
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agents.orchestrator import Orchestrator


def _make_bond_orchestrator(
    *,
    daily_loss_exceeded: bool = False,
    process_lock_is_mine: bool = True,
    open_position_count: int = 0,
    max_open_positions: int = 5,
) -> Orchestrator:
    orch = Orchestrator.__new__(Orchestrator)

    orch.position_manager = SimpleNamespace(
        daily_loss_exceeded=lambda threshold: daily_loss_exceeded,
        open_position_count=lambda: open_position_count,
        pool_available=lambda pool: 100.0,
        pool_position_count=lambda pool: 0,
        has_position=lambda market_id: False,
        add_position=lambda *a, **k: None,
    )
    orch.max_open_positions = max_open_positions
    orch.daily_stop_loss = 0.15
    orch._process_lock = SimpleNamespace(is_mine=lambda: process_lock_is_mine)
    orch._reentry_guard = SimpleNamespace(
        is_blocked=lambda market_id: False,
        mark_traded=lambda market_id: None,
    )

    fake_opportunity = SimpleNamespace(
        condition_id="bond-market-1",
        token_id="bond-token-1",
        side="YES",
        price=0.95,
        question="Will BTC close above $50k by Friday?",
        expected_yield=0.05,
        days_to_resolve=2.0,
    )
    orch._bond_scanner = SimpleNamespace(
        MAX_POSITIONS=3,
        scan=AsyncMock(return_value=[fake_opportunity]),
    )
    orch.client = SimpleNamespace(place_passive_order=AsyncMock(
        return_value={"order_id": "o1", "amount": 4.75, "status": "LIVE"}
    ))

    return orch


@pytest.mark.asyncio
async def test_bond_cycle_skips_when_daily_stop_loss_exceeded():
    orch = _make_bond_orchestrator(daily_loss_exceeded=True)
    await orch._bond_cycle()
    orch.client.place_passive_order.assert_not_called()
    orch._bond_scanner.scan.assert_not_called()


@pytest.mark.asyncio
async def test_bond_cycle_skips_when_process_lock_not_mine():
    orch = _make_bond_orchestrator(process_lock_is_mine=False)
    await orch._bond_cycle()
    orch.client.place_passive_order.assert_not_called()
    orch._bond_scanner.scan.assert_not_called()


@pytest.mark.asyncio
async def test_bond_cycle_skips_when_account_wide_position_cap_reached():
    orch = _make_bond_orchestrator(open_position_count=5, max_open_positions=5)
    await orch._bond_cycle()
    orch.client.place_passive_order.assert_not_called()
    orch._bond_scanner.scan.assert_not_called()


@pytest.mark.asyncio
async def test_bond_cycle_still_trades_when_all_gates_pass():
    """Sanity check: the fix must not disable legitimate bond trading."""
    orch = _make_bond_orchestrator(
        daily_loss_exceeded=False,
        process_lock_is_mine=True,
        open_position_count=0,
        max_open_positions=5,
    )
    await orch._bond_cycle()
    orch.client.place_passive_order.assert_called_once()


def test_cycle_phase_b_dispatch_still_requires_is_live_trading():
    """Locks in the *shape* of the Phase B dispatch condition in
    Orchestrator._cycle() directly, independent of the rest of that
    ~2500-line method. A wholesale rewrite of _cycle() that drops the
    self._is_live_trading() gate on Phase B (as observed transiently on
    `main` during this review, before being reverted) fails this test even
    if nothing else about _bond_cycle()'s internals changes.
    """
    source = inspect.getsource(Orchestrator._cycle)
    match = re.search(
        r"if\s*\(?\s*self\._bond_enabled\s+and\s+self\._bond_scanner"
        r"[\s\S]{0,200}?:\s*\n",
        source,
    )
    assert match is not None, "Phase B bond-scan dispatch condition not found in _cycle()"
    dispatch_condition = match.group(0)
    assert "self._is_live_trading()" in dispatch_condition, (
        "Phase B (bond scan) dispatch lost its self._is_live_trading() gate — "
        "real bond orders would keep firing even with the master live/sim "
        "kill switch off. See 42nd/43rd daily review."
    )
