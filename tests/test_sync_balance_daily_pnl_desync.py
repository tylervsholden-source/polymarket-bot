"""
Regression test: Orchestrator._sync_real_balance() corrupts the daily -15%
stop-loss denominator by overwriting `position_manager.data["capital"]`
without also adjusting `data["daily"]["pnl"]` by the same amount.

Bug (agents/orchestrator.py::Orchestrator._sync_real_balance()):

    new_capital = balance + locked
    old_capital = self.position_manager.data.get("capital", 0)

    # Capital'i güncelle
    self.position_manager.data["capital"] = round(new_capital, 4)
    self.position_manager._save()

`PositionManager.daily_loss_exceeded()` (CLAUDE.md's non-negotiable
"Günlük stop-loss: -%15 → bot o gün durur") computes the day's starting
capital as `data["capital"] - data["daily"]["pnl"]`, then measures today's
loss against that baseline. Every OTHER place that changes `data["capital"]`
(`_close_position()`, `_close_position_neutral()`) moves `data["daily"]["pnl"]`
by the exact same amount in the same call, preserving that invariant.

`_sync_real_balance()` is the one exception: it runs every single cycle
(60-120s) specifically because, per its own docstring, PositionManager's own
PnL bookkeeping has been proven unreliable ("752 yanlış resolution -> $670
tracking hatası") and the real CLOB balance is the actual source of truth.
When it corrects `capital` downward to match reality, that correction *is*
today's real loss -- but because `daily["pnl"]` is left untouched, the
`capital - daily.pnl` baseline silently drifts by the same amount, which can
fully mask a real breach of the -15% daily stop-loss (or manufacture a fake
one), letting the bot keep trading on a day it must have stopped.

Fix: `_sync_real_balance()` now folds the correction (`new_capital -
old_capital`) into `data["daily"]["pnl"]` too, the same invariant already
kept by `_close_position()` / `_close_position_neutral()`.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def tmp_positions_file(tmp_path, monkeypatch):
    import core.position_manager as pm_mod
    monkeypatch.setattr(pm_mod, "DATA_FILE", tmp_path / "positions.json")
    monkeypatch.setenv("INITIAL_CAPITAL", "1000.0")


def _make_orchestrator(get_real_balance_return: float, capital: float, daily_pnl: float):
    from agents.orchestrator import Orchestrator
    from core.position_manager import PositionManager
    from datetime import date

    orch = Orchestrator.__new__(Orchestrator)
    orch.client = MagicMock()
    orch.client.get_real_balance.return_value = get_real_balance_return
    orch.position_manager = PositionManager()
    orch.position_manager.data["capital"] = capital
    orch.position_manager.data["daily"] = {"date": str(date.today()), "pnl": daily_pnl}
    orch.position_manager._save()
    return orch


@pytest.mark.asyncio
async def test_balance_correction_is_reflected_in_daily_pnl_so_stop_loss_still_fires():
    """Day started at $1000 (daily.pnl=0, no open positions). PositionManager's
    own tracking thought nothing had changed, but the real CLOB balance has
    actually dropped to $849 -- a genuine 15.1% loss for the day. Once
    _sync_real_balance() corrects `capital` to match, `daily_loss_exceeded()`
    must still detect the breach: it must not be silently absorbed by a
    daily.pnl bucket that never moved.
    """
    orch = _make_orchestrator(get_real_balance_return=849.0, capital=1000.0, daily_pnl=0.0)

    await orch._sync_real_balance()

    assert orch.position_manager.data["capital"] == pytest.approx(849.0)
    assert orch.position_manager.daily_loss_exceeded(0.15) is True, (
        "A real $151/$1000 (15.1%) drop revealed by the CLOB balance sync "
        "must trip the daily -15% stop-loss, not be masked by a stale "
        "daily.pnl bucket that the sync never adjusted."
    )


@pytest.mark.asyncio
async def test_balance_correction_upward_does_not_fake_a_loss():
    """Non-regression / symmetry: a positive correction (CLOB balance higher
    than tracked, e.g. an underreported win) must also flow into daily.pnl,
    so it doesn't manufacture a phantom loss either."""
    orch = _make_orchestrator(get_real_balance_return=1050.0, capital=1000.0, daily_pnl=0.0)

    await orch._sync_real_balance()

    assert orch.position_manager.data["capital"] == pytest.approx(1050.0)
    assert orch.position_manager.daily_loss_exceeded(0.15) is False


@pytest.mark.asyncio
async def test_small_correction_still_tracked_before_threshold():
    """A correction alone that is under the -15% threshold must not trip the
    stop-loss, confirming the fix measures against the true day-start
    capital rather than always tripping."""
    orch = _make_orchestrator(get_real_balance_return=950.0, capital=1000.0, daily_pnl=0.0)

    await orch._sync_real_balance()

    assert orch.position_manager.data["capital"] == pytest.approx(950.0)
    assert orch.position_manager.daily_loss_exceeded(0.15) is False
