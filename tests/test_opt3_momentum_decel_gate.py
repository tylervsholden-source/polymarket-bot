"""
Regression guard for OPT-3's Momentum Deceleration Guard (CLAUDE.md v9,
"Tümü Aktif" — "Son 3 mumda |change| azalıyorsa -> bounce riski -> NO block").

`momentum_decelerating` was computed in full in
ArbitrageEngine._evaluate_market (candle-change comparison plus a
volatility-based fallback) but never read afterward — the gate itself was
deleted in commit 9b5fd52 with the comment "MOMENTUM_DECEL kaldırıldı".
CLAUDE.md still documents OPT-3 as active, so any NO trade went through
regardless of deceleration. Guard against the gate silently disappearing
again.
"""
from strategies.arbitrage_engine import _momentum_decel_blocks_no


def test_decelerating_no_trade_is_blocked():
    assert _momentum_decel_blocks_no("NO", True) is True


def test_decelerating_yes_trade_is_not_blocked():
    assert _momentum_decel_blocks_no("YES", True) is False


def test_non_decelerating_no_trade_is_not_blocked():
    assert _momentum_decel_blocks_no("NO", False) is False


def test_non_decelerating_yes_trade_is_not_blocked():
    assert _momentum_decel_blocks_no("YES", False) is False
