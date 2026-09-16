"""
Regression test: EdgeModel.single_market_edge() must charge execution cost
for BOTH legs of the trade, not just one.

single_market_edge() models the "YES + NO < 1" within-market arbitrage:
buying the YES token AND the NO token simultaneously to lock in the $1
resolution payout. That is two separate taker/maker fills (one on the
YES book, one on the NO book), each of which incurs its own spread +
slippage cost (strategies/edge_model.py's own docstring: "buy both
sides"). The reported edge must therefore subtract cost twice.

This function's output feeds directly into live position sizing and the
edge-threshold gate in strategies/arbitrage_engine.py:

    single_edge = self.edge_model.single_market_edge(yes_price, no_price_ask)
    edge = max(trade_edge, single_edge)
    ...
    if edge < effective_min_edge:
        return None
    size = self.kelly.position_size(edge=edge, ...)

Currently single_market_edge() subtracts self.total_cost(yes_price) only
ONCE, understating the true round-trip cost by a full leg's worth
(spread_cost + slippage ≈ 0.008). This makes marginally-unprofitable
"arbitrage" look profitable and inflates the edge used for Kelly sizing.
"""
from __future__ import annotations

import pytest

from strategies.edge_model import EdgeModel


def test_single_market_edge_charges_cost_on_both_legs():
    em = EdgeModel()
    yes_price = 0.50
    no_price = 0.49  # YES + NO = 0.99 -> looks like a 1% pricing gap

    edge = em.single_market_edge(yes_price, no_price)

    one_leg_cost = em.total_cost(yes_price)
    combined = yes_price + no_price
    deviation = 1.0 - combined  # 0.01

    # Buying BOTH legs costs one_leg_cost per leg -> 2x total.
    expected_edge = deviation - 2 * one_leg_cost

    assert edge == pytest.approx(expected_edge, abs=1e-4), (
        f"single_market_edge should deduct execution cost for both the YES "
        f"leg and the NO leg of this two-sided arbitrage, not just one. "
        f"got={edge} expected={expected_edge}"
    )

    # With realistic per-leg costs (~0.008), a 1% combined pricing gap is
    # actually a LOSING trade once both fills are paid for (2 * 0.008 =
    # 0.016 > 0.01 deviation). The buggy single-cost version reports this
    # as a small positive edge, which would pass live gates and get sized
    # by Kelly as if it were a guaranteed-profit arbitrage.
    assert edge < 0, (
        f"a 1% YES+NO pricing gap does not cover round-trip cost on both "
        f"legs and must not be reported as a positive edge, got edge={edge}"
    )
