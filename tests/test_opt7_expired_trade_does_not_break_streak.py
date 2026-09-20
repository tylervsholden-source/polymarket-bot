"""
Regression test for OPT-7 (consecutive NO-win bounce guard) in
Orchestrator._update_loss_streak — 102nd daily review (round c).

Bug: the per-coin streak-building loop explicitly skips (does not break the
streak on) a "NEUTRAL" result — "Unfilled/cancelled GTC order — neither win
nor loss, doesn't break streak" — but had no equivalent case for "EXPIRED"
(sim market not resolved 45min+ after entry, set by
Orchestrator._check_sim_resolutions()). agents/trade_analyzer.py already
documents that EXPIRED must be treated the same as NEUTRAL for exactly this
reason ("bu dal olmasaydı ... her EXPIRED kapanış LOSS olarak yanlış
etiketlenirdi"), and agents/autonomous_engine.py's own
_update_performance() streak loop already skips anything that isn't
WIN/LOSS (comment: "matching kelly_criterion.update_streak()"). This one
loop was the odd one out: an EXPIRED trade fell into the trailing `else`
branch and was treated exactly like a real loss or an opposing-direction
win, incorrectly resetting `_consecutive_wins_per_coin[coin]` to 0.

Live impact: `_consecutive_wins_per_coin` feeds the OPT-7 CONSEC_WIN_GUARD
in `_cycle()`, which SKIPs a 3rd+ consecutive NO bet on the same coin (and
should half-Kelly the 2nd) to protect against the bounce pattern CLAUDE.md
documents ("2+ ardışık NO-win periyottan sonra %100 bounce geliyor"). An
EXPIRED trade landing between two NO wins on the same coin silently reset
that count to zero, so a real 3-in-a-row bounce-risk streak was
under-counted as 1, and the guard failed to fire when it was supposed to.

Fix: EXPIRED is now skipped (continue) exactly like NEUTRAL.
"""
from __future__ import annotations

from types import SimpleNamespace

from agents.orchestrator import Orchestrator


def _make_orchestrator(*, sim_results: list[dict]) -> Orchestrator:
    orch = Orchestrator.__new__(Orchestrator)
    orch._consecutive_losses = 0
    orch._max_consecutive_losses = 3
    orch._consecutive_wins_per_coin = {}
    orch._last_loss_slots = set()
    orch._sim_results = sim_results
    orch.position_manager = SimpleNamespace(data={"positions": {}, "closed": []})
    orch._is_live_trading = lambda: False
    return orch


def _sim_trade(question: str, direction: str, result: str) -> dict:
    return {"question": question, "direction": direction, "result": result}


def test_expired_trade_does_not_reset_consecutive_no_win_streak():
    """2 NO wins, an EXPIRED trade, then a 3rd NO win on the same coin must
    still count as a 3-streak (EXPIRED skipped, not a streak-breaker) so the
    CONSEC_WIN_GUARD can fire on the next NO signal for BTC."""
    orch = _make_orchestrator(
        sim_results=[
            _sim_trade("BTC Up or Down - March 22, 8:00AM-8:05AM", "NO", "WIN"),
            _sim_trade("BTC Up or Down - March 22, 8:05AM-8:10AM", "NO", "WIN"),
            _sim_trade("BTC Up or Down - March 22, 8:10AM-8:15AM", "NO", "EXPIRED"),
            _sim_trade("BTC Up or Down - March 22, 8:15AM-8:20AM", "NO", "WIN"),
        ],
    )

    orch._update_loss_streak()

    assert orch._consecutive_wins_per_coin.get("BTC") == 3


def test_expired_trade_alone_does_not_start_or_break_anything():
    """A lone EXPIRED trade (no wins yet) must not register as a streak
    entry, and must not prevent a subsequent independent streak from being
    counted correctly."""
    orch = _make_orchestrator(
        sim_results=[
            _sim_trade("BTC Up or Down - March 22, 8:00AM-8:05AM", "NO", "EXPIRED"),
            _sim_trade("BTC Up or Down - March 22, 8:05AM-8:10AM", "NO", "WIN"),
            _sim_trade("BTC Up or Down - March 22, 8:10AM-8:15AM", "NO", "WIN"),
        ],
    )

    orch._update_loss_streak()

    assert orch._consecutive_wins_per_coin.get("BTC") == 2
