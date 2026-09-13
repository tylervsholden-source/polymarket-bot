"""
Regression guard for OPT-5's adaptive min-edge gate (CLAUDE.md kural 5).

`effective_min_edge` was computed in ArbitrageEngine._evaluate_market but never
checked against `edge` (dead code, removed alongside other gates in commit
9b5fd52 with the comment "EDGE_REJECT ... hepsi kaldırıldı"). This let any
trade with positive edge above the ~$3 Kelly floor's 0.03 skip execute,
regardless of the 0.12/0.18 (+regime/coin addons) thresholds CLAUDE.md and
docs/architecture.md's v9 OPT-5 spec describe as active. Guard against the
gate silently disappearing again.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from strategies.arbitrage_engine import ArbitrageEngine


def _make_engine() -> ArbitrageEngine:
    return ArbitrageEngine(http_session=MagicMock(), binance_feed=None, smart_trader_tracker=None)


def _market(**overrides) -> dict:
    m = {
        "condition_id": "opt5test",
        "question": "Bitcoin Up or Down - March 16, 7:10PM-7:15PM ET",
        "best_ask": "0.50",
        "best_bid": "0.48",
        "no_best_ask": "0.46",
        "no_best_bid": "0.44",
        "yes_token_id": "yes_tok_opt5",
        "no_token_id": "no_tok_opt5",
        "endDate": "2030-12-31T23:59:00Z",
        "startDate": "2026-03-16T00:00:00Z",
    }
    m.update(overrides)
    return m


def _mock_bayesian(engine: ArbitrageEngine, probability: float) -> None:
    result = MagicMock()
    result.probability = probability
    engine.bayesian.estimate = MagicMock(return_value=result)


def _mock_supporting(engine: ArbitrageEngine, kelly_size: float = 50.0, stoikov_price: float = 0.45) -> None:
    engine.kelly.position_size = MagicMock(return_value=kelly_size)
    engine.stoikov.adjusted_entry_price = MagicMock(return_value=stoikov_price)
    engine.edge_model.single_market_edge = MagicMock(return_value=0.0)
    engine.edge_model.cross_market_edge = MagicMock(return_value=0.0)
    engine.edge_model.has_edge = MagicMock(return_value=True)


@pytest.mark.asyncio
async def test_below_threshold_no_edge_is_rejected():
    """NO edge below effective_min_edge (~0.19 for BTC) must not produce a signal."""
    engine = _make_engine()
    # no_ask=0.46 caps max achievable NO edge (post PROB_CAP + costs) below the
    # ~0.19 BTC effective_min_edge threshold — this must be rejected, not floored
    # down to the much lower $3/0.03 Kelly-floor skip.
    market = _market()
    _mock_bayesian(engine, probability=0.20)
    _mock_supporting(engine)

    signal = await engine._evaluate_market(market, capital=1000.0, z_score=0.0, signal_type="bayesian")

    assert signal is None, "Edge under OPT-5's effective_min_edge must be rejected"


@pytest.mark.asyncio
async def test_above_threshold_no_edge_is_accepted():
    """NO edge comfortably above effective_min_edge must still produce a signal."""
    engine = _make_engine()
    # Lower no_ask gives enough room for the PROB_CAP-limited edge to clear
    # the ~0.19 BTC effective_min_edge threshold with margin.
    market = _market(no_best_ask="0.35", no_best_bid="0.33")
    _mock_bayesian(engine, probability=0.20)
    _mock_supporting(engine, stoikov_price=0.34)

    signal = await engine._evaluate_market(market, capital=1000.0, z_score=0.0, signal_type="bayesian")

    assert signal is not None, "Edge above OPT-5's effective_min_edge should be accepted"
    assert signal.direction == "NO"
