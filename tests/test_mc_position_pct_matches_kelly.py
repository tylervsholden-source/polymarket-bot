"""
Regression test: ArbitrageEngine._maybe_run_monte_carlo() must simulate trades
using the same MAX_POSITION_PCT default as the live sizing pipeline
(strategies/kelly_criterion.py, core/position_manager.py both default to 0.20).

Bug: the Monte Carlo call passed `os.getenv("MAX_POSITION_PCT", 0.10)` — a
different fallback (0.10) than the 0.20 used everywhere else the same env var
is read. With MAX_POSITION_PCT unset (the default), Monte Carlo therefore
modeled trades at half the real position size, understating both simulated
win rate and drawdown relative to what the live bot actually risks per trade.
"""
from strategies.arbitrage_engine import ArbitrageEngine
from strategies.kelly_criterion import KellyCriterion


def test_mc_call_uses_same_default_as_kelly(monkeypatch, tmp_path):
    monkeypatch.delenv("MAX_POSITION_PCT", raising=False)
    import core.position_manager as pm_mod
    monkeypatch.setattr(pm_mod, "DATA_FILE", tmp_path / "positions.json")

    engine = ArbitrageEngine()
    captured = {}

    def _fake_simulate(**kwargs):
        captured.update(kwargs)
        from strategies.monte_carlo import MonteCarloResult
        return MonteCarloResult(0, 0, 0, 0, 0, 0, True)

    engine.mc.simulate = _fake_simulate
    engine._maybe_run_monte_carlo(edge=0.20, price=0.50, capital=1000.0)

    kelly_default = KellyCriterion().max_position_pct
    pm_default = pm_mod.PositionManager().max_position_pct

    assert kelly_default == pm_default == 0.20
    assert captured["position_size_pct"] == kelly_default
