"""
Regression test for OPT-6 (loss slot cooldown) in Orchestrator._update_loss_streak
— sim/paper mode (the bot's actual default operating mode; see CLAUDE.md /
docs/architecture.md, no live control.json in this environment).

Bug: _update_loss_streak() unconditionally rebuilt self._last_loss_slots from
position_manager.data["closed"] only (real CLOB positions). But when
_is_live_trading() is False — the default/current mode — trades never reach
position_manager at all; they are tracked in the orchestrator's own in-memory
self._sim_trades / self._sim_results (see the `else:` branch in _cycle()'s
order-placement block). _check_sim_resolutions() runs earlier in the same
cycle and incrementally adds a resolved LOSS's time slot to
self._last_loss_slots (LOSS_SLOT_TRACK) — but _update_loss_streak(), called
right after it in the same cycle, unconditionally did
`self._last_loss_slots.clear()` and then rebuilt strictly from the (empty, in
sim mode) real closed-trades list, silently discarding the sim loss slot it
had just recorded. The result: OPT-6's dead-cat-bounce cooldown
(_is_adjacent_to_loss_slot(), consumed by _limit_coins_per_period()) never
actually blocked a post-loss slot while running in sim/paper mode, even
though CLAUDE.md documents OPT-6 as active.

Fix: source the rebuild from position_manager's real closed trades in live
mode, and from self._sim_results in sim mode (the two are mutually exclusive
at any given moment, matching how trades are recorded in _cycle()).
"""
from __future__ import annotations

from types import SimpleNamespace
from datetime import datetime
from zoneinfo import ZoneInfo

from agents.orchestrator import Orchestrator

_TODAY_ET = datetime.now(ZoneInfo("America/New_York")).strftime("%B %d").replace(" 0", " ")


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


def test_sim_mode_loss_slot_survives_the_same_cycles_rebuild():
    """Mirrors the real _cycle() sequence: _check_sim_resolutions() records a
    LOSS slot into self._sim_results, then _update_loss_streak() runs. In sim
    mode the loss slot must still be present in _last_loss_slots afterwards."""
    orch = _make_orchestrator(
        is_live=False,
        sim_results=[
            {"question": f"BTC Up or Down - {_TODAY_ET}, 8:05AM-8:10AM", "result": "LOSS"},
        ],
        closed=[],  # sim mode: position_manager never receives real trades
    )

    orch._update_loss_streak()

    assert "8:05AM-8:10AM" in orch._last_loss_slots


def test_sim_mode_win_clears_its_own_slot():
    orch = _make_orchestrator(
        is_live=False,
        sim_results=[
            {"question": f"BTC Up or Down - {_TODAY_ET}, 8:05AM-8:10AM", "result": "LOSS"},
            {"question": f"BTC Up or Down - {_TODAY_ET}, 8:05AM-8:10AM", "result": "WIN"},
        ],
        closed=[],
    )

    orch._update_loss_streak()

    assert "8:05AM-8:10AM" not in orch._last_loss_slots


def test_live_mode_still_sources_from_real_closed_trades():
    """Live mode must be unaffected: real closed positions still populate
    _last_loss_slots, and an (empty) sim_results list must not suppress it."""
    orch = _make_orchestrator(
        is_live=True,
        sim_results=[],
        closed=[
            {"question": f"BTC Up or Down - {_TODAY_ET}, 8:05AM-8:10AM", "result": "LOSS", "outcome": "NO"},
        ],
    )

    orch._update_loss_streak()

    assert "8:05AM-8:10AM" in orch._last_loss_slots


def test_live_mode_ignores_sim_results():
    """A stray/leftover sim_results entry must not leak into live-mode
    accounting once the bot is actually live."""
    orch = _make_orchestrator(
        is_live=True,
        sim_results=[
            {"question": f"BTC Up or Down - {_TODAY_ET}, 9:00AM-9:05AM", "result": "LOSS"},
        ],
        closed=[],
    )

    orch._update_loss_streak()

    assert orch._last_loss_slots == set()
