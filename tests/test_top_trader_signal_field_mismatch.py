"""
Regression test: TopTraderTracker._process_trades() never matched a single
real trade to a market, so the Top Trader Copy Signal always contributed
0.0 to bayesian_prob.

Bug (agents/top_trader_signal.py::TopTraderTracker._process_trades()):

    cid = t.get("market", t.get("condition_id", ""))
    ...
    side = t.get("side", "").upper()
    if side in ("BUY", "YES", "1"):
        market_trades[cid]["yes_vol"] += size
    elif side in ("SELL", "NO", "0"):
        market_trades[cid]["no_vol"] += size

data-api.polymarket.com/trades (the endpoint this class actually calls in
refresh()) keys the market id as "conditionId" (camelCase) — never "market"
or "condition_id". agents/copytrade.py hits this exact same endpoint and
already reads t.get("conditionId", "") correctly, confirming the real
schema. So `cid` was always "", `if not cid: continue` fired on every
trade, `market_trades` (and therefore `self._cache`) stayed permanently
empty, and get_signal()/get_boost() always returned the NEUTRAL/0.0
default.

This is consumed for real money in strategies/arbitrage_engine.py
(_evaluate_market): `_tt_boost = self.top_trader.get_boost(condition_id)`
is added directly into `bayesian_prob`, which drives `edge = prob - price`,
direction selection, and Kelly sizing for every live trade. A permanently
empty cache means the Top Trader Copy Signal — documented as one of the
live signal sources — silently contributed nothing, ever.

Second, compounding bug in the same loop: `side` (BUY/SELL — i.e. opened or
closed a position) was read alone as if it were the outcome (YES/NO). The
real outcome traded is in a separate "outcome" field (e.g. "Up"/"Down").
Fixing only the cid lookup without also combining side+outcome (as
copytrade.py does) would flip the signal's sign for SELL-of-YES /
BUY-of-NO trades.
"""
from __future__ import annotations

from agents.top_trader_signal import TopTraderTracker


def _tracker() -> TopTraderTracker:
    return TopTraderTracker(session=None)


def test_conditionid_field_is_matched_not_market_or_condition_id():
    """Real data-api schema: trades key the market id as 'conditionId'."""
    tracker = _tracker()
    trades = [
        {"conditionId": "0xabc", "side": "BUY", "outcome": "YES", "size": 20},
        {"conditionId": "0xabc", "side": "BUY", "outcome": "YES", "size": 20},
    ]

    tracker._process_trades(trades)

    assert "0xabc" in tracker._cache, (
        "trade with a real 'conditionId' field was dropped — cid lookup is "
        "still reading the wrong key(s)."
    )
    signal = tracker.get_signal("0xabc")
    assert signal.direction == "BULLISH"
    assert tracker.get_boost("0xabc") > 0.0


def test_market_and_condition_id_keys_are_not_the_real_schema():
    """Non-regression: trades using the OLD (wrong) key names must not be
    silently 'supported' by accident — they simply aren't real payload
    shapes, so they correctly produce no signal."""
    tracker = _tracker()
    trades = [
        {"market": "0xold1", "side": "BUY", "outcome": "YES", "size": 100},
        {"condition_id": "0xold2", "side": "BUY", "outcome": "YES", "size": 100},
    ]

    tracker._process_trades(trades)

    assert tracker._cache == {}


def test_side_alone_does_not_drive_direction_outcome_matters_too():
    """A SELL of the YES token is bearish, not bullish — side must be
    combined with outcome, not read alone as if side WAS the outcome."""
    tracker = _tracker()
    trades = [
        {"conditionId": "0xdef", "side": "SELL", "outcome": "YES", "size": 30},
        {"conditionId": "0xdef", "side": "SELL", "outcome": "YES", "size": 30},
    ]

    tracker._process_trades(trades)

    signal = tracker.get_signal("0xdef")
    assert signal.direction == "BEARISH", (
        f"got {signal.direction} — SELL of the YES outcome must count as "
        "bearish (no_vol), not be misread as a bullish 'BUY-like' side."
    )
    assert tracker.get_boost("0xdef") < 0.0


def test_buy_of_no_outcome_counts_as_bearish_not_bullish():
    """A BUY of the NO/DOWN token is bearish — reading `side == "BUY"` alone
    (the old bug) would have wrongly counted this as yes_vol."""
    tracker = _tracker()
    trades = [
        {"conditionId": "0xghi", "side": "BUY", "outcome": "DOWN", "size": 40},
        {"conditionId": "0xghi", "side": "BUY", "outcome": "DOWN", "size": 40},
    ]

    tracker._process_trades(trades)

    signal = tracker.get_signal("0xghi")
    assert signal.direction == "BEARISH"
    assert signal.total_no_volume == 80.0
    assert signal.total_yes_volume == 0.0


def test_up_down_outcome_labels_are_supported_like_copytrade():
    """Crypto up/down markets label outcomes 'Up'/'Down', not 'YES'/'NO'
    (see agents/copytrade.py's CRYPTO_TRADERS handling of this same
    endpoint) — must be recognized the same way."""
    tracker = _tracker()
    trades = [
        {"conditionId": "0xjkl", "side": "BUY", "outcome": "Up", "size": 15},
        {"conditionId": "0xjkl", "side": "BUY", "outcome": "Up", "size": 15},
    ]

    tracker._process_trades(trades)

    signal = tracker.get_signal("0xjkl")
    assert signal.direction == "BULLISH"
    assert signal.total_yes_volume == 30.0
