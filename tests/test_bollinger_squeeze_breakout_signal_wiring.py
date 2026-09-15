"""
Regression test: BinanceFeed.get_signal() drops the bb_squeeze/bb_breakout
fields it itself computes, so strategies/bayesian.py's Bollinger
breakout/squeeze signal never actually fires live — identical in shape to
the already-fixed "bullish_exhaustion dropped by get_signal()" bug (46th
daily review, tests/test_bullish_exhaustion_signal_wiring.py).

Bug (agents/binance_feed.py::get_signal()):

BinanceFeed's per-interval refresh (agents/binance_feed.py, the block that
builds the `result` dict around "Bollinger Bands") computes and caches
four Bollinger fields per (symbol, timeframe) via its internal Bollinger
helper (agents/binance_feed.py lines ~1287-1329):

    "bb_width":            float  — band width %
    "bb_pos":              float  — position within bands (0=lower, 1=upper)
    "bb_squeeze":          bool   — bandwidth < 1.2% (low-volatility compression)
    "bb_breakout":         float  — -1.0..+1.0, price crossing the bands
    "bb_bandwidth_pctile": float  — bandwidth percentile vs recent history

strategies/arbitrage_engine.py._evaluate_market() reads all of these
straight off the dict returned by BinanceFeed.get_signal():

    bb_pos       = spot["bb_pos"]
    bb_width     = spot["bb_width"]
    bb_squeeze   = spot.get("bb_squeeze", False)
    bb_breakout  = spot.get("bb_breakout", 0.0)

and forwards bb_squeeze/bb_breakout straight into
strategies/bayesian.py::BayesianEstimator.estimate(), where they gate a
real part of the probability signal:

    bb_signal = (bb_pos - 0.5) * 0.8
    if bb_breakout != 0.0:
        bb_signal = bb_breakout * (1.5 if bb_squeeze else 1.0)
    elif bb_squeeze:
        bb_signal *= 0.2

bb_signal then contributes 5% weight to raw_signal, which feeds the
log-odds Bayesian update that ultimately drives direction/edge/Kelly
sizing.

But get_signal() (agents/binance_feed.py, the dict returned around
"New technical indicators") forwards bb_width and bb_pos but never
forwards bb_squeeze or bb_breakout — every other Bollinger field it
computes makes it through except these two. Net effect:
`spot.get("bb_squeeze", False)` and `spot.get("bb_breakout", 0.0)` always
see the default (the keys are absent from the dict, not merely
False/0.0), so real Bollinger-band squeeze/breakout events never reach
the Bayesian estimator live — the model silently falls back to the plain
`(bb_pos - 0.5) * 0.8` term and the actual band-crossing/compression
signal is permanently dead code in live trading, identical in shape to
the OPT-7 and bullish_exhaustion field-mismatch bugs.
"""
from __future__ import annotations

from agents.binance_feed import BinanceFeed
from strategies.bayesian import BayesianEstimator


def _feed_with_bb(squeeze: bool, breakout: float) -> BinanceFeed:
    """Real BinanceFeed with a pre-populated cache — no network calls."""
    feed = BinanceFeed.__new__(BinanceFeed)
    feed._cache = {
        "BTCUSDT": {
            "price": 50000.0,
            "ob_imbalance": 0.0,
            "intervals": {
                "5m": {
                    "change_pct": 0.0,
                    "bb_pos": 0.5,
                    "bb_width": 0.008,
                    "bb_squeeze": squeeze,
                    "bb_breakout": breakout,
                }
            },
        }
    }
    feed._fng_value = 50
    return feed


def test_get_signal_forwards_bb_squeeze_flag():
    feed = _feed_with_bb(squeeze=True, breakout=0.9)

    signal = feed.get_signal("BTCUSDT", "5m")

    assert signal["bb_squeeze"] is True, (
        "BinanceFeed itself computed bb_squeeze=True and cached it in "
        "iv_data, but get_signal() dropped the key — "
        "spot.get('bb_squeeze', False) in arbitrage_engine.py always fell "
        "back to the False default."
    )
    assert signal["bb_breakout"] == 0.9, (
        "BinanceFeed itself computed bb_breakout=0.9 and cached it in "
        "iv_data, but get_signal() dropped the key — "
        "spot.get('bb_breakout', 0.0) in arbitrage_engine.py always fell "
        "back to the 0.0 default."
    )


def test_get_signal_forwards_non_squeeze_state_too():
    feed = _feed_with_bb(squeeze=False, breakout=-1.0)

    signal = feed.get_signal("BTCUSDT", "5m")

    assert signal["bb_squeeze"] is False
    assert signal["bb_breakout"] == -1.0


def test_evaluate_market_consumer_contract_sees_the_real_value():
    """Mirrors exactly what ArbitrageEngine._evaluate_market() does with the
    dict BinanceFeed.get_signal() returns — reproduces the bug's live impact
    without needing to drive the whole 1800-line analyze() pipeline."""
    feed = _feed_with_bb(squeeze=True, breakout=0.9)
    spot = feed.get_signal("BTCUSDT", "5m")

    bb_pos = spot["bb_pos"]
    bb_squeeze = spot.get("bb_squeeze", False)
    bb_breakout = spot.get("bb_breakout", 0.0)

    assert bb_squeeze is True, (
        "BayesianEstimator.estimate() reads this exact expression — with "
        "the old get_signal() it always evaluated to False regardless of "
        "what BinanceFeed actually detected."
    )
    assert bb_breakout == 0.9

    # Feed the (correctly forwarded) values into the real Bayesian
    # estimator and confirm they actually move the probability, proving
    # this is not a dead/unused field.
    est = BayesianEstimator()
    result_with_signal = est.estimate(
        market_price=0.5,
        spot_change_pct=0.0,
        volatility=0.0,
        order_book_imbalance=0.0,
        bb_pos=bb_pos,
        bb_squeeze=bb_squeeze,
        bb_breakout=bb_breakout,
    )
    result_without_signal = est.estimate(
        market_price=0.5,
        spot_change_pct=0.0,
        volatility=0.0,
        order_book_imbalance=0.0,
        bb_pos=bb_pos,
        bb_squeeze=False,   # pre-fix get_signal() default
        bb_breakout=0.0,    # pre-fix get_signal() default
    )

    assert result_with_signal.probability != result_without_signal.probability, (
        "A real Bollinger squeeze+breakout must move the Bayesian "
        "probability away from the no-signal baseline — if get_signal() "
        "drops bb_squeeze/bb_breakout, every live call sees the "
        "no-signal baseline regardless of the real market state."
    )
    assert result_with_signal.probability > 0.5, (
        "bb_breakout=0.9 (bullish upper-band breakout) should push the "
        "probability above the neutral 0.5 prior."
    )
