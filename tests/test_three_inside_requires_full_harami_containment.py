"""
Regression: THREE_INSIDE_UP/DOWN only checked ONE bound of the harami
containment, so a second candle that merely poked past the OTHER bound of
the first candle's body (not actually "inside" it at all) still fired the
pattern.

core/candlestick_analyzer.py's rule 18 (THREE_INSIDE_UP) required:

    body2 < body3 * 0.5 and CA._body_top(c2) < c3[1]

— only the top bound (c2's body stays below c3's open). It never checked
CA._body_bot(c2) > c3[4] (c2's body stays above c3's close). A c2 whose
body extended BELOW c3's close (i.e. hangs out the bottom of c3's body)
still passed as long as its top stayed under c3's open, firing the
+0.7-score reversal pattern on two candles that don't actually form a
harami. Rule 19 (THREE_INSIDE_DOWN) had the mirror-image bug: it checked
CA._body_bot(c2) > c3[1] but never CA._body_top(c2) < c3[4].

For comparison, the plain two-candle BULLISH_HARAMI/BEARISH_HARAMI rules
just above (#10/#11) already check both bounds correctly.

Live impact (strategies/arbitrage_engine.py): THREE_INSIDE_UP/DOWN are in
_BULLISH_REVERSAL/_BEARISH_REVERSAL and contribute +-0.7 to _pattern_score.
A single false-positive detection is enough on its own to cross the
_pattern_bullish (score >= 0.6) / _pattern_bearish (score <= -0.4)
thresholds and fire CANDLE_YES_ACTIVATE / CANDLE_NO_ACTIVATE on candles
that were never actually a harami.
"""
from core.candlestick_analyzer import CandlestickAnalyzer as CA


# c3 bearish: open=100, close=90 (body 90-100).
# c2 bullish: open=85, close=88 -> body_top=88 (< c3 open, passes old check)
#             but body_bot=85, which is BELOW c3's close (90) -> c2 is NOT
#             contained inside c3's body at all.
# c1 bullish, closes above c3's open to satisfy the confirmation leg.
_UP_NOT_CONTAINED = [
    [0, 100, 101, 89, 90, 1000],   # c3: bearish
    [0, 85, 89, 84, 88, 1000],     # c2: bullish, pokes out below c3's close
    [0, 88, 106, 87, 105, 1000],   # c1: bullish, confirms
]

# Genuine THREE_INSIDE_UP: c2's body fully inside c3's body.
_UP_CONTAINED = [
    [0, 100, 101, 89, 90, 1000],   # c3: bearish, body 90-100
    [0, 92, 96, 91, 95, 1000],     # c2: bullish, body 92-95 (fully inside)
    [0, 95, 106, 94, 105, 1000],   # c1: bullish, confirms (> 100)
]

# Mirror case for THREE_INSIDE_DOWN: c3 bullish (open=90, close=100),
# c2 bearish with body_bot=99 (> c3 open=90, passes old check) but
# body_top=102, which pokes out ABOVE c3's close (100).
_DOWN_NOT_CONTAINED = [
    [0, 90, 101, 89, 100, 1000],   # c3: bullish, body 90-100
    [0, 102, 103, 98, 99, 1000],   # c2: bearish, pokes out above c3's close
    [0, 99, 100, 80, 85, 1000],    # c1: bearish, confirms (< 90)
]

_DOWN_CONTAINED = [
    [0, 90, 101, 89, 100, 1000],   # c3: bullish, body 90-100
    [0, 97, 98, 93, 94, 1000],     # c2: bearish, body 94-97 (fully inside)
    [0, 94, 95, 78, 80, 1000],     # c1: bearish, confirms (< 90)
]


def test_three_inside_up_rejects_non_contained_middle_candle():
    patterns = CA.analyze(_UP_NOT_CONTAINED)
    assert "THREE_INSIDE_UP" not in patterns, (
        f"c2's body pokes out below c3's close, not a real harami: {patterns}"
    )


def test_three_inside_up_still_fires_for_genuine_harami():
    patterns = CA.analyze(_UP_CONTAINED)
    assert "THREE_INSIDE_UP" in patterns


def test_three_inside_down_rejects_non_contained_middle_candle():
    patterns = CA.analyze(_DOWN_NOT_CONTAINED)
    assert "THREE_INSIDE_DOWN" not in patterns, (
        f"c2's body pokes out above c3's close, not a real harami: {patterns}"
    )


def test_three_inside_down_still_fires_for_genuine_harami():
    patterns = CA.analyze(_DOWN_CONTAINED)
    assert "THREE_INSIDE_DOWN" in patterns


def test_three_inside_up_false_positive_no_longer_crosses_bullish_threshold():
    """Mirrors arbitrage_engine's _pattern_bullish = _pattern_score >= 0.6."""
    patterns = CA.analyze(_UP_NOT_CONTAINED)
    score = CA.pattern_score(patterns)
    assert score < 0.6, (
        f"a rejected THREE_INSIDE_UP must not alone cross the bullish "
        f"pattern-score threshold: score={score} patterns={patterns}"
    )


def test_three_inside_down_false_positive_no_longer_crosses_bearish_threshold():
    """Mirrors arbitrage_engine's _pattern_bearish = _pattern_score <= -0.4."""
    patterns = CA.analyze(_DOWN_NOT_CONTAINED)
    score = CA.pattern_score(patterns)
    assert score > -0.4, (
        f"a rejected THREE_INSIDE_DOWN must not alone cross the bearish "
        f"pattern-score threshold: score={score} patterns={patterns}"
    )
