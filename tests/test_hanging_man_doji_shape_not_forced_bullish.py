"""
Regression: a doji-bodied hanging man was forced into HAMMER (bullish).

core/candlestick_analyzer.py's HAMMER/HANGING_MAN split used:

    if body1 > 0 and lw1 >= body1 * 2 and uw1 <= body1 * 0.5:
        if not is_doji and CA._is_bullish(c2):
            patterns.append("HANGING_MAN")
        else:
            patterns.append("HAMMER")

`is_doji` describes c1 (the hammer/hanging-man candle itself — body <= 10%
of its own range), not c2 (the preceding candle whose direction is supposed
to be the ONLY thing that tells HAMMER and HANGING_MAN apart, per the
docstring/comment directly above this code).

A hammer/hanging-man shape has a small body by definition, so it very
commonly also satisfies the DOJI/DRAGONFLY_DOJI threshold (body <= 10% of
range). Whenever that happened, `not is_doji` was False, so the `else`
branch fired unconditionally — HAMMER — even when c2 was bullish, where the
textbook classification is the bearish HANGING_MAN.

Live impact (strategies/arbitrage_engine.py):
  * "HAMMER" landed in _bullish_patterns / made _has_strong_bullish_pattern
    True (it's explicitly in that tuple), so combined with
    consecutive_bullish >= 2 (the uptrend a hanging man appears in) this
    could set _pattern_bullish and fire CANDLE_YES_ACTIVATE — buying the UP
    side on what is actually a bearish reversal candle.
  * pattern_score() returned a net-positive score (DRAGONFLY_DOJI +0.3 and
    HAMMER +0.5) instead of a bearish one, so _pattern_bearish
    (score <= -0.4) never had a chance to fire either.

Fix: the branch must key off c2's direction alone, matching the docstring
("Only the preceding candle's direction tells them apart") and the already-
correct INVERTED_HAMMER / SHOOTING_STAR split just below it.
"""
from core.candlestick_analyzer import CandlestickAnalyzer as CA


# Uptrend (c2 bullish) + a hanging-man shape whose tiny body also crosses
# the DOJI threshold (body=0.05, range=1.05 -> body/range ~4.8% <= 10%).
_UPTREND_DOJI_SHAPED_HANGING_MAN = [
    [0, 99.50, 99.60, 99.40, 99.55, 1000],    # filler
    [0, 99.55, 100.00, 99.50, 99.95, 1000],   # bullish (uptrend context)
    [0, 100.00, 100.05, 99.00, 100.05, 1000], # doji-shaped hanging man
]


def test_doji_shaped_hanging_man_is_not_reported_as_hammer():
    patterns = CA.analyze(_UPTREND_DOJI_SHAPED_HANGING_MAN)
    assert "HANGING_MAN" in patterns
    assert "HAMMER" not in patterns, (
        f"doji-bodied hammer shape after a bullish candle is a HANGING_MAN, "
        f"not a HAMMER: {patterns}"
    )


def test_doji_shaped_hanging_man_does_not_score_bullish():
    patterns = CA.analyze(_UPTREND_DOJI_SHAPED_HANGING_MAN)
    score = CA.pattern_score(patterns)
    # Was +0.8 before the fix (DRAGONFLY_DOJI +0.3 and HAMMER +0.5).
    assert score < 0, f"doji-shaped hanging man must not score bullish, got {score} from {patterns}"


def test_doji_shaped_hanging_man_does_not_look_like_a_strong_bullish_pattern():
    """Mirrors arbitrage_engine's _has_strong_bullish_pattern / _pattern_bullish."""
    patterns = CA.analyze(_UPTREND_DOJI_SHAPED_HANGING_MAN)
    strong_bullish = {
        "BULLISH_MARUBOZU", "BULLISH_ENGULFING", "HAMMER",
        "THREE_WHITE_SOLDIERS", "MORNING_STAR",
    }
    has_strong_bullish = any(p in strong_bullish for p in patterns)
    score = CA.pattern_score(patterns)
    pattern_bullish = score >= 0.6 or (has_strong_bullish and True)  # consecutive_bullish >= 2 assumed

    assert not has_strong_bullish
    assert not pattern_bullish, "a hanging man must not activate the YES side"


def test_downtrend_doji_shaped_candle_still_reports_hammer():
    """Control: same doji-shape logic, but after a bearish c2 -> still HAMMER."""
    downtrend = [
        [0, 100.50, 100.60, 100.40, 100.45, 1000],  # bearish (downtrend context)
        [0, 100.45, 100.50, 99.45,  99.50, 1000],   # bearish
        [0, 99.50,  99.55,  98.50,  99.55, 1000],   # doji-shaped hammer (same geometry)
    ]
    patterns = CA.analyze(downtrend)
    assert "HAMMER" in patterns
    assert "HANGING_MAN" not in patterns
