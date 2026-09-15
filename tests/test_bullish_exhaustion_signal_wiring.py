"""
Regression test: BinanceFeed.get_signal() dropped the bullish_exhaustion
fields it itself computes, so the live EXHAUSTION_NO_ACTIVATE gate in
strategies/arbitrage_engine.py never fired.

Bug (agents/binance_feed.py::get_signal()):

BinanceFeed's per-interval refresh (agents/binance_feed.py, the block that
builds the `result` dict around "BULLISH EXHAUSTION DETECTION") computes and
caches two fields per (symbol, timeframe):

    "bullish_exhaustion": bool           — 2+ green candles with a
                                            significant cumulative move
    "bullish_exhaustion_magnitude": float — the cumulative % move

strategies/arbitrage_engine.py._evaluate_market() reads them straight off
the dict returned by BinanceFeed.get_signal():

    bullish_exhaustion = spot.get("bullish_exhaustion", False)
    bullish_exhaustion_magnitude = spot.get("bullish_exhaustion_magnitude", 0.0)

and feeds them into the EXHAUSTION_NO_ACTIVATE gate (a legitimate NO-entry
activation on an overextended bullish run — "Piyasa 'çok yükseldi' -> mean
reversion (pullback) olasılığı yüksek. Bu NO trade için ideal giriş
noktası."). But get_signal() never actually forwarded either key in the
dict it builds from `iv_data` — every other bounce-detection field
(consecutive_bullish, consecutive_bearish, bounce_signal,
pre_bounce_streak) was copied over, but bullish_exhaustion and
bullish_exhaustion_magnitude were missing entirely.

Net effect: `spot.get("bullish_exhaustion", False)` always saw the
default (the key was absent from the dict, not merely False), so
EXHAUSTION_NO_ACTIVATE was permanently dead code in live trading —
identical in shape to the already-fixed OPT-7 "consecutive-NO-win bounce
guard checked a field that never exists" bug.
"""
from __future__ import annotations

from agents.binance_feed import BinanceFeed


def _feed_with_exhaustion(exhausted: bool, magnitude: float) -> BinanceFeed:
    """Real BinanceFeed with a pre-populated cache — no network calls."""
    feed = BinanceFeed.__new__(BinanceFeed)
    feed._cache = {
        "BTCUSDT": {
            "price": 50000.0,
            "ob_imbalance": 0.0,
            "intervals": {
                "5m": {
                    "change_pct": 0.10,
                    "consecutive_bullish": 3,
                    "bullish_exhaustion": exhausted,
                    "bullish_exhaustion_magnitude": magnitude,
                }
            },
        }
    }
    feed._fng_value = 50
    return feed


def test_get_signal_forwards_bullish_exhaustion_flag():
    feed = _feed_with_exhaustion(True, 0.45)

    signal = feed.get_signal("BTCUSDT", "5m")

    assert signal["bullish_exhaustion"] is True, (
        "BinanceFeed itself computed bullish_exhaustion=True and cached it "
        "in iv_data, but get_signal() dropped the key — "
        "spot.get('bullish_exhaustion', False) in arbitrage_engine.py "
        "always fell back to the False default."
    )
    assert signal["bullish_exhaustion_magnitude"] == 0.45


def test_get_signal_forwards_non_exhausted_state_too():
    feed = _feed_with_exhaustion(False, 0.0)

    signal = feed.get_signal("BTCUSDT", "5m")

    assert signal["bullish_exhaustion"] is False
    assert signal["bullish_exhaustion_magnitude"] == 0.0


def test_evaluate_market_consumer_contract_sees_the_real_value():
    """Mirrors exactly what ArbitrageEngine._evaluate_market() does with the
    dict BinanceFeed.get_signal() returns — reproduces the bug's live impact
    without needing to drive the whole 1800-line analyze() pipeline."""
    feed = _feed_with_exhaustion(True, 0.62)
    spot = feed.get_signal("BTCUSDT", "5m")

    bullish_exhaustion = spot.get("bullish_exhaustion", False)
    bullish_exhaustion_magnitude = spot.get("bullish_exhaustion_magnitude", 0.0)

    assert bullish_exhaustion is True, (
        "EXHAUSTION_NO_ACTIVATE gate reads this exact expression — with the "
        "old get_signal() it always evaluated to False regardless of what "
        "BinanceFeed actually detected."
    )
    assert bullish_exhaustion_magnitude == 0.62
