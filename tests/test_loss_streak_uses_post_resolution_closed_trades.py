"""
Regression test: Orchestrator._cycle() must compute the loss-streak / Dynamic
Kelly / Walk-Forward / autonomous-engine risk snapshot from the *post*-
resolution closed-trade list, not a copy captured before this cycle's
resolutions run.

Bug (77th daily review): `closed_trades = self.position_manager.data.get(
"closed", [])` was read, and immediately fed into `_update_loss_streak()`
(via its own internal re-read), `self.arb_engine.kelly.update_streak()` and
`self._walk_forward.validate()`, *before*
`await self.position_manager.update_positions(self.client)` — the call that
actually resolves this cycle's expiring positions into `data["closed"]`. The
same stale `closed_trades` reference was then reused later in the same
cycle for `self.autonomous_engine.evaluate(..., closed_trades=closed_trades)`.

Concrete failure: three positions resolve as LOSS during this cycle's
`update_positions()` call. A new signal executed later in the *same* cycle
is still sized against the pre-cycle streak/win-rate/drawdown — Dynamic
Kelly applies no streak cut, Walk-Forward sees no fresh drawdown, and the
autonomous engine classifies risk as if the fresh losses never happened.
The staleness only clears on the *next* cycle.

This is the same "control exists but isn't wired to fresh state" pattern as
the 74th daily review's `get_adaptive_params()` fix (see
tests/test_adaptive_params_wiring.py) — that fix added a fresh read at its
own call site but didn't touch this sibling snapshot.

Fix: `await self.position_manager.update_positions(self.client)` now runs
before the loss-streak/Kelly-streak/walk-forward block, so `closed_trades`
(and everything derived from it this cycle) reflects this cycle's own
resolutions.
"""
from __future__ import annotations

import inspect

from agents.orchestrator import Orchestrator


def test_update_positions_runs_before_closed_trades_snapshot():
    src = inspect.getsource(Orchestrator._cycle)

    resolve_idx = src.index("await self.position_manager.update_positions(self.client)")
    streak_idx = src.index("self._update_loss_streak()")
    snapshot_idx = src.index("closed_trades = self._current_closed_trades()")
    kelly_idx = src.index("self.arb_engine.kelly.update_streak(closed_trades)")
    wf_idx = src.index("self._walk_forward.validate(closed_trades)")

    assert resolve_idx < streak_idx, (
        "update_positions() must resolve this cycle's closes before "
        "_update_loss_streak() reads position_manager.data['closed']"
    )
    assert resolve_idx < snapshot_idx, (
        "closed_trades must be captured after update_positions() resolves "
        "this cycle's WIN/LOSS closes, not before"
    )
    assert snapshot_idx < kelly_idx < wf_idx, (
        "closed_trades snapshot must feed both Dynamic Kelly and "
        "Walk-Forward in source order"
    )


def test_open_position_check_still_runs_after_resolution():
    # Preserve the pre-existing invariant this function documents: resolution
    # must happen before the max-open-positions check, or the bot locks up
    # once positions that should have resolved never get resolved.
    src = inspect.getsource(Orchestrator._cycle)
    resolve_idx = src.index("await self.position_manager.update_positions(self.client)")
    open_count_idx = src.index("open_count: int = self.position_manager.open_position_count()")
    assert resolve_idx < open_count_idx
