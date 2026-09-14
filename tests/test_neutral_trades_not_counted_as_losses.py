"""
Regression test: AutonomousDecisionEngine must not treat NEUTRAL closes
(unfilled GTC orders whose USDC was simply refunded, pnl=0) as trading
losses.

Bug: agents/autonomous_engine.py::_update_performance() classified wins and
losses — and computed the consecutive win/loss streak that
`evaluate()`/`get_adaptive_params()` act on — purely from the sign of
`pnl`:

    wins   = [t for t in closed_trades if t.get("pnl", 0) > 0]
    losses = [t for t in closed_trades if t.get("pnl", 0) <= 0]
    ...
    elif pnl <= 0:
        if perf.consecutive_wins == 0:
            perf.consecutive_losses += 1
        else:
            break

`core/position_manager.py::_close_position_neutral()` closes a position
with `pnl=0.0` and `result="NEUTRAL"` whenever a GTC order never fills
before the market ends (a routine, expected occurrence — see
`update_positions()`'s "Emir dolmadı (NEUTRAL)" / "FORCE_CLOSE_TIMEOUT ...
NEUTRAL" paths). Because `pnl <= 0` is true for pnl==0, every one of these
non-events was counted as a LOSS: it depressed `win_rate`, and in the
consecutive-streak loop it both extended `consecutive_losses` and could
mask/break a real win streak — even though nothing about the bot's trading
skill or edge was actually wrong; the order simply never got filled.

This directly drives live behavior in `evaluate()`:
  - `STREAK_LOSS_THRESHOLD = 2` shrinks size_multiplier once
    `consecutive_losses >= 2`.
  - `streak >= 4 and edge < 0.08` sets `action = SKIP` outright
    (STREAK_FILTER) — a real, good-edge-adjacent signal gets skipped purely
    because 4 prior orders failed to fill, not because of 4 real losses.

Elsewhere in the same codebase, `strategies/kelly_criterion.py::
update_streak()` already treats `result == "NEUTRAL"` as a pass-through
(neither wins nor breaks a streak) — proving the pnl-sign-only handling in
`autonomous_engine.py` was an oversight, not an intentional design choice.

Fix: classify wins/losses and the consecutive streak from the `result`
field ("WIN"/"LOSS"/"NEUTRAL") that `position_manager.py` already attaches
to every closed trade, skipping NEUTRAL entries entirely — matching
`kelly_criterion.py`'s existing behavior.
"""
from __future__ import annotations

from types import SimpleNamespace

from agents.autonomous_engine import AutonomousDecisionEngine


def _neutral(pnl: float = 0.0) -> dict:
    return {"result": "NEUTRAL", "pnl": pnl, "edge": 0.1}


def _win(pnl: float = 5.0) -> dict:
    return {"result": "WIN", "pnl": pnl, "edge": 0.1}


def _loss(pnl: float = -5.0) -> dict:
    return {"result": "LOSS", "pnl": pnl, "edge": 0.1}


def test_neutral_closes_do_not_count_as_losses_in_streak(monkeypatch):
    monkeypatch.setenv("INITIAL_CAPITAL", "1000")
    engine = AutonomousDecisionEngine()

    # Realistic sequence (oldest -> newest, matching position_manager.data
    # ["closed"] append order): one real WIN, then four GTC orders in a row
    # that simply never filled (NEUTRAL, pnl=0) — zero actual trading losses.
    closed_trades = [_win(), _neutral(), _neutral(), _neutral(), _neutral()]

    engine._update_performance(closed_trades=closed_trades, capital=1000.0)

    assert engine._performance.consecutive_losses == 0, (
        "four unfilled/refunded (NEUTRAL, pnl=0) orders must not be counted "
        f"as a loss streak; got consecutive_losses="
        f"{engine._performance.consecutive_losses} (expected 0 — the most "
        "recent decided trade was a WIN)"
    )
    assert engine._performance.consecutive_wins == 1, (
        "the streak should still see through the NEUTRAL closes to the "
        f"real WIN behind them; got consecutive_wins="
        f"{engine._performance.consecutive_wins}"
    )


def test_neutral_closes_do_not_depress_win_rate():
    engine = AutonomousDecisionEngine()
    # 2 real wins, 0 real losses, 8 unfilled/refunded NEUTRAL closes.
    closed_trades = [_win(), _win()] + [_neutral() for _ in range(8)]

    engine._update_performance(closed_trades=closed_trades, capital=1000.0)

    assert engine._performance.win_count == 2
    assert engine._performance.loss_count == 0
    assert engine._performance.win_rate == 1.0, (
        "win_rate must be computed over decided (WIN/LOSS) trades, not "
        f"diluted by NEUTRAL non-events; got "
        f"{engine._performance.win_rate:.3f}"
    )


def test_streak_filter_does_not_skip_a_good_signal_over_unfilled_orders(monkeypatch):
    """End-to-end through evaluate(): 4 NEUTRAL closes must not trigger the
    STREAK_FILTER SKIP path that real 4-loss streaks are meant to guard."""
    monkeypatch.setenv("INITIAL_CAPITAL", "1000")
    engine = AutonomousDecisionEngine()

    signal = SimpleNamespace(
        edge=0.06,  # below 0.08 -> would trip STREAK_FILTER under the bug
        direction="YES", risk_flags=[], confluence_score=0.9,
        regime_strength=0.0,
    )
    review_decision = SimpleNamespace(verdict="APPROVE", suggested_size_pct=1.0)
    closed_trades = [_win(), _neutral(), _neutral(), _neutral(), _neutral()]

    decision = engine.evaluate(
        signal=signal, review_decision=review_decision, capital=1000.0,
        open_count=0, max_positions=5, closed_trades=closed_trades,
    )

    assert decision.action.value != "SKIP", (
        "4 unfilled/refunded orders (no real losses) must not trigger the "
        f"loss-streak SKIP filter; got action={decision.action.value}, "
        f"reasoning={decision.reasoning}"
    )
