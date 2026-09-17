"""
Regression: HANGING_MAN (bearish) was always accompanied by HAMMER (bullish).

core/candlestick_analyzer.py emitted HAMMER for the hammer *shape*
unconditionally, while HANGING_MAN was emitted for the same shape whenever
the previous candle was bullish. A textbook hanging man therefore produced
BOTH names, and pattern_score() summed +0.5 (HAMMER) with -0.5
(HANGING_MAN) to exactly 0.0 — the bearish reversal signal was erased.

Live impact (strategies/arbitrage_engine.py):
  * _pattern_bearish = (_pattern_score <= -0.4) never fired for a hanging
    man, so neither the NO side nor the YES-activation block was triggered.
  * "HAMMER" landed in _bullish_patterns and made
    _has_strong_bullish_pattern True; combined with consecutive_bullish >= 2
    (the uptrend a hanging man by definition appears in) that set
    _pattern_bullish / _bounce_active and could fire CANDLE_YES_ACTIVATE —
    a bearish candle activating the YES (up) side.

The INVERTED_HAMMER / SHOOTING_STAR pair (identical shape, mirrored) was
already split correctly by the previous candle's direction; this test pins
the same exclusivity for HAMMER / HANGING_MAN.
"""
from core.candlestick_analyzer import CandlestickAnalyzer as CA


# Uptrend + textbook hanging man: small body at the top, long lower wick,
# (almost) no upper wick.
_UPTREND_HANGING_MAN = [
    [0, 100.00, 100.50, 99.90, 100.40, 1000],    # bullish
    [0, 100.40, 100.90, 100.30, 100.80, 1000],   # bullish
    [0, 100.80, 100.85, 100.50, 100.85, 1000],   # hanging man
]

# Downtrend + textbook hammer (control — must stay bullish).
_DOWNTREND_HAMMER = [
    [0, 100.80, 100.90, 100.30, 100.40, 1000],   # bearish
    [0, 100.40, 100.50, 99.90, 100.00, 1000],    # bearish
    [0, 100.00, 100.05, 99.70, 100.05, 1000],    # hammer
]


def test_hanging_man_is_not_also_reported_as_hammer():
    patterns = CA.analyze(_UPTREND_HANGING_MAN)
    assert "HANGING_MAN" in patterns
    assert "HAMMER" not in patterns, (
        f"hammer shape after a bullish candle is a HANGING_MAN, not a HAMMER: {patterns}"
    )


def test_hanging_man_keeps_its_bearish_score():
    patterns = CA.analyze(_UPTREND_HANGING_MAN)
    score = CA.pattern_score(patterns)
    # Was exactly 0.0 before the fix (+0.5 HAMMER and -0.5 HANGING_MAN).
    assert score <= -0.4, f"hanging man must score bearish, got {score} from {patterns}"


def test_downtrend_hammer_still_bullish():
    patterns = CA.analyze(_DOWNTREND_HAMMER)
    assert "HAMMER" in patterns
    assert "HANGING_MAN" not in patterns
    assert CA.pattern_score(patterns) >= 0.5


def test_hanging_man_does_not_look_like_a_strong_bullish_pattern():
    """Mirrors arbitrage_engine's _has_strong_bullish_pattern / _pattern_bullish."""
    patterns = CA.analyze(_UPTREND_HANGING_MAN)
    strong_bullish = {
        "BULLISH_MARUBOZU", "BULLISH_ENGULFING", "HAMMER",
        "THREE_WHITE_SOLDIERS", "MORNING_STAR",
    }
    consecutive_bullish = CA.trend_analysis(_UPTREND_HANGING_MAN)["consecutive_same_dir"]
    assert consecutive_bullish >= 2  # the uptrend context the bug needed

    has_strong_bullish = any(p in strong_bullish for p in patterns)
    score = CA.pattern_score(patterns)
    pattern_bullish = score >= 0.6 or (has_strong_bullish and consecutive_bullish >= 2)
    pattern_bearish = score <= -0.4

    assert not has_strong_bullish
    assert not pattern_bullish, "a hanging man must not activate the YES side"
    assert pattern_bearish
