"""
Edge model: determines if a detected dislocation has real mathematical advantage.

Two modes:
1. Single-market: YES + NO < 1  →  within-market pricing inefficiency
2. Cross-market: Bayesian prob vs market price  →  model vs market disagreement

EV_net = q - p - c
q = internal Bayesian probability
p = market price
c = dynamic_taker_fee + spread + slippage

Polymarket fee formula: fee = price * (1-price) * fee_rate
FOK orders are taker orders. fee_rate ≈ 2%.
Max fee at price 0.50: 0.50*0.50*0.02 = 0.5%
At extremes (0.10/0.90): 0.10*0.90*0.02 = 0.18%
"""

SPREAD_COST = 0.005   # GTC (maker) orders → price bump +0.01 for fill priority
SLIPPAGE_EST = 0.005  # $4 orders on thin books → conservative estimate

# Polymarket GTC orders are MAKER orders → fee is 0% on most markets
# Only taker orders (FOK) pay 2% fee. We use GTC.
_MAKER_FEE_RATE = 0.00  # maker fee = 0%
_TAKER_FEE_RATE = 0.02  # kept for reference only


def _dynamic_taker_fee(price: float) -> float:
    """
    Polymarket fee for GTC (maker) orders = 0%.
    Only FOK (taker) orders pay 2%. We use GTC exclusively.

    Returns 0 for maker orders. Kept as function for future flexibility.
    """
    # GTC = maker order → no fee
    return 0.0


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
