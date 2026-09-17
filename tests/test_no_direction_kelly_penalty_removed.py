"""
Regression test for the 67th daily review.

GATE 4 / Factor 2 in ArbitrageEngine._evaluate_market() used to apply an
unconditional -0.20 Kelly-size penalty to every NO-direction trade, based on
a stale comment ("NO edges less reliable", "NO trades historically 33% WR").
YES had no unconditional equivalent (its only size penalty, Factor 3, is
conditional on 3+ green candles and only -0.10). data/3day_eval.txt (last 44
real trades) shows the opposite of the assumption baked into the removed
gate: NO 55.6% WR / +$15.40 PnL vs YES 47.1% WR / -$14.39 PnL. Systematically
shrinking NO's winning trades by 20% while leaving YES full-size let YES's
larger losses dominate net PnL despite its lower win rate.

This test asserts a NO-direction signal's confidence multiplier no longer
includes the removed "NO-dir" penalty.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import MagicMock, patch
from loguru import logger


def make_engine(min_edge=0.04):
    from strategies.arbitrage_engine import ArbitrageEngine
    eng = ArbitrageEngine(
        http_session=MagicMock(),
        binance_feed=None,
        smart_trader_tracker=None,
    )
    eng.min_edge = min_edge
    return eng


def make_market(yes_price: float, no_best_ask: float, no_best_bid: float):
    return {
        "condition_id": "cid1",
        "question": "Will bitcoin up or down in 5 minutes?",
        "best_ask": yes_price,
        "best_bid": yes_price - 0.01,
        "yes_token_id": "yes_tok",
        "no_token_id": "no_tok",
        "endDate": "2099-01-01T00:00:00Z",
        "no_best_ask": no_best_ask,
        "no_best_bid": no_best_bid,
    }


@pytest.mark.asyncio
async def test_no_direction_no_longer_size_penalized():
    """A NO signal's CONFIDENCE_MULT reasons must not include the removed
    unconditional 'NO-dir' penalty (Factor 2 / GATE 4)."""
    eng = make_engine()
    # binance_feed=None -> change_pct=0.0 -> Factor 1 (micro-move) always
    # fires, guaranteeing the CONFIDENCE_MULT log line is emitted so we can
    # inspect its reasons regardless of other (e.g. time-of-day) factors.
    market = make_market(yes_price=0.70, no_best_ask=0.35, no_best_bid=0.33)

    captured = []
    handler_id = logger.add(lambda m: captured.append(m.record["message"]), level="INFO")
    try:
        with patch.object(eng.bayesian, "estimate") as mock_est:
            mock_est.return_value = MagicMock(probability=0.44, signal_strength=0.5)
            signal = await eng._evaluate_market(
                market, capital=50.0, z_score=0.0, signal_type="bayesian"
            )
    finally:
        logger.remove(handler_id)

    assert signal is not None
    assert signal.direction == "NO"

    conf_lines = [m for m in captured if "CONFIDENCE_MULT" in m]
    assert conf_lines, "Factor 1 (micro-move) should have triggered the CONFIDENCE_MULT log"
    assert "NO-dir" not in conf_lines[0], (
        f"NO-direction penalty (Factor 2) should be removed, got: {conf_lines[0]}"
    )
    assert "micro-move" in conf_lines[0]


@pytest.mark.asyncio
async def test_no_direction_size_matches_yes_size_for_symmetric_edge():
    """With the penalty removed, a NO trade's Kelly size should equal the
    raw kelly.position_size() output reduced only by the same
    direction-agnostic factors a YES trade would face (here, just the
    Factor 1 micro-move penalty) — not further shrunk by 20% for being NO."""
    from strategies.kelly_criterion import KellyCriterion

    eng = make_engine()
    market = make_market(yes_price=0.70, no_best_ask=0.35, no_best_bid=0.33)

    with patch.object(eng.bayesian, "estimate") as mock_est:
        mock_est.return_value = MagicMock(probability=0.44, signal_strength=0.5)
        signal = await eng._evaluate_market(
            market, capital=50.0, z_score=0.0, signal_type="bayesian"
        )

    assert signal is not None
    assert signal.direction == "NO"

    raw = KellyCriterion().position_size(
        edge=signal.edge, price=signal.entry_price,
        capital=50.0, signal_strength=0.5, regime_strength=0.0,
    )
    # Only Factor 1 (micro-move, -0.15) should apply — no more -0.20 NO-dir.
    expected = raw * 0.85
    assert expected <= 4.0, "test fixture should stay below MAX_BET cap to be meaningful"
    assert expected >= 3.0, "test fixture should stay above the Kelly floor to be meaningful"
    assert signal.size == pytest.approx(expected, abs=0.01)

    # Before the fix this would have been raw * 0.65 (an extra -0.20 for NO).
    buggy_old_value = raw * 0.65
    assert signal.size != pytest.approx(buggy_old_value, abs=0.01)
