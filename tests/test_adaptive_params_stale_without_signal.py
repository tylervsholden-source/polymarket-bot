"""
Regression test: AutonomousDecisionEngine.get_adaptive_params() must reflect
real closed trades even in cycles where no signal reached evaluate().

Bug: `agents/autonomous_engine.py`'s PerformanceSnapshot (win_rate,
consecutive_losses, drawdown_pct — the fields get_adaptive_params() bases
DEFENSIVE/SURVIVAL/AGGRESSIVE tightening on) is only ever refreshed inside
_update_performance(), and the ONLY caller of _update_performance() is
evaluate():

    def evaluate(self, signal, review_decision, capital, open_count,
                 max_positions, closed_trades=None):
        if closed_trades:
            self._update_performance(closed_trades, capital)
        ...

`agents/orchestrator.py::Orchestrator.run()` calls
`self.autonomous_engine.evaluate()` from exactly one place: inside the
`for signal, review_decision in coord_result.approved_signals:` loop in
_cycle() — i.e. only on cycles where at least one signal survived the
6-model engine + coordinator + reviewer pipeline. Most cycles (no
candidate market in its 5-15 minute entry window, or every candidate
rejected by a gate) produce zero approved signals, so evaluate() — and
therefore _update_performance() — never runs.

But `run()` calls `self.autonomous_engine.get_adaptive_params(capital)`
unconditionally, every single cycle, and its result (min_edge_yes/no,
max_bet_multiplier, aggression) is applied to the live edge gate
(`self.arb_engine.min_edge_yes/no`) and bet sizing
(`self._adaptive_bet_multiplier`) for the NEXT cycle. If a real trade
closes (via `position_manager.update_positions()`, which runs every cycle
regardless of signals) during a run of signal-less cycles, that loss/streak
is invisible to get_adaptive_params() — win_rate/consecutive_losses/
drawdown_pct stay frozen at whatever they were the last time a signal
happened to be evaluated — until the next signal finally appears, at which
point evaluate() reacts, but get_adaptive_params() for the cycles in
between (and the current one, whose min_edge/bet_multiplier were already
computed from the stale snapshot at the end of the previous cycle) kept
running at NORMAL sizing/edge despite an active, real losing streak.

Fix: get_adaptive_params() accepts the current closed_trades list and
refreshes the performance snapshot itself before computing params, so it
never depends on evaluate() having run this session.
"""
from __future__ import annotations

from agents.autonomous_engine import AutonomousDecisionEngine


def _loss(i: int) -> dict:
    return {"order_id": f"loss-{i}", "result": "LOSS", "pnl": -1.0}


def _win(i: int) -> dict:
    return {"order_id": f"win-{i}", "result": "WIN", "pnl": 1.0}


def test_adaptive_params_see_real_losses_without_evaluate_ever_running():
    """A fresh engine (as if this session's cycles have all had zero
    approved signals so far, so evaluate() has never fired) must still
    reflect a real, already-closed losing streak when asked for adaptive
    params — not silently report NORMAL/AGGRESSIVE sizing."""
    engine = AutonomousDecisionEngine()

    # 12 real closed trades: 3 wins, 9 losses (win_rate=25% < 40% threshold,
    # total_trades=12 > 10) with the most recent 5 all LOSS (consecutive
    # losses >= 3). evaluate() is deliberately never called — this
    # reproduces "no signal was ever approved this session" exactly.
    closed_trades = (
        [_win(i) for i in range(3)]
        + [_loss(i) for i in range(4)]
        + [_win(99)]
        + [_loss(i) for i in range(4, 9)]
    )

    params = engine.get_adaptive_params(capital=100.0, closed_trades=closed_trades)

    assert params["aggression"] == "DEFENSIVE", (
        "get_adaptive_params() must pick up a real 25% win-rate losing "
        "streak from closed_trades on its own — it must not require a "
        "prior evaluate() call (which only runs when a signal was "
        "actually approved this cycle) to refresh performance"
    )
    assert params["min_edge_no"] == 0.20
    assert params["min_edge_yes"] == 0.10
    assert params["max_bet_multiplier"] == 0.6


def test_get_adaptive_params_without_closed_trades_is_unaffected():
    """Omitting closed_trades (existing call sites / tests) must keep
    reading whatever the last evaluate() call left in _performance —
    no behavior change for callers that don't pass it."""
    engine = AutonomousDecisionEngine()
    engine._performance.win_rate = 0.30
    engine._performance.total_trades = 20
    engine._performance.capital = 100.0

    params = engine.get_adaptive_params()

    assert params["aggression"] == "DEFENSIVE"
    assert params["min_edge_yes"] == 0.10
