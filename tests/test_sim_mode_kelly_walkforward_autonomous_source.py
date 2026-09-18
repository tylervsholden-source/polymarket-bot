"""
Regression test (84th daily review): Dynamic Kelly's streak multiplier,
Walk-Forward validation and AutonomousDecisionEngine's risk snapshot all
went blind in sim/paper mode — the bot's actual default operating mode (see
CLAUDE.md / docs/architecture.md; no live control.json in this environment).

Bug: `Orchestrator._cycle()` computed

    closed_trades = self.position_manager.data.get("closed", [])

unconditionally, then fed that single snapshot into THREE downstream
consumers: `self.arb_engine.kelly.update_streak(closed_trades)` (Dynamic
Kelly's win/loss streak size multiplier), `self._walk_forward.validate(
closed_trades)` (walk-forward's confidence multiplier) and, later in the
same cycle, `self.autonomous_engine.evaluate(..., closed_trades=closed_trades)`
(risk classification: win_rate, consecutive_losses, drawdown_pct). A fourth
call site, `Orchestrator.run()`'s `self.autonomous_engine.get_adaptive_params(
capital, self.position_manager.data.get("closed", []))`, read the exact same
raw dict path independently.

When `_is_live_trading()` is False (the default), `_cycle()`'s order-placement
`else` branch never touches `position_manager` at all — it records trades into
`self._sim_trades` / `self._sim_results` instead (see `_check_sim_resolutions()`).
So `position_manager.data["closed"]` stays permanently `[]` in this mode, and
all four call sites above silently saw an eternally-empty trade history: no
loss-streak Kelly size cut, no walk-forward drawdown detection, no autonomous
risk escalation — no matter how long or how bad a real losing streak in
`self._sim_results` ran.

This is the same root cause the 82nd/83rd daily reviews fixed for
`_update_loss_streak()`'s OPT-6/OPT-7 state (mode-conditional `closed` vs
`self._sim_results` selection) — but that fix was never propagated to these
four sibling call sites, which the 77th review's own commit message flagged
as still open ("aynı bug sınıfı ... bu kardeş kullanım noktalarında kalmıştı").

Fix: added `Orchestrator._current_closed_trades()`, the same mode-conditional
selection used internally by `_update_loss_streak()`, and routed all four call
sites through it.
"""
from __future__ import annotations

import inspect

from agents.orchestrator import Orchestrator
from strategies.kelly_criterion import KellyCriterion
from strategies.walk_forward import WalkForwardValidator


def _make_orchestrator(*, is_live: bool, sim_results: list, closed: list) -> Orchestrator:
    from types import SimpleNamespace

    orch = Orchestrator.__new__(Orchestrator)
    orch._sim_results = sim_results
    orch.position_manager = SimpleNamespace(data={"positions": {}, "closed": closed})
    orch._is_live_trading = lambda: is_live
    return orch


# ── _current_closed_trades() itself ─────────────────────────────────────

def test_current_closed_trades_sim_mode_reads_sim_results():
    orch = _make_orchestrator(
        is_live=False,
        sim_results=[{"result": "LOSS"}, {"result": "LOSS"}],
        closed=[],
    )
    assert orch._current_closed_trades() == [{"result": "LOSS"}, {"result": "LOSS"}]


def test_current_closed_trades_live_mode_reads_real_closed_and_ignores_sim():
    orch = _make_orchestrator(
        is_live=True,
        sim_results=[{"result": "LOSS"}],  # stray, must not leak into live
        closed=[{"result": "WIN"}],
    )
    assert orch._current_closed_trades() == [{"result": "WIN"}]


# ── Downstream effect: Dynamic Kelly must actually see the sim loss streak ──

def test_dynamic_kelly_streak_multiplier_reacts_to_sim_mode_losses():
    """Reproduces the real bug end-to-end: a 3-loss streak recorded only in
    self._sim_results (as it always is in the bot's default mode) must cut
    Dynamic Kelly's size multiplier. Feeding it through the pre-fix source
    (position_manager.data["closed"], always [] in sim mode) leaves the
    multiplier stuck at 1.0 despite the real losing streak."""
    sim_results = [{"result": "LOSS"} for _ in range(3)]
    orch = _make_orchestrator(is_live=False, sim_results=sim_results, closed=[])

    kelly = KellyCriterion()

    # Pre-fix source: always empty in sim mode -> bug reproduced.
    buggy_source = orch.position_manager.data.get("closed", [])
    kelly.update_streak(buggy_source)
    assert kelly._streak_multiplier == 1.0, (
        "sanity check: the old unconditional position_manager.data['closed'] "
        "read must reproduce the bug (streak invisible in sim mode)"
    )

    # Fixed source: sees the sim-mode loss streak.
    kelly.update_streak(orch._current_closed_trades())
    assert kelly._loss_streak == 3
    assert kelly._streak_multiplier == max(
        kelly._STREAK_MULT_MIN, round(1.0 - 3 * kelly._STREAK_CUT_PER_LOSS, 10)
    )
    assert kelly._streak_multiplier < 1.0


def test_walk_forward_sees_sim_mode_trades_through_fixed_source():
    trades = [{"result": "WIN", "pnl": 1.0} for _ in range(80)]
    orch = _make_orchestrator(is_live=False, sim_results=trades, closed=[])

    wf = WalkForwardValidator(train_window=50, test_window=20)

    # Pre-fix source would always be [] in sim mode -> total_needed never met.
    buggy_result = wf.validate(orch.position_manager.data.get("closed", []))
    assert buggy_result["recommendation"] == "FULL_SIZE"  # default early-out, not a real read

    fixed_result = wf.validate(orch._current_closed_trades())
    assert fixed_result["test_wr"] == 1.0  # actually read the 80 sim trades


# ── Wiring: the real call sites must route through _current_closed_trades() ──

def test_cycle_uses_current_closed_trades_helper_for_kelly_and_walk_forward():
    src = inspect.getsource(Orchestrator._cycle)
    snapshot_idx = src.index("closed_trades = self._current_closed_trades()")
    kelly_idx = src.index("self.arb_engine.kelly.update_streak(closed_trades)")
    wf_idx = src.index("self._walk_forward.validate(closed_trades)")
    evaluate_idx = src.index("closed_trades=closed_trades,")
    assert snapshot_idx < kelly_idx < wf_idx < evaluate_idx


def test_run_uses_current_closed_trades_helper_for_adaptive_params():
    src = inspect.getsource(Orchestrator.run)
    assert "self._current_closed_trades()" in src
    assert 'self.position_manager.data.get("closed", [])' not in src
