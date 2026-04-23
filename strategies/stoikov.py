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

    def maker_quotes(
        self,
        yes_mid: float,
        yes_inventory: float,
        no_inventory: float,
        volatility: float,
        time_remaining: float,
        yes_best_bid: float = 0.0,
        no_best_bid: float = 0.0,
    ) -> tuple[float, float, float, float]:
        """Compute passive bid prices for both YES and NO sides.

        Returns: (yes_bid, yes_size_factor, no_bid, no_size_factor)
        where size_factor 0.0-1.0 indicates allocation weight per side.

        Strategy: Place bids 1-2 cents above current best bid to get queue
        priority while staying passive (below ask). This ensures fills happen.
        Target: YES_bid + NO_bid < 0.95 for guaranteed spread profit.
        """
        # ── Practical approach: bid just above current best bid ──
        # If best_bid available, place 1c above it (but below ask)
        EDGE_ABOVE_BEST = 0.01  # 1 cent above best bid for priority

        if yes_best_bid > 0:
            yes_bid = round(yes_best_bid + EDGE_ABOVE_BEST, 2)
        else:
            yes_bid = round(max(0.01, yes_mid - 0.03), 2)

        if no_best_bid > 0:
            no_bid = round(no_best_bid + EDGE_ABOVE_BEST, 2)
        else:
            no_mid = 1.0 - yes_mid
            no_bid = round(max(0.01, no_mid - 0.03), 2)

        # ── Safety: total cost must be < 0.95 for min 5% profit margin ──
        MAX_TOTAL = 0.95
        if yes_bid + no_bid > MAX_TOTAL:
            # Scale both down proportionally
            scale = MAX_TOTAL / (yes_bid + no_bid)
            yes_bid = round(yes_bid * scale, 2)
            no_bid = round(no_bid * scale, 2)

        # ── Min price: don't bid below 0.10 (too far, won't fill) ──
        if yes_bid < 0.10:
            yes_bid = 0.0  # skip this side
        if no_bid < 0.10:
            no_bid = 0.0  # skip this side

        # ── Inventory skew: reduce size on overweight side ──
        net_inventory = yes_inventory - no_inventory
        if net_inventory > 0:
            yes_factor = max(0.2, 1.0 - net_inventory * 0.1)
            no_factor = 1.0
        elif net_inventory < 0:
            yes_factor = 1.0
            no_factor = max(0.2, 1.0 + net_inventory * 0.1)
        else:
            yes_factor = 1.0
            no_factor = 1.0

        # Skip sides with zero bid
        if yes_bid <= 0:
            yes_factor = 0.0
        if no_bid <= 0:
            no_factor = 0.0

        return yes_bid, yes_factor, no_bid, no_factor

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
        # Agresif giriş: ask'a %80 yakın (mid yerine) — dolma oranını artır
        aggressive_r = mid + (ask_price - mid) * 0.80
        r = max(r, aggressive_r)
        # Clamp to within the spread — don't cross the ask
        return round(min(ask_price, max(bid_price, r)), 4)
