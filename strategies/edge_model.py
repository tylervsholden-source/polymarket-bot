"""
Edge model: determines if a detected dislocation has real mathematical advantage.

Two modes:
1. Single-market: YES + NO < 1  →  within-market pricing inefficiency
2. Cross-market: Bayesian prob vs market price  →  model vs market disagreement

EV_net = q - p - c
q = internal Bayesian probability
p = market price
c = dynamic_taker_fee + spread + slippage

Research finding (2026): Polymarket charges dynamic taker fees.
Near 50/50 (~3.0-3.15%), at extremes (~1.0%). This kills edge for near-50/50 markets.
Best opportunities: price 0.10-0.28 and 0.72-0.90 (lower fees, less bot competition).
"""

SPREAD_COST = 0.004   # ~0.4 cent spread cost
SLIPPAGE_EST = 0.003  # estimated slippage on limit orders


def _dynamic_taker_fee(price: float) -> float:
    """
    Polymarket dynamic taker fee based on distance from 0.50.
    Research: ~3.15% near 50/50, ~1.0% at extremes.
    """
    d = abs(price - 0.5)
    if d < 0.08:    return 0.030   # very near 50/50: 3.0%
    elif d < 0.18:  return 0.020   # moderate: 2.0%
    elif d < 0.28:  return 0.013   # further: 1.3%
    else:           return 0.008   # extremes (>0.72 or <0.28): 0.8%


class EdgeModel:
    def __init__(
        self,
        spread_cost: float = SPREAD_COST,
        slippage: float = SLIPPAGE_EST,
    ):
        self.base_cost = spread_cost + slippage

    def total_cost(self, price: float) -> float:
        return self.base_cost + _dynamic_taker_fee(price)

    def single_market_edge(self, yes_price: float, no_price: float) -> float:
        """
        Within a single binary market:
        Edge = 1 - (yes_price + no_price) - costs

        If YES + NO < 1, there's a pricing gap — buy both sides.
        """
        combined = yes_price + no_price
        # Use YES price to estimate fee (mid price approximation)
        edge = 1.0 - combined - self.total_cost(yes_price)
        return int(edge * 10000) / 10000

    def cross_market_edge(self, bayesian_prob: float, market_price: float) -> float:
        """
        Model vs market:
        EV_net = bayesian_prob - market_price - costs (dynamic)
        """
        ev_net = bayesian_prob - market_price - self.total_cost(market_price)
        return int(ev_net * 10000) / 10000

    def has_edge(self, edge: float, min_edge: float = 0.04) -> bool:
        return edge >= min_edge
