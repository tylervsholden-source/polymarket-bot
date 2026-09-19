"""
Regression test: THREE_WHITE_SOLDIERS / THREE_BLACK_CROWS body-strength
gate compared body2/body3 against range1 (the LAST candle's range) instead
of each candle's own range (range2/range3).

Bug (core/candlestick_analyzer.py::CandlestickAnalyzer.analyze()):

    range1, range2 = CA._range(c1), CA._range(c2)   # range3 never computed
    ...
    if (... and
            body3 > range1 * 0.3 and body2 > range1 * 0.3 and body1 > range1 * 0.3):
        patterns.append("THREE_WHITE_SOLDIERS")

`range3` was never even defined in the function, and body2/body3 (the
strength of the OLDER two candles) were checked against range1 (the range
of the newest candle) instead of their own range2/range3. Whenever the
most recent candle happened to have a small range, this made the 30%
body-strength bar for the older two candles trivially easy to clear — two
weak, near-doji candles (mostly wick, e.g. 8%/19% body-to-range) could
satisfy `body > range1 * 0.3` even though they clearly fail the real
per-candle test (`body > own_range * 0.3`).

This is consumed for real money: `agents/binance_feed.py` folds
`CandlestickAnalyzer.pattern_score()` into a 10%-weighted composite signal,
and `strategies/arbitrage_engine.py` puts THREE_WHITE_SOLDIERS/
THREE_BLACK_CROWS directly in `_BULLISH_REVERSAL`/`_BEARISH_REVERSAL` and
`_has_strong_bullish_pattern`/`_pattern_bearish`, which gate YES/NO trade
activation. THREE_WHITE_SOLDIERS/THREE_BLACK_CROWS are also the single
highest-magnitude entries in `_PATTERN_SCORES` (+/-0.9), so a false
positive here produces the strongest possible directional bias the
candlestick module can emit, from candles that are actually weak/
indecisive.
"""
from __future__ import annotations

from core.candlestick_analyzer import CandlestickAnalyzer


def test_three_white_soldiers_rejects_weak_bodied_older_candles():
    # c3, c2: bullish but weak-bodied relative to THEIR OWN (large) range
    # (8.3% and 18.75% body-to-range — near-doji, not "strong conviction"
    # soldiers). c1: small range, so range1*0.3 is tiny.
    klines = [
        [0, 100.0, 104.0, 98.0, 100.5, 100],   # c3: range=6.0, body=0.5 (8.3%)
        [0, 100.5, 107.0, 99.0, 102.0, 100],   # c2: range=8.0, body=1.5 (18.75%)
        [0, 102.0, 102.3, 101.9, 102.2, 100],  # c1: range=0.4, body=0.2 (50%)
    ]
    patterns = CandlestickAnalyzer.analyze(klines)
    assert "THREE_WHITE_SOLDIERS" not in patterns
    # Falsely getting the single strongest bullish score in the table
    assert CandlestickAnalyzer.pattern_score(patterns) < 0.9


def test_three_black_crows_rejects_weak_bodied_older_candles():
    # Mirror image: bearish, weak-bodied c3/c2, small-range c1.
    klines = [
        [0, 100.5, 103.5, 97.5, 100.0, 100],   # c3: range=6.0, body=0.5 (8.3%)
        [0, 99.9, 103.0, 95.0, 98.4, 100],     # c2: range=8.0, body=1.5 (18.75%)
        [0, 98.3, 98.32, 97.92, 98.1, 100],    # c1: range=0.4, body=0.2 (50%)
    ]
    patterns = CandlestickAnalyzer.analyze(klines)
    assert "THREE_BLACK_CROWS" not in patterns
    assert CandlestickAnalyzer.pattern_score(patterns) > -0.9


def test_three_white_soldiers_still_detects_genuine_pattern():
    # Control: all three candles genuinely strong-bodied relative to their
    # OWN range, and ascending closes — must still fire post-fix.
    klines = [
        [0, 100.0, 102.2, 99.9, 102.0, 100],   # body 2.0/range 2.3 (~87%)
        [0, 102.0, 104.3, 101.8, 104.0, 100],  # body 2.0/range 2.5 (80%)
        [0, 104.0, 106.2, 103.9, 106.0, 100],  # body 2.0/range 2.3 (~87%)
    ]
    patterns = CandlestickAnalyzer.analyze(klines)
    assert "THREE_WHITE_SOLDIERS" in patterns
    assert CandlestickAnalyzer.pattern_score(patterns) == 0.9


def test_three_black_crows_still_detects_genuine_pattern():
    klines = [
        [0, 106.0, 106.2, 103.9, 104.0, 100],
        [0, 104.0, 104.3, 101.8, 102.0, 100],
        [0, 102.0, 102.2, 99.9, 100.0, 100],
    ]
    patterns = CandlestickAnalyzer.analyze(klines)
    assert "THREE_BLACK_CROWS" in patterns
    assert CandlestickAnalyzer.pattern_score(patterns) == -0.9
