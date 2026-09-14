"""
Regression test: WalkForwardValidator must not classify NEUTRAL
(unfilled/refunded) closed trades as LOSS.

Bug: strategies/walk_forward.py::validate() computed train/test win rate
purely from pnl's sign:

    train_wins = sum(1 for t in train_trades if t.get("pnl", 0) > 0)
    train_wr = train_wins / len(train_trades) if train_trades else 0

completely ignoring the trade's already-correct `result` field that
core/position_manager.py sets to "WIN" / "LOSS" / "NEUTRAL" (NEUTRAL is set
whenever a GTC order never fills before the market ends and the USDC is
simply refunded — pnl=0.0, not a trading loss). Because `pnl > 0` is False
for pnl==0, every NEUTRAL close landed in the denominator without ever
landing in the numerator, diluting train_wr/test_wr exactly like a real
loss would.

This is the same bug class already fixed in agents/autonomous_engine.py's
`_update_performance()` (23rd daily review, PR #44) and
agents/trade_analyzer.py's `analyze_trade()` (25th daily review, PR #46) —
strategies/walk_forward.py was missed and had an independent, duplicated
copy of the pnl-sign logic.

Live impact: agents/orchestrator.py reads
`self._walk_forward.last_check["confidence_multiplier"]` and multiplies
`bet_size` by it directly. An artificially depressed test_wr from unfilled
orders (not real losses) can push the multiplier down to 0.60/0.70/0.80x,
or even trigger the test_wr < 0.20 -> "STOP"/0.40x branch, shrinking real
position sizes for a problem that has nothing to do with signal quality.

Fix: validate() now counts wins/losses via the `result` field
(WIN/LOSS only; NEUTRAL is excluded from both numerator and denominator),
matching strategies/kelly_criterion.py's update_streak().
"""
from strategies.walk_forward import WalkForwardValidator


def _trade(result: str, pnl: float = 0.0) -> dict:
    return {"result": result, "pnl": pnl}


def test_neutral_closes_excluded_from_win_rate():
    validator = WalkForwardValidator(train_window=4, test_window=5)

    # Train window matches the real (post-fix) test win rate (3/4 = 0.75)
    # so `gap` stays 0 and only the win-rate computation itself is exercised.
    # Test window (last 5): 3 real WINs, 1 real LOSS, 1 NEUTRAL (unfilled).
    # Real win rate among decided trades = 3/4 = 0.75.
    # Pre-fix behavior divided by all 5 (including the NEUTRAL) = 3/5 = 0.60,
    # which crosses the < 0.80 "below breakeven" HALF_SIZE threshold that the
    # fixed 0.75 does not.
    train_trades = [
        _trade("WIN", 1.0), _trade("WIN", 1.0), _trade("WIN", 1.0), _trade("LOSS", -1.0),
    ]
    test_trades = [
        _trade("WIN", 2.0),
        _trade("WIN", 2.0),
        _trade("WIN", 2.0),
        _trade("LOSS", -3.0),
        _trade("NEUTRAL", 0.0),
    ]

    result = validator.validate(train_trades + test_trades)

    assert result["test_wr"] == 0.75
    assert result["gap"] == 0.0
    assert result["recommendation"] == "FULL_SIZE"
    assert result["confidence_multiplier"] == 1.0


def test_all_neutral_test_window_does_not_force_stop():
    validator = WalkForwardValidator(train_window=2, test_window=3)

    # No decided trades at all in the test window (max(0, 1) guards the
    # division) -- must not be misread as a 0% win rate / STOP.
    train_trades = [_trade("WIN", 1.0), _trade("LOSS", -1.0)]
    test_trades = [_trade("NEUTRAL", 0.0)] * 3

    result = validator.validate(train_trades + test_trades)

    assert result["test_wr"] == 0.0
    # Still correctly flagged as noise-level (no real signal in this
    # window) rather than crashing or silently full-sizing on zero data.
    assert result["recommendation"] == "STOP"


def test_backward_compatible_without_result_field_is_no_longer_supported_as_win():
    """Trades lacking a `result` field (e.g. malformed legacy data) count
    as neither WIN nor LOSS rather than being misread via pnl sign."""
    validator = WalkForwardValidator(train_window=1, test_window=1)

    train_trades = [{"pnl": 5.0}]
    test_trades = [{"pnl": 5.0}]

    result = validator.validate(train_trades + test_trades)

    assert result["train_wr"] == 0.0
    assert result["test_wr"] == 0.0
