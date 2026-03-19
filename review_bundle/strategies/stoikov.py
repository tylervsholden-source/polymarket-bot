"""
Stoikov execution model: adjusts entry price based on inventory and volatility.

Reservation price formula:
    r = s - q * gamma * sigma^2 * (T - t)

r     = adjusted quoting price (what we're willing to pay)
s     = mid price
q     = current inventory imbalance (signed: + = long, - = short)
gamma = risk aversion coefficient (higher = more conservative)
sigma = volatility of the underlying
T - t = remaining time fraction (1.0 at market open, 0.0 at close)
"""


class StoikovExecutor:
    def __init__(self, gamma: float = 0.15):
        self.gamma = gamma  # Risk aversion — higher = more price adjustment

    def reservation_price(
        self,
        mid_price: float,
        inventory: float,       # Shares already held (positive = long)
        volatility: float,      # Estimated volatility (0.01 = 1%)
        time_remaining: float,  # Fraction of market life remaining (0-1)
    ) -> float:
        """
        The more inventory we hold and the more time remains, the more
        we should discount our willingness-to-pay to manage inventory risk.
        """
        adjustment = inventory * self.gamma * (volatility ** 2) * time_remaining
        r = mid_price - adjustment
        return round(max(0.01, min(0.99, r)), 4)

    def optimal_bid_ask_spread(
        self,
        sigma: float,
        time_remaining: float,
        kappa: float = 1.5,  # Order arrival intensity
    ) -> float:
        """
        Optimal spread to quote around reservation price.
        Larger when volatility is high or lots of time remains.
        """
        import math
        term1 = self.gamma * sigma ** 2 * time_remaining
        term2 = (2.0 / self.gamma) * math.log(1.0 + self.gamma / kappa)
        return max(0.01, round(term1 + term2, 4))

    def should_fill_aggressively(
        self,
        inventory: float,
        max_inventory: float,
        threshold: float = 0.65,
    ) -> bool:
        """
        If inventory imbalance is large relative to limit, switch from
        passive limit orders to market-order-style aggressive execution.
        """
        if max_inventory <= 0:
            return False
        return abs(inventory) / max_inventory > threshold

    def adjusted_entry_price(
        self,
        ask_price: float,
        bid_price: float,
        inventory: float,
        volatility: float,
        time_remaining: float,
    ) -> float:
        """
        Convenience: compute the best limit price to use when entering a position.
        Starts from mid price, adjusts for inventory risk, stays within bid-ask.
        """
        mid = (ask_price + bid_price) / 2.0
        r = self.reservation_price(mid, inventory, volatility, time_remaining)
        # Clamp to within the spread — don't cross the ask
        return round(min(ask_price, max(bid_price, r)), 4)
