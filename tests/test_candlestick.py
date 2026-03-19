import pytest
from core.candlestick_analyzer import CandlestickAnalyzer

def test_doji():
    # open=close, high/low spread
    klines = [
        [0, 100, 110, 90, 100, 100],
        [0, 100, 110, 90, 100, 100],
        [0, 100, 110, 90, 100.01, 100] # Close almost equal to open
    ]
    patterns = CandlestickAnalyzer.analyze(klines)
    assert "DOJI" in patterns

def test_hammer():
    # small body, long lower wick
    klines = [
        [0, 100, 110, 90, 100, 100],
        [0, 100, 110, 90, 100, 100],
        [0, 100, 101, 70, 105, 100] # Open=100, High=101, Low=70, Close=105 -> Body=5, Lower Wick=30 (6x body)
    ]
    patterns = CandlestickAnalyzer.analyze(klines)
    assert "HAMMER" in patterns

def test_bullish_engulfing():
    klines = [
        [0, 100, 110, 90, 100, 100],
        [0, 100, 105, 95, 96, 100], # Bearish
        [0, 95, 102, 94, 101, 100]  # Bullish, engulfs body of previous
    ]
    patterns = CandlestickAnalyzer.analyze(klines)
    assert "BULLISH_ENGULFING" in patterns

def test_marubozu():
    klines = [
        [0, 100, 110, 90, 100, 100],
        [0, 100, 110, 90, 100, 100],
        [0, 100, 110, 100, 110, 100] # Open=100, Close=110, High=110, Low=100 -> No wicks
    ]
    patterns = CandlestickAnalyzer.analyze(klines)
    assert "BULLISH_MARUBOZU" in patterns
