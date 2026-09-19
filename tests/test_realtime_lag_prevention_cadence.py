"""
Regression test: REALTIME LAG PREVENTION (`arbitrage_engine.py`'s
RT_LAG_BLOCK_YES/RT_LAG_BLOCK_NO gate) silently never fired in production
because `BinanceFeed.get_recent_change()` read from `self._price_history`,
which is only appended to once per `BinanceFeed.refresh()` call — and
`refresh()` runs exactly once per orchestrator cycle (60-120s, see
CLAUDE.md's "adaptif dongu: 60-120sn").

Bug (agents/binance_feed.py::get_recent_change(), before this fix):

    def get_recent_change(self, symbol, seconds=60):
        history = self._price_history.get(symbol, [])
        ...
        cutoff = now_ts - seconds   # 60s window
        for ts, p in history:
            if ts >= cutoff:
                old_price = p
                break
        ...

With one data point appended per 60-120s cycle, the last-60-seconds window
almost never contains 2 points, so `old_price` stays `None` and the
function returns `0.0` on essentially every call. Since the RT_LAG gate in
`strategies/arbitrage_engine.py` only blocks when `abs(rt_change) >
_RT_THRESHOLD (0.15)`, a permanent 0.0 means the gate documented as
"Check last 60s actual price movement... block the trade to avoid chasing
stale momentum" never actually blocks anything.

The fix: `agents/ws_feed.py`'s `RealtimeFeed` already runs a background
thread that receives a live trade tick from Bitstamp continuously (many
times per minute for BTC/ETH), it just didn't retain any history — only
the single latest price/timestamp. This adds a capped per-trade history
and a `get_change_pct()` reader to `RealtimeFeed`, and makes
`BinanceFeed.get_recent_change()` prefer it (falling back to the old
coarse `_price_history` path when the WS feed has no data yet, e.g. right
after startup or when `websocket-client` isn't installed).
"""
from __future__ import annotations

import time

from agents.binance_feed import BinanceFeed
from agents.ws_feed import RealtimeFeed


def test_realtime_feed_change_pct_needs_two_points():
    feed = RealtimeFeed()
    assert feed.get_change_pct("BTCUSDT") is None
    feed._price_history["BTCUSDT"] = __import__("collections").deque(
        [(time.time(), 100.0)], maxlen=2000
    )
    assert feed.get_change_pct("BTCUSDT") is None


def test_realtime_feed_change_pct_ignores_points_outside_window():
    feed = RealtimeFeed()
    now = time.time()
    # Both points are older than the 60s window -> no in-window comparison price.
    feed._price_history["BTCUSDT"] = __import__("collections").deque(
        [(now - 500, 100.0), (now - 400, 101.0)], maxlen=2000
    )
    assert feed.get_change_pct("BTCUSDT", seconds=60) is None


def test_realtime_feed_change_pct_computes_real_move():
    feed = RealtimeFeed()
    now = time.time()
    feed._price_history["BTCUSDT"] = __import__("collections").deque(
        [(now - 45, 100.0), (now - 20, 100.5), (now - 1, 100.3)], maxlen=2000
    )
    change = feed.get_change_pct("BTCUSDT", seconds=60)
    assert change is not None
    assert round(change, 4) == round((100.3 - 100.0) / 100.0 * 100, 4)


def test_ws_on_message_records_history_per_trade():
    feed = RealtimeFeed()
    feed._reverse_map = {"live_trades_btcusd": "BTCUSDT"}
    import json as _json
    for price in (100.0, 100.4, 100.9):
        feed._on_message(
            None,
            _json.dumps({"event": "trade", "channel": "live_trades_btcusd", "data": {"price": str(price)}}),
        )
    history = feed._price_history["BTCUSDT"]
    assert len(history) == 3
    assert [p for _, p in history] == [100.0, 100.4, 100.9]


def test_binance_feed_prefers_ws_realtime_change_over_coarse_history():
    feed = BinanceFeed.__new__(BinanceFeed)  # skip __init__ (no network needed)
    feed._price_history = {"BTCUSDT": []}  # coarse fallback would return 0.0 here

    class _StubWS:
        def get_change_pct(self, symbol, seconds=60):
            return 1.23

    feed._ws_feed = _StubWS()
    assert feed.get_recent_change("BTCUSDT", seconds=60) == 1.23


def test_binance_feed_falls_back_when_ws_has_no_data():
    feed = BinanceFeed.__new__(BinanceFeed)
    now = time.time()
    # Coarse per-cycle history DOES have 2 points inside the window here.
    feed._price_history = {"BTCUSDT": [(now - 50, 100.0), (now - 1, 101.0)]}

    class _StubWS:
        def get_change_pct(self, symbol, seconds=60):
            return None  # WS not ready yet

    feed._ws_feed = _StubWS()
    change = feed.get_recent_change("BTCUSDT", seconds=60)
    assert round(change, 4) == round((101.0 - 100.0) / 100.0 * 100, 4)


def test_binance_feed_works_without_ws_feed_at_all():
    feed = BinanceFeed.__new__(BinanceFeed)
    feed._price_history = {}
    feed._ws_feed = None
    assert feed.get_recent_change("BTCUSDT", seconds=60) == 0.0
