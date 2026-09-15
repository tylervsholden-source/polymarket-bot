"""
Regression test: MonteCarloSimulator.simulate() must model a win's payout using
the same b = (1/price) - 1 ratio as strategies/kelly_criterion.py's
KellyCriterion.position_size(), not a flat `net_edge * 2.0` multiplier.

Bug (found in the 49th daily review): simulate() computed win payout as
`bet * net_edge * 2.0`, capping the payout to a small multiple of the edge
itself instead of the real prediction-market payout (bet/price on a win).
For every realistic live edge (0.05-0.30) this made mean_return deeply
negative, so `viable` was False unconditionally — silently halting all
directional trading if ArbitrageEngine.analyze() had started consuming the
gate's return value (it doesn't yet; see arbitrage_engine.py's MC_SHADOW
logging, which observes without blocking).
"""
import random

import strategies.monte_carlo as mc_mod
from strategies.monte_carlo import MonteCarloSimulator


def test_single_winning_trade_pays_out_kelly_b(monkeypatch):
    # Force a fully deterministic single winning trade: no edge noise, no
    # slippage, full fill, and a guaranteed win (random.random() == 0.0 is
    # always < win_prob).
    monkeypatch.setattr(mc_mod.random, "gauss", lambda mu, sigma: 0.0)
    monkeypatch.setattr(mc_mod.random, "uniform", lambda a, b: b)
    monkeypatch.setattr(mc_mod.random, "random", lambda: 0.0)

    mc = MonteCarloSimulator(n_simulations=1)
    price = 0.40
    capital = 1000.0
    result = mc.simulate(
        edge=0.30,
        capital=capital,
        price=price,
        n_trades=1,
        position_size_pct=0.20,
    )

    b = (1.0 / price) - 1.0
    bet = capital * 0.20 * 1.0  # actual_fill forced to 1.0 above
    expected_win_return = round((bet * b) / capital, 4)

    assert result.win_rate == 1.0
    assert result.mean_return == expected_win_return


def test_realistic_edge_no_longer_always_nonviable():
    """The pre-fix formula made viable=False for every edge in 0.05-0.30 at
    any price. Post-fix, a favorable low-priced, high-edge setup should show
    a strongly positive mean_return (the old formula produced a deeply
    negative one for the same inputs)."""
    random.seed(1)
    mc = MonteCarloSimulator(n_simulations=500)
    result = mc.simulate(edge=0.20, capital=1000.0, price=0.40, position_size_pct=0.20)
    assert result.mean_return > 0
