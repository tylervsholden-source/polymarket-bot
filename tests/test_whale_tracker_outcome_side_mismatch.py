"""
Regression test: WhaleTracker._analyze() read only the trade's `side`
(BUY/SELL) field and ignored `outcome` (YES/UP vs NO/DOWN) entirely.

Bug (agents/whale_tracker.py::WhaleTracker._analyze()):

    if side == "BUY":
        large_buys += 1
    elif side == "SELL":
        large_sells += 1

data-api.polymarket.com/trades (the endpoint WhaleTracker._fetch_recent_trades
calls) reports `side` as BUY/SELL of whichever outcome token was traded, and
the traded outcome separately as "outcome" (e.g. "Up"/"Down" for the crypto
up-or-down markets this bot trades — see artifacts/recent_trades.json).
A BUY of the "Down"/NO token is a bearish bet, but the old code counted every
BUY as bullish regardless of which side of the market was bought — the same
side-vs-outcome confusion already fixed for TopTraderTracker in
agents/top_trader_signal.py (see tests/test_top_trader_signal_field_mismatch.py)
and correctly handled in agents/smart_trader_tracker.py's `outcome == "YES"`
check.

This is consumed for real money: agents/orchestrator.py wires the real
WhaleTracker into AgentCoordinator -> ResearchAgent -> ResearchResult, and
agents/subagents/signal_agent_v2.py reads whale_direction/smart_money_signal
from it to move confluence_score and set/unset the WHALE_OPPOSITION risk
flag, both feeding AutonomousDecisionEngine's sizing/risk decisions. A wave
of large "Down"-side buying was reported as direction="BULLISH", inverting
the real whale signal whenever flow concentrated on the NO/Down side.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agents.whale_tracker import WhaleTracker


def _tracker() -> WhaleTracker:
    return WhaleTracker()


def _recent_ts() -> str:
    """A timestamp safely inside WhaleTracker's 6h smart-money window,
    regardless of what time this test happens to run at (a hardcoded
    absolute timestamp would fall outside the window and flake once
    the wall clock passes it)."""
    return (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def test_buy_of_down_outcome_counts_as_bearish_not_bullish():
    """A BUY of the NO/DOWN token is bearish — reading `side == "BUY"` alone
    (the old bug) would have wrongly counted this as a large/smart buy."""
    tracker = _tracker()
    trades = [
        {
            "side": "BUY",
            "outcome": "Down",
            "size": 20_000,
            "price": 0.5,
            "timestamp": _recent_ts(),
        },
        {
            "side": "BUY",
            "outcome": "Down",
            "size": 20_000,
            "price": 0.5,
            "timestamp": _recent_ts(),
        },
    ]

    result = tracker._analyze(trades)

    assert result["direction"] == "BEARISH", (
        f"got {result['direction']} — BUY of the Down/NO outcome must count "
        "as bearish, not be misread as a bullish BUY side."
    )
    assert result["whale_alignment"] == "SELL"
    assert result["smart_money_buys"] == 0
    assert result["smart_money_sells"] == 2


def test_sell_of_up_outcome_counts_as_bearish():
    """A SELL of the YES/UP token is bearish (closing/shorting the up side),
    not bullish."""
    tracker = _tracker()
    trades = [
        {
            "side": "SELL",
            "outcome": "Up",
            "size": 1_000,
            "price": 0.6,
            "timestamp": _recent_ts(),
        },
    ]

    result = tracker._analyze(trades)

    assert result["large_sells"] == 1
    assert result["large_buys"] == 0


def test_buy_of_up_outcome_still_counts_as_bullish():
    """Non-regression: the normal BUY-of-YES/UP case must still be bullish."""
    tracker = _tracker()
    trades = [
        {
            "side": "BUY",
            "outcome": "Up",
            "size": 20_000,
            "price": 0.5,
            "timestamp": _recent_ts(),
        },
        {
            "side": "BUY",
            "outcome": "Up",
            "size": 20_000,
            "price": 0.5,
            "timestamp": _recent_ts(),
        },
    ]

    result = tracker._analyze(trades)

    assert result["direction"] == "BULLISH"
    assert result["whale_alignment"] == "BUY"
    assert result["smart_money_buys"] == 2
