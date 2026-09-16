"""
tests/test_pnl_daily_stop_loss_wrong_denominator.py

Regression test for operator_layer/pnl.py::build_equity_state().

Bug: EquityState.blocked_reason (the dashboard's "DAILY_STOP_LOSS" flag)
measured today's percentage loss against the bot's life-of-bot
initial_capital (a fixed env var recorded once at startup), instead of
today's start-of-day capital — the denominator that
core/position_manager.py::daily_loss_exceeded() (the function that
actually gates real order placement in agents/orchestrator.py) uses:

    day_start_capital = capital - daily.pnl
    loss_pct = abs(min(0, daily.pnl)) / day_start_capital
    exceeded = loss_pct >= threshold

Because this bot's entire purpose (CLAUDE.md: $1000 -> $3000 in 20 days)
is for `capital` to drift far away from `initial_capital` over the run,
the two denominators diverge almost immediately in live operation, in
both directions:

  1. Capital has GROWN far above initial_capital: a small loss relative
     to today's real capital gets blown up into a large percentage of the
     tiny original initial_capital -> dashboard falsely reports
     DAILY_STOP_LOSS even though the bot's real stop-loss (measured off
     today's actual start-of-day capital) has NOT tripped and trading is
     still live.

  2. Capital has SHRUNK far below initial_capital: a loss that *has*
     tripped the real day-start-capital-based stop-loss looks tiny against
     the (much larger) original initial_capital -> dashboard falsely
     reports "clear" while the bot has actually already halted trading
     for the day.

Both are exactly the kind of "operator dashboard disagrees with real
enforcement" defect this bot's daily reviews target (c.f. the 36th daily
review's peak-capital drawdown fix, and the 62nd/#62 sync_real_balance
daily.pnl masking fix) — an operator watching the Architect Chamber
dashboard would draw the wrong conclusion about whether the bot is still
trading today.
"""
from __future__ import annotations

from operator_layer.pnl import build_equity_state


def _positions_data(capital: float, pnl: float) -> dict:
    return {
        "capital": capital,
        "positions": {},
        "closed": [],
        "daily": {"date": "2026-09-16", "pnl": pnl},
    }


class TestDailyStopLossUsesRealDayStartCapital:
    def test_grown_capital_small_relative_loss_not_blocked(self):
        """
        Bot grew from $1000 (initial_capital) to $3000. Today's start-of-day
        capital is therefore $3200 (current $3000 + today's $200 loss).
        $200 / $3200 = 6.25% -- well under the 15% threshold, so the real
        daily_loss_exceeded() in core/position_manager.py would NOT trip.
        The dashboard must agree.
        """
        eq = build_equity_state(
            _positions_data(capital=3000.0, pnl=-200.0),
            {"initial_capital": 1000.0},
            [],
            [],
        )
        # Sanity: the bug's formula (200 / 1000 = 20%) would have blocked this.
        assert eq.blocked_reason is None, (
            f"blocked_reason={eq.blocked_reason!r} but real day-start-capital "
            f"loss is only 200/3200=6.25% (< 15%) — dashboard must not falsely "
            f"report DAILY_STOP_LOSS just because initial_capital ($1000) is "
            f"small relative to current capital ($3000)."
        )

    def test_shrunk_capital_relative_loss_is_blocked(self):
        """
        Bot shrank from $1000 (initial_capital) to $200. Today's start-of-day
        capital is $240 (current $200 + today's $40 loss).
        $40 / $240 = 16.7% -- over the 15% threshold, so the real
        daily_loss_exceeded() WOULD trip and halt trading for the day.
        The dashboard must agree.
        """
        eq = build_equity_state(
            _positions_data(capital=200.0, pnl=-40.0),
            {"initial_capital": 1000.0},
            [],
            [],
        )
        # Sanity: the bug's formula (40 / 1000 = 4%) would have missed this.
        assert eq.blocked_reason == "DAILY_STOP_LOSS", (
            "real day-start-capital loss is 40/240=16.7% (>= 15%) — the real "
            "bot has already halted trading for the day, but the dashboard "
            "failed to report DAILY_STOP_LOSS because it divided by the "
            "life-of-bot initial_capital ($1000) instead."
        )

    def test_matches_position_manager_formula_directly(self):
        """Cross-check against the exact formula in
        core/position_manager.py::daily_loss_exceeded() for a range of
        capital/pnl combinations, independent of initial_capital."""
        cases = [
            (500.0, -50.0, 0.15),   # day_start=550, loss=9.1% -> not blocked
            (500.0, -90.0, 0.15),   # day_start=590, loss=15.3% -> blocked
            (10.0, -5.0, 0.15),     # day_start=15, loss=33% -> blocked
        ]
        for capital, pnl, threshold in cases:
            day_start_capital = capital - pnl
            expected_exceeded = (
                day_start_capital <= 0
                or abs(min(0, pnl)) / day_start_capital >= threshold
            )
            eq = build_equity_state(
                _positions_data(capital=capital, pnl=pnl),
                {"initial_capital": 999999.0},  # deliberately huge/irrelevant
                [],
                [],
            )
            got_blocked = eq.blocked_reason == "DAILY_STOP_LOSS"
            assert got_blocked == expected_exceeded, (
                f"capital={capital} pnl={pnl}: expected blocked={expected_exceeded} "
                f"got={got_blocked} (day_start_capital={day_start_capital})"
            )
