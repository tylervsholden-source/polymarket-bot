"""
Regression test: `_no_viable`'s regime-adaptive spot-direction safeguard was
computed but never applied.

strategies/arbitrage_engine.py._evaluate_market() computes
`_spot_bearish_for_no = change_pct < _NO_SPOT_THRESHOLD` right after a block
explicitly titled "NO safeguard thresholds (regime-adaptive)" that derives
`_NO_SPOT_THRESHOLD` from the current regime — its entire purpose is to stop
the bot from taking a NO trade while the live 5-minute spot price is still
rising (which directly contradicts the "MOMENTUM-FIRST" strategy documented
at the top of this module: "Spot UP + Bayesian > 0.50 -> YES. Spot DOWN +
Bayesian < 0.50 -> NO. Celiski varsa -> trade etme").

But `_no_viable`'s definition never references `_spot_bearish_for_no` — it
only checks no_edge/no_price_ask/no_price_source/no_side_health. So a NO
trade can be (and, per this test, is) selected even while spot is clearly
rising well above the computed threshold, exactly the scenario the guard
exists to block. Same bug class as the already-fixed `momentum_decelerating`
gate (see tests/test_opt3_momentum_decel_gate.py) and HANGING_MAN
co-emission (64th daily review): a safeguard computed in full and then
silently never read.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from strategies.arbitrage_engine import ArbitrageEngine
from strategies.bayesian import BayesianEstimate


def _make_market() -> dict:
    return {
        "condition_id": "cond_spot_guard",
        "question": "Bitcoin Up or Down - March 16, 7:10PM-7:15PM ET",
        "best_ask": "0.45",
        "best_bid": "0.43",
        "no_best_ask": "0.40",
        "no_best_bid": "0.38",
        "yes_token_id": "yes_tok_sg",
        "no_token_id": "no_tok_sg",
        "endDate": "2030-12-31T23:59:00Z",
    }


def _rising_spot() -> dict:
    """5-minute spot is rising strongly (+0.50%) — no candle patterns, no
    bounce/exhaustion signals, so only the base `_no_viable` computation
    (and the spot-direction safeguard it should include) is in play."""
    return {
        "change_pct": 0.50,
        "trend_pct": 0.0,
        "macro_trend_pct": 0.0,
        "volatility": 0.5,
        "ob_imbalance": 0.0,
        "rsi": 55.0,
        "volume_ratio": 1.5,
        "macd_hist": 0.0,
        "patterns": [],
        "momentum_decelerating": False,
        "consecutive_bearish": 0,
        "consecutive_bullish": 0,
        "bounce_signal": False,
        "pre_bounce_streak": 0,
        "bullish_exhaustion": False,
        "bullish_exhaustion_magnitude": 0.0,
        "bb_pos": 0.5,
        "bb_width": 0.02,
        "bb_squeeze": False,
        "bb_breakout": 0.0,
        "ema_cross": 0.0,
        "atr_pct": 0.3,
        "stoch_k": 50.0,
        "sr_position": 0.5,
        "vwap_dev": 0.0,
        "ichi_signal": 0.0,
        "ichi_tk_cross": 0.0,
        "fib_level": 0.5,
        "adx": 25.0,
        "adx_plus": 0.0,
        "adx_minus": 0.0,
        "obv_slope": 0.0,
        "cmf": 0.0,
        "tech_score": 0.0,
    }


def _make_engine_with_rising_feed() -> ArbitrageEngine:
    feed = MagicMock()
    feed.has_data.return_value = True
    feed.get_signal.return_value = _rising_spot()
    feed.get_recent_change.return_value = 0.0
    engine = ArbitrageEngine(http_session=None, binance_feed=feed, smart_trader_tracker=None)
    engine._current_regime = {
        "regime": "NEUTRAL", "strength": 0.0,
        "btc_5m_pct": 0.0, "eth_5m_pct": 0.0,
        "btc_4h_pct": 0.0, "eth_4h_pct": 0.0,
    }
    return engine


def test_no_direction_not_selected_while_spot_is_rising():
    """NEUTRAL regime -> _NO_SPOT_THRESHOLD=0.05. change_pct=+0.50% is far
    above that threshold (spot is rising), so the regime-adaptive safeguard
    should keep NO non-viable. YES stays non-viable on its own (edge is
    negative at this price), so no signal should be produced at all.
    """
    engine = _make_engine_with_rising_feed()

    # bayesian_prob=0.20 -> after 5m dampening + PROB_CAP floor (0.35) and
    # the 5% NEUTRAL pull, adjusted_bayesian ~= 0.36. yes_price=0.45 keeps
    # yes_edge negative; no_price_ask=0.40 keeps no_edge comfortably
    # positive and above OPT-5's effective_min_edge -- isolating the
    # missing spot-direction guard as the only thing that should block NO.
    mock_estimate = BayesianEstimate(
        probability=0.20, prior=0.30, signal_strength=0.3, direction="DOWN"
    )
    engine.kelly.position_size = MagicMock(return_value=50.0)
    engine.edge_model.single_market_edge = MagicMock(return_value=0.0)
    engine.edge_model.has_edge = MagicMock(return_value=True)

    market = _make_market()
    with patch.object(engine.bayesian, "estimate", return_value=mock_estimate):
        signal = asyncio.run(
            engine._evaluate_market(market, capital=500.0, z_score=0.0, signal_type="bayesian")
        )

    diag = engine.get_last_diagnostics().get("cond_spot_guard")
    assert diag is not None
    assert signal is None, (
        f"NO was selected (direction={getattr(signal, 'direction', None)!r}) while "
        f"spot was rising +0.50% against a NEUTRAL-regime threshold of 0.05% -- "
        f"the regime-adaptive spot-direction safeguard (_spot_bearish_for_no) was "
        f"computed but never wired into _no_viable."
    )
    assert diag.selected_direction == "NONE"


def test_no_direction_is_selected_once_spot_actually_supports_it():
    """Sanity check: with the same setup but change_pct comfortably below
    the NEUTRAL threshold (spot falling), NO should be selected normally --
    confirms the fix doesn't just block NO unconditionally."""
    engine = _make_engine_with_rising_feed()
    engine.binance_feed.get_signal.return_value = {
        **_rising_spot(),
        "change_pct": -0.50,  # spot falling, well below the 0.05% threshold
    }

    mock_estimate = BayesianEstimate(
        probability=0.20, prior=0.30, signal_strength=0.3, direction="DOWN"
    )
    engine.kelly.position_size = MagicMock(return_value=50.0)
    engine.edge_model.single_market_edge = MagicMock(return_value=0.0)
    engine.edge_model.has_edge = MagicMock(return_value=True)

    market = _make_market()
    with patch.object(engine.bayesian, "estimate", return_value=mock_estimate):
        signal = asyncio.run(
            engine._evaluate_market(market, capital=500.0, z_score=0.0, signal_type="bayesian")
        )

    assert signal is not None
    assert signal.direction == "NO"
