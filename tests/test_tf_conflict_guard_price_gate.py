"""
Regression test (56th daily review): the 5m-vs-4h "TF_CONFLICT" guard in
ArbitrageEngine._evaluate_market could force a direction on without the
price/data-quality gate every other activation path in the same function
enforces.

strategies/arbitrage_engine.py has several places that turn a direction
"viable" mid-function (CANDLE_YES_ACTIVATE, EXHAUSTION_NO_ACTIVATE,
CANDLE_NO_ACTIVATE, ...). Every one of them re-checks the same bounds the
base viability computation uses:

    YES: yes_price <= _YES_MAX_PRICE (0.47) and yes_price >= _YES_MIN_PRICE
    NO:  no_price_source == NoPriceSource.REAL_BOOK and no_side_health == "OK"

except the TF_CONFLICT_FLIP_YES / TF_CONFLICT_FLIP_NO block ("5m vs 4h
CONFLICT GUARD"), which used to set `_yes_viable = True` / `_no_viable =
True` unconditionally whenever 5m and 4h regime data disagreed strongly —
no later gate re-imposes the YES price ceiling (the "MAX YES PRICE CAP"
gate was removed by design in v3; GATE 2 only blocks entries below 0.20,
never above the 0.47 ceiling), and no later gate re-checks the NO side's
price source/health either.

Net effect: a 5m/4h conflict could flip YES on at a price the engine's own
research says is bad risk/reward ("YES@0.61: win=$0.39, lose=$0.61"), or
flip NO on against a SYNTHETIC/stale-book price instead of a real one.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from strategies.arbitrage_engine import ArbitrageEngine
from strategies.bayesian import BayesianEstimate


def _make_market(
    condition_id: str,
    best_ask: str,
    best_bid: str,
    no_best_ask=None,
    no_best_bid=None,
) -> dict:
    return {
        "condition_id": condition_id,
        "question": "Bitcoin Up or Down - March 16, 7:00PM-7:05PM ET",
        "best_ask": best_ask,
        "best_bid": best_bid,
        "no_best_ask": no_best_ask,
        "no_best_bid": no_best_bid,
        "yes_token_id": "yes_tok_tf",
        "no_token_id": "no_tok_tf",
        "endDate": "2030-12-31T23:59:00Z",
    }


def _neutral_spot() -> dict:
    """A 'nothing else is going on' spot snapshot — no candle patterns,
    no bounce, no exhaustion — so the TF_CONFLICT block is the only thing
    that can turn a direction on."""
    return {
        "change_pct": 0.20,
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


def _make_engine_with_feed() -> ArbitrageEngine:
    feed = MagicMock()
    feed.has_data.return_value = True
    feed.get_signal.return_value = _neutral_spot()
    feed.get_recent_change.return_value = 0.0
    engine = ArbitrageEngine(http_session=None, binance_feed=feed, smart_trader_tracker=None)
    return engine


def test_tf_conflict_flip_yes_respects_price_ceiling():
    """5m bullish / 4h bearish conflict must not flip YES on above _YES_MAX_PRICE."""
    engine = _make_engine_with_feed()
    engine._current_regime = {
        "regime": "NEUTRAL", "strength": 0.0,
        "btc_5m_pct": 0.20, "eth_5m_pct": 0.20,   # avg_5m = +0.20 > 0.10
        "btc_4h_pct": -0.50, "eth_4h_pct": -0.50,  # avg_4h = -0.50 < -0.30
    }

    # yes_price=0.70 is above _YES_MAX_PRICE (0.47) — base viability already
    # rejects it on price. bayesian_prob=0.85 keeps yes_edge positive so the
    # only thing keeping YES off is the price gate.
    market = _make_market("cond_tf_yes", best_ask="0.70", best_bid="0.68")
    mock_estimate = BayesianEstimate(
        probability=0.85, prior=0.70, signal_strength=0.3, direction="UP"
    )
    with patch.object(engine.bayesian, "estimate", return_value=mock_estimate):
        result = asyncio.run(
            engine._evaluate_market(market, capital=500.0, z_score=0.0, signal_type="bayesian")
        )

    diag = engine.get_last_diagnostics().get("cond_tf_yes")
    assert diag is not None
    assert diag.selected_direction == "NONE", (
        "TF_CONFLICT_FLIP_YES flipped YES on above the 0.47 price ceiling — "
        "the same gate every other YES-activation path in this function enforces."
    )
    assert result is None


def test_tf_conflict_flip_no_respects_real_book_gate():
    """5m bearish / 4h bullish conflict must not flip NO on against a
    SYNTHETIC (not a real orderbook) price."""
    engine = _make_engine_with_feed()
    engine._current_regime = {
        "regime": "NEUTRAL", "strength": 0.0,
        "btc_5m_pct": -0.20, "eth_5m_pct": -0.20,  # avg_5m = -0.20 < -0.10
        "btc_4h_pct": 0.50, "eth_4h_pct": 0.50,    # avg_4h = +0.50 > 0.30
    }

    # No real NO book (no_best_ask=None) -> NO price is SYNTHETIC.
    # bayesian_prob=0.10 keeps yes_edge negative (YES stays non-viable on its
    # own) while the synthetic no_edge is comfortably positive, isolating the
    # NO-side data-quality gate as the only thing that should block NO.
    market = _make_market("cond_tf_no", best_ask="0.30", best_bid="0.28")
    mock_estimate = BayesianEstimate(
        probability=0.10, prior=0.30, signal_strength=0.3, direction="DOWN"
    )
    with patch.object(engine.bayesian, "estimate", return_value=mock_estimate):
        result = asyncio.run(
            engine._evaluate_market(market, capital=500.0, z_score=0.0, signal_type="bayesian")
        )

    diag = engine.get_last_diagnostics().get("cond_tf_no")
    assert diag is not None
    assert diag.no_price_source == "SYNTHETIC"
    assert diag.selected_direction == "NONE", (
        "TF_CONFLICT_FLIP_NO flipped NO on against a SYNTHETIC price — the "
        "same REAL_BOOK/health gate every other NO-activation path in this "
        "function enforces."
    )
    assert result is None
