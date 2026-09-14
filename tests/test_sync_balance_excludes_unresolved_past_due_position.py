"""
Regression test: Orchestrator._sync_real_balance() silently drops a still-open
position's amount from tracked capital once its market window has ended but
before CLOB has actually resolved it.

Bug (agents/orchestrator.py::Orchestrator._sync_real_balance()):

    for p in self.position_manager.data.get("positions", {}).values():
        amt = p.get("amount", 0)
        question = p.get("question", "")
        _, end_utc = parse_market_times(question)
        if end_utc and now_utc > end_utc:
            # Market süresi geçmiş — payout CLOB'a düşmüş olabilir
            # locked'a EKLEMİYORUZ
            continue
        locked += amt
    new_capital = balance + locked

The comment's assumption -- "the payout has already landed in the CLOB
balance, so counting it as locked would double-count it" -- only holds once
a position has actually been resolved and moved out of
`position_manager.data["positions"]` (into `data["closed"]`, with its pnl
already added to `data["capital"]` by `PositionManager._close_position()`).
But `update_positions()` always runs earlier in the same cycle and removes
any position it can actually resolve. Anything still sitting in
`data["positions"]` by the time `_sync_real_balance()` looks at it -- even
one whose market window has ended -- is, by construction, a position
`update_positions()` could NOT yet resolve (CLOB tokens.winner not
available yet; logged elsewhere as WAITING_RESOLUTION / STALE_UNRESOLVED).
Its USDC has NOT been redeemed and is NOT yet part of the real CLOB
balance -- it is still committed to the open position, same as every other
open position. Excluding it from `locked` here (while still including it
in `position_manager.locked_capital()`/`available_capital()` everywhere
else) makes `data["capital"]` -- and therefore `available_capital()` --
silently evaporate by the position's dollar amount for as long as
resolution is pending, corrupting Kelly sizing, AutonomousDecisionEngine's
SURVIVAL-mode threshold, and PositionManager.daily_loss_exceeded()
(CLAUDE.md's non-negotiable daily -15% stop-loss uses
`data["capital"] - daily.pnl` as its denominator).

This is a routine occurrence, not a rare corner case: crypto up/down
markets run on 5-15 minute windows against a 60-120s orchestrator cycle,
and CLOB resolution has real latency (position_manager.update_positions()
itself has WAITING_RESOLUTION / STALE_UNRESOLVED / 45-minute-timeout
fallback logic specifically because this window is common).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def tmp_positions_file(tmp_path, monkeypatch):
    import core.position_manager as pm_mod
    monkeypatch.setattr(pm_mod, "DATA_FILE", tmp_path / "positions.json")
    monkeypatch.setenv("INITIAL_CAPITAL", "50.0")


def _make_orchestrator(get_real_balance_return: float, question: str, amount: float):
    from agents.orchestrator import Orchestrator
    from core.position_manager import PositionManager

    orch = Orchestrator.__new__(Orchestrator)
    orch.client = MagicMock()
    orch.client.get_real_balance.return_value = get_real_balance_return
    orch.position_manager = PositionManager()
    orch.position_manager.data["capital"] = 50.0
    orch.position_manager.data["positions"]["mkt1"] = {
        "order_id": "o1",
        "question": question,
        "outcome": "YES",
        "amount": amount,
        "entry_price": 0.5,
        "status": "MATCHED",
    }
    orch.position_manager._save()
    return orch


@pytest.mark.asyncio
async def test_past_due_unresolved_position_still_counts_as_locked_capital():
    """The one open position's market window ended months ago (still
    reliably in the past regardless of when this suite runs), but the
    position is still open in position_manager.data["positions"] --
    exactly the "CLOB hasn't resolved it yet" state. Its $4 has NOT been
    redeemed into the real CLOB balance, so the mocked balance (46.0)
    reflects only the free cash. True total capital must still be
    $46 (free) + $4 (still locked in the unresolved position) = $50 --
    unchanged from before the sync, since nothing has actually resolved.
    """
    orch = _make_orchestrator(
        get_real_balance_return=46.0,
        question="Bitcoin Up or Down - March 16, 7:10PM-7:15PM ET",
        amount=4.0,
    )

    await orch._sync_real_balance()

    assert orch.position_manager.data["capital"] == pytest.approx(50.0), (
        f"capital was corrupted to {orch.position_manager.data['capital']} — "
        "a still-open position whose market window has merely ended (but "
        "which CLOB has not yet resolved) must still count as locked "
        "capital, not vanish from the tracked total."
    )


@pytest.mark.asyncio
async def test_still_open_future_position_unaffected():
    """Non-regression: a position whose market window has NOT ended yet
    must still be counted as locked, same as before this fix."""
    orch = _make_orchestrator(
        get_real_balance_return=46.0,
        question="Bitcoin Up or Down - March 16, 7:10PM-7:15PM ET",
        amount=4.0,
    )
    # Force the "future" branch by using a question with no parseable time
    # at all — parse_market_times returns (None, None), so the position
    # was never subject to the past-due exclusion in the first place.
    orch.position_manager.data["positions"]["mkt1"]["question"] = "Bitcoin up?"
    orch.position_manager._save()

    await orch._sync_real_balance()

    assert orch.position_manager.data["capital"] == pytest.approx(50.0)
