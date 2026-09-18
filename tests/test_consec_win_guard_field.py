"""
Regression test for OPT-7 (consecutive NO-win bounce guard) in
Orchestrator._update_loss_streak.

Bug: real closed positions (position_manager.data["closed"]) are written by
PositionManager.add_position()/_close_position() with an "outcome" field
(YES/NO) — they never carry a "direction" key (that key only exists on the
separate self._sim_trades dicts built elsewhere in orchestrator.py for sim
mode). _update_loss_streak()'s OPT-7 tracker checked
`trade.get("direction", "").upper() == "NO"`, which always read "" for real
trades and could never equal "NO" — so `_consecutive_wins_per_coin` stayed
empty forever in live/paper trading, and the CONSEC_WIN_GUARD in `_cycle()`
(meant to SKIP a 3rd+ consecutive NO bet on the same coin — CLAUDE.md's
documented "2+ ardışık NO-win periyottan sonra %100 bounce geliyor" pattern)
never fired, letting the bot keep taking increasingly bounce-prone NO bets.
"""
from __future__ import annotations

from types import SimpleNamespace

from agents.orchestrator import Orchestrator


def _make_orchestrator(closed: list[dict]) -> Orchestrator:
    orch = Orchestrator.__new__(Orchestrator)
    orch._consecutive_losses = 0
    orch._max_consecutive_losses = 3
    orch._consecutive_wins_per_coin = {}
    orch._last_loss_slots = set()
    orch.position_manager = SimpleNamespace(data={"closed": closed})
    return orch


def _closed_trade(question: str, outcome: str, result: str) -> dict:
    """Shape of a real PositionManager closed-position record: it carries
    'outcome' (the traded side), never a 'direction' key."""
    return {"question": question, "outcome": outcome, "result": result}


def test_consecutive_no_wins_tracked_from_real_closed_trades():
    closed = [
        _closed_trade("BTC Up or Down - March 22, 8:00AM-8:05AM", "NO", "WIN"),
        _closed_trade("BTC Up or Down - March 22, 8:05AM-8:10AM", "NO", "WIN"),
        _closed_trade("BTC Up or Down - March 22, 8:10AM-8:15AM", "NO", "WIN"),
    ]
    orch = _make_orchestrator(closed)

    orch._update_loss_streak()

    assert orch._consecutive_wins_per_coin.get("BTC") == 3


def test_streak_breaks_on_loss():
    closed = [
        _closed_trade("BTC Up or Down - March 22, 8:00AM-8:05AM", "NO", "LOSS"),
        _closed_trade("BTC Up or Down - March 22, 8:05AM-8:10AM", "NO", "WIN"),
        _closed_trade("BTC Up or Down - March 22, 8:10AM-8:15AM", "NO", "WIN"),
    ]
    orch = _make_orchestrator(closed)

    orch._update_loss_streak()

    # Most recent two are NO wins, but the walk stops at the first non
    # (WIN, NO) trade going backwards from that pair... here it's 2 (the
    # 8:00 LOSS breaks the streak before it can extend further).
    assert orch._consecutive_wins_per_coin.get("BTC") == 2


def test_neutral_close_does_not_break_no_win_streak():
    """An unfilled/cancelled GTC order closes with result="NEUTRAL" (USDC
    refunded, pnl=0) — it is neither a win nor a loss and must not break the
    OPT-7 streak, matching kelly_criterion.update_streak() and
    autonomous_engine._update_performance()."""
    closed = [
        _closed_trade("BTC Up or Down - March 22, 8:00AM-8:05AM", "NO", "WIN"),
        _closed_trade("BTC Up or Down - March 22, 8:05AM-8:10AM", "NO", "NEUTRAL"),
        _closed_trade("BTC Up or Down - March 22, 8:10AM-8:15AM", "NO", "WIN"),
    ]
    orch = _make_orchestrator(closed)

    orch._update_loss_streak()

    assert orch._consecutive_wins_per_coin.get("BTC") == 2


def test_yes_wins_do_not_count_toward_no_guard():
    closed = [
        _closed_trade("BTC Up or Down - March 22, 8:00AM-8:05AM", "YES", "WIN"),
        _closed_trade("BTC Up or Down - March 22, 8:05AM-8:10AM", "YES", "WIN"),
    ]
    orch = _make_orchestrator(closed)

    orch._update_loss_streak()

    assert orch._consecutive_wins_per_coin.get("BTC", 0) == 0
