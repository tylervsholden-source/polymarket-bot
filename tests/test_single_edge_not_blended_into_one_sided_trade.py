"""
Regression guard for the 61st daily review.

`ArbitrageEngine._evaluate_market` computed:

    edge = max(trade_edge, single_edge)

`single_edge` (`EdgeModel.single_market_edge`) is only a real, riskless edge
when BOTH the YES and NO tokens are bought (its own docstring: "If YES + NO
< 1 ... buy both sides"). The engine only ever places one order for
`direction` — there is no two-sided execution path — so blending
`single_edge` into `edge` fed an edge that was never actually available to
the one-sided trade straight into Kelly sizing (`kelly.position_size(edge=...)`)
and the min-edge gate, oversizing (or wrongly enabling) trades. Guard that
`edge` — and therefore what's passed to Kelly and stored on the signal —
tracks only `trade_edge`, regardless of how large `single_edge` is.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from strategies.arbitrage_engine import ArbitrageEngine


def _make_engine() -> ArbitrageEngine:
    return ArbitrageEngine(http_session=MagicMock(), binance_feed=None, smart_trader_tracker=None)


def _market(**overrides) -> dict:
    m = {
        "condition_id": "singleedgetest",
        "question": "Bitcoin Up or Down - March 16, 7:10PM-7:15PM ET",
        "best_ask": "0.50",
        "best_bid": "0.48",
        "no_best_ask": "0.35",
        "no_best_bid": "0.33",
        "yes_token_id": "yes_tok_se",
        "no_token_id": "no_tok_se",
        "endDate": "2030-12-31T23:59:00Z",
        "startDate": "2026-03-16T00:00:00Z",
    }
    m.update(overrides)
    return m


def _mock_bayesian(engine: ArbitrageEngine, probability: float) -> None:
    result = MagicMock()
    result.probability = probability
    engine.bayesian.estimate = MagicMock(return_value=result)


def _mock_supporting(engine: ArbitrageEngine, single_edge: float) -> None:
    engine.kelly.position_size = MagicMock(return_value=50.0)
    engine.stoikov.adjusted_entry_price = MagicMock(return_value=0.34)
    engine.edge_model.single_market_edge = MagicMock(return_value=single_edge)
    engine.edge_model.cross_market_edge = MagicMock(return_value=0.0)
    engine.edge_model.has_edge = MagicMock(return_value=True)


@pytest.mark.asyncio
async def test_inflated_single_edge_does_not_change_signal_edge_or_sizing():
    """A large single_market_edge must not leak into the one-sided trade's edge/Kelly sizing."""
    market = _market()

    baseline_engine = _make_engine()
    _mock_bayesian(baseline_engine, probability=0.20)
    _mock_supporting(baseline_engine, single_edge=0.0)
    baseline_signal = await baseline_engine._evaluate_market(
        market, capital=1000.0, z_score=0.0, signal_type="bayesian"
    )

    inflated_engine = _make_engine()
    _mock_bayesian(inflated_engine, probability=0.20)
    _mock_supporting(inflated_engine, single_edge=0.90)
    inflated_signal = await inflated_engine._evaluate_market(
        market, capital=1000.0, z_score=0.0, signal_type="bayesian"
    )

    assert baseline_signal is not None and inflated_signal is not None
    assert inflated_signal.edge == pytest.approx(baseline_signal.edge), (
        "single_edge leaked into the signal's edge for a one-sided trade"
    )

    baseline_kelly_edge = baseline_engine.kelly.position_size.call_args.kwargs["edge"]
    inflated_kelly_edge = inflated_engine.kelly.position_size.call_args.kwargs["edge"]
    assert inflated_kelly_edge == pytest.approx(baseline_kelly_edge), (
        "single_edge leaked into the Kelly sizing edge for a one-sided trade"
    )
    assert inflated_kelly_edge < 0.90, "Kelly edge must not be inflated to the two-sided single_edge value"
