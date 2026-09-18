"""
Regression test for OPT-7 (consecutive NO-win bounce guard) in
Orchestrator._update_loss_streak — sim/paper mode (the bot's actual
default operating mode; see CLAUDE.md / docs/architecture.md).

Bug: _update_loss_streak()'s OPT-7 tracker (`_consecutive_wins_per_coin`,
consumed by the CONSEC_WIN_GUARD in `_cycle()` to SKIP a 3rd+ consecutive
NO bet on the same coin, or half-kelly a 2nd — CLAUDE.md's documented
"2+ ardışık NO-win periyottan sonra %100 bounce geliyor" pattern) was
computed exclusively from `position_manager.data["closed"]` (real CLOB
positions). The 82nd daily review already established that this list is
ALWAYS empty while `_is_live_trading()` is False (the bot's default mode) —
real orders are never placed then, trades are tracked instead in the
orchestrator's own self._sim_results — and fixed that specific source
mismatch for OPT-6's `loss_slot_source`. It missed that the OPT-7
consecutive-win-per-coin computation (and the `_consecutive_losses` streak
right above it) read the exact same unconditional `closed` variable, so
OPT-7's bounce guard silently never fired in sim/paper mode either, even
after multiple consecutive NO wins on the same coin that CLAUDE.md's own
documented pattern says should trigger a bounce-risk SKIP/half-kelly.

A second, compounding issue: even once `closed` is sourced correctly in sim
mode, sim-mode trade records carry a "direction" field (not "outcome" —
that field only exists on real closed positions), so the OPT-7 check must
accept either key.

Fix: source `closed` from position_manager in live mode and from
self._sim_results otherwise (mirroring the already-fixed OPT-6 selection),
and read "outcome" or "direction", whichever the record carries.
"""
from __future__ import annotations

from types import SimpleNamespace

from agents.orchestrator import Orchestrator


def _make_orchestrator(*, is_live: bool, sim_results: list[dict], closed: list[dict]) -> Orchestrator:
    orch = Orchestrator.__new__(Orchestrator)
    orch._consecutive_losses = 0
    orch._max_consecutive_losses = 3
    orch._consecutive_wins_per_coin = {}
    orch._last_loss_slots = set()
    orch._sim_results = sim_results
    orch.position_manager = SimpleNamespace(data={"positions": {}, "closed": closed})
    orch._is_live_trading = lambda: is_live
    return orch


def _sim_trade(question: str, direction: str, result: str) -> dict:
    """Shape of a self._sim_results record: carries 'direction', never
    'outcome' (see _cycle()'s sim_entry construction)."""
    return {"question": question, "direction": direction, "result": result}


def test_sim_mode_consecutive_no_wins_trigger_bounce_guard():
    """Three consecutive sim-mode NO wins on BTC must populate
    _consecutive_wins_per_coin so _cycle()'s CONSEC_WIN_GUARD can SKIP the
    next NO signal — mirroring the exact live-mode behavior already covered
    by tests/test_consec_win_guard_field.py."""
    orch = _make_orchestrator(
        is_live=False,
        sim_results=[
            _sim_trade("BTC Up or Down - March 22, 8:00AM-8:05AM", "NO", "WIN"),
            _sim_trade("BTC Up or Down - March 22, 8:05AM-8:10AM", "NO", "WIN"),
            _sim_trade("BTC Up or Down - March 22, 8:10AM-8:15AM", "NO", "WIN"),
        ],
        closed=[],  # sim mode: position_manager never receives real trades
    )

    orch._update_loss_streak()

    assert orch._consecutive_wins_per_coin.get("BTC") == 3


def test_sim_mode_streak_breaks_on_loss():
    orch = _make_orchestrator(
        is_live=False,
        sim_results=[
            _sim_trade("BTC Up or Down - March 22, 8:00AM-8:05AM", "NO", "LOSS"),
            _sim_trade("BTC Up or Down - March 22, 8:05AM-8:10AM", "NO", "WIN"),
            _sim_trade("BTC Up or Down - March 22, 8:10AM-8:15AM", "NO", "WIN"),
        ],
        closed=[],
    )

    orch._update_loss_streak()

    assert orch._consecutive_wins_per_coin.get("BTC") == 2


def test_live_mode_still_ignores_sim_results_for_opt7():
    """Stray/leftover sim_results must not leak into live-mode OPT-7
    accounting once the bot is actually live — same guarantee already
    established for OPT-6's loss_slot_source."""
    orch = _make_orchestrator(
        is_live=True,
        sim_results=[
            _sim_trade("BTC Up or Down - March 22, 8:00AM-8:05AM", "NO", "WIN"),
            _sim_trade("BTC Up or Down - March 22, 8:05AM-8:10AM", "NO", "WIN"),
        ],
        closed=[],
    )

    orch._update_loss_streak()

    assert orch._consecutive_wins_per_coin.get("BTC", 0) == 0
