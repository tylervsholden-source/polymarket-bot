"""
Regression test: the Regime Decay Guard (v8, CLAUDE.md: "COIN_LIMIT=2 + decay
guard eklendi") is computed every cycle in ArbitrageEngine.analyze() but was
never consulted anywhere, so it never actually blocked a NO trade.

analyze() tracks `self._regime_strength_peak` and sets
`self._regime_decay_pause = True` (with a "NO trade'ler 1 cycle DURDU" log)
whenever regime strength decays >= 0.20 from a peak >= 0.40 -- the exact
dead-cat-bounce window CLAUDE.md's "Kritik Kesifler" section documents
("2+ ardisik NO-win periyottan sonra %100 bounce geliyor"). But
`_evaluate_market()`'s `_no_viable` computation and every later
reactivation branch (3GREEN_NO_ACTIVATE, EXHAUSTION_NO_ACTIVATE,
CANDLE_NO_ACTIVATE, TF_CONFLICT_FLIP_NO) never referenced
`self._regime_decay_pause`, so a NO trade could still be placed during the
exact window the guard's own log claims it paused. Same "computed but never
read" bug class as `momentum_decelerating` (OPT-3, see
tests/test_opt3_momentum_decel_gate.py) and `_spot_bearish_for_no` (see
tests/test_no_viable_missing_spot_guard.py).
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from strategies.arbitrage_engine import ArbitrageEngine
from strategies.bayesian import BayesianEstimate


def _make_market() -> dict:
    return {
        "condition_id": "cond_decay_guard",
        "question": "Bitcoin Up or Down - March 16, 7:10PM-7:15PM ET",
        "best_ask": "0.45",
        "best_bid": "0.43",
        "no_best_ask": "0.40",
        "no_best_bid": "0.38",
        "yes_token_id": "yes_tok_dg",
        "no_token_id": "no_tok_dg",
        "endDate": "2030-12-31T23:59:00Z",
    }


def _falling_spot() -> dict:
    """Spot is falling (-0.50%) with no candle patterns / bounce / exhaustion
    signals in play, so only the base `_no_viable` computation (and the
    decay guard, once wired) decide the outcome."""
    return {
        "change_pct": -0.50,
        "trend_pct": 0.0,
        "macro_trend_pct": 0.0,
        "volatility": 0.5,
        "ob_imbalance": 0.0,
        "rsi": 45.0,
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


def _make_engine_with_falling_feed() -> ArbitrageEngine:
    feed = MagicMock()
    feed.has_data.return_value = True
    feed.get_signal.return_value = _falling_spot()
    feed.get_recent_change.return_value = 0.0
    engine = ArbitrageEngine(http_session=None, binance_feed=feed, smart_trader_tracker=None)
    engine._current_regime = {
        "regime": "BEARISH", "strength": 0.0,
        "btc_5m_pct": 0.0, "eth_5m_pct": 0.0,
        "btc_4h_pct": 0.0, "eth_4h_pct": 0.0,
    }
    return engine


def _run(engine: ArbitrageEngine, mock_estimate: BayesianEstimate, market: dict):
    engine.kelly.position_size = MagicMock(return_value=50.0)
    engine.edge_model.single_market_edge = MagicMock(return_value=0.0)
    engine.edge_model.has_edge = MagicMock(return_value=True)
    with patch.object(engine.bayesian, "estimate", return_value=mock_estimate):
        return asyncio.run(
            engine._evaluate_market(market, capital=500.0, z_score=0.0, signal_type="bayesian")
        )


def test_no_direction_blocked_while_regime_decay_pause_active():
    """Falling spot would normally select NO (see
    test_no_viable_missing_spot_guard's sanity-check case), but with
    `_regime_decay_pause=True` -- the state analyze() sets after a fast
    regime-strength drop from a peak -- NO must stay blocked."""
    engine = _make_engine_with_falling_feed()
    engine._regime_strength_peak = 0.85
    engine._regime_decay_pause = True

    mock_estimate = BayesianEstimate(
        probability=0.20, prior=0.30, signal_strength=0.3, direction="DOWN"
    )
    signal = _run(engine, mock_estimate, _make_market())

    assert signal is None, (
        f"NO was selected (direction={getattr(signal, 'direction', None)!r}) while "
        f"self._regime_decay_pause was True -- the regime decay guard is computed "
        f"every cycle but was never wired into _no_viable."
    )
    diag = engine.get_last_diagnostics().get("cond_decay_guard")
    assert diag is not None
    assert diag.selected_direction == "NONE"


def test_no_direction_is_selected_once_decay_pause_is_not_active():
    """Sanity check: identical setup but `_regime_decay_pause=False` -- NO
    should be selected normally, confirming the fix doesn't block NO
    unconditionally."""
    engine = _make_engine_with_falling_feed()
    engine._regime_strength_peak = 0.0
    engine._regime_decay_pause = False

    mock_estimate = BayesianEstimate(
        probability=0.20, prior=0.30, signal_strength=0.3, direction="DOWN"
    )
    signal = _run(engine, mock_estimate, _make_market())

    assert signal is not None
    assert signal.direction == "NO"
