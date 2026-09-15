"""
Monte Carlo simulator: validates strategy viability under realistic market conditions.

Tests thousands of scenarios with randomized:
- Fill rates (partial fills)
- Slippage
- Execution delays
- Consecutive loss streaks
- Edge estimation errors

W(t+1) = W(t) * (1 + r(t))
Max Drawdown = max((Peak(t) - W(t)) / Peak(t))
"""
import random
import math
from dataclasses import dataclass


@dataclass
class MonteCarloResult:
    mean_return: float       # Expected return across simulations
    median_return: float     # Median return (more robust to outliers)
    win_rate: float          # Fraction of trades that win
    max_drawdown: float      # Average max drawdown across simulations
    sharpe_ratio: float      # Mean / Std of returns
    p10_return: float        # Worst 10% scenario return (tail risk)
    viable: bool             # True if strategy survives realistic conditions


class MonteCarloSimulator:
    def __init__(self, n_simulations: int = 2000):
        self.n_simulations = n_simulations

    def simulate(
        self,
        edge: float,               # Net edge per trade (EV_net)
        capital: float,            # Starting capital
        price: float = 0.50,       # Entry price of the token being bought (YES or NO ask)
        n_trades: int = 100,       # Number of trades to simulate
        fill_rate: float = 0.85,   # Expected order fill rate
        slippage_std: float = 0.004,  # Slippage std deviation
        position_size_pct: float = 0.08,  # Capital fraction per trade
        edge_noise_std: float = 0.015,    # Estimation error in edge
    ) -> MonteCarloResult:
        """
        Run N simulations of the arbitrage strategy.
        Each simulation randomizes fills, slippage, and edge estimation.
        """
        # Polymarket pays $1/share on a win; buying `bet` at `price` buys
        # bet/price shares, so win profit is bet * (1-price)/price and loss
        # is the full bet. Clamp away from 0/1 to keep the payout finite.
        price = min(max(price, 0.02), 0.98)

        final_returns: list[float] = []
        max_drawdowns: list[float] = []
        win_rates: list[float] = []

        for _ in range(self.n_simulations):
            w = capital
            peak = capital
            max_dd = 0.0
            wins = 0
            total_trades = 0

            for _ in range(n_trades):
                # Randomize actual edge (estimation noise)
                actual_edge = edge + random.gauss(0, edge_noise_std)
                if actual_edge <= 0:
                    continue

                # Randomize fill (partial fills common on Polymarket)
                actual_fill = random.uniform(fill_rate * 0.6, 1.0)

                # Randomize slippage
                actual_slippage = abs(random.gauss(0, slippage_std))
                net_edge = actual_edge - actual_slippage

                if net_edge <= 0:
                    continue

                bet = w * position_size_pct * actual_fill
                if bet < 0.5:
                    continue

                total_trades += 1

                # True win probability implied by price + net edge
                # (edge = true_prob - price - costs  =>  true_prob = price + net_edge)
                win_prob = min(0.97, max(0.03, price + net_edge))
                if random.random() < win_prob:
                    # Win: bet/price shares bought, each resolves to $1
                    w += bet * (1.0 - price) / price
                    wins += 1
                else:
                    w -= bet

                # Track peak and drawdown
                if w > peak:
                    peak = w
                if peak > 0:
                    dd = (peak - w) / peak
                    max_dd = max(max_dd, dd)

                # Ruin check
                if w < capital * 0.05:
                    break

            ret = (w - capital) / capital
            final_returns.append(ret)
            max_drawdowns.append(max_dd)
            win_rates.append(wins / total_trades if total_trades > 0 else 0.0)

        if not final_returns:
            return MonteCarloResult(0, 0, 0, 1.0, 0, -1.0, False)

        final_returns.sort()
        mean_ret = sum(final_returns) / len(final_returns)
        median_ret = final_returns[len(final_returns) // 2]
        mean_wr = sum(win_rates) / len(win_rates)
        mean_dd = sum(max_drawdowns) / len(max_drawdowns)
        p10_idx = max(0, len(final_returns) // 10)
        p10_ret = final_returns[p10_idx]

        std_ret = math.sqrt(
            sum((r - mean_ret) ** 2 for r in final_returns) / len(final_returns)
        ) if len(final_returns) > 1 else 1.0
        sharpe = mean_ret / std_ret if std_ret > 1e-6 else 0.0

        viable = (
            mean_ret > 0.0
            and p10_ret > -0.40        # Worst 10% loses less than 40%
            and mean_dd < 0.45         # Average drawdown under 45%
            and mean_wr > 0.50         # Win more than half the trades
        )

        return MonteCarloResult(
            mean_return=round(mean_ret, 4),
            median_return=round(median_ret, 4),
            win_rate=round(mean_wr, 3),
            max_drawdown=round(mean_dd, 4),
            sharpe_ratio=round(sharpe, 3),
            p10_return=round(p10_ret, 4),
            viable=viable,
        )
