"""
Regression test (85th daily review): TradeAnalyzer's only entry point,
`Orchestrator._analyze_new_closed_trades()`, never fired in sim/paper mode —
the bot's actual default operating mode (see CLAUDE.md / docs/architecture.md;
no live control.json in this environment).

Bug: `_analyze_new_closed_trades()` computed

    closed_trades = self.position_manager.data.get("closed", [])
    new_count = len(closed_trades) - self._last_analyzed_count

unconditionally. When `_is_live_trading()` is False (the default), `_cycle()`'s
order-placement `else` branch never touches `position_manager` at all — it
records trades into `self._sim_trades` / `self._sim_results` instead (see
`_check_sim_resolutions()`). So `position_manager.data["closed"]` stays
permanently `[]` in this mode, `new_count` is never positive, and
`self.trade_analyzer.analyze_trade()` — CLAUDE.md's documented post-trade
root-cause analysis, pattern matching and adaptive-parameter-recommendation
pipeline — was silently never invoked, no matter how many sim trades closed.

This is the same root cause the 82nd/83rd/84th daily reviews fixed for
`_update_loss_streak()`, `kelly.update_streak()`, `walk_forward.validate()`,
`autonomous_engine.evaluate()`/`get_adaptive_params()` (mode-conditional
`closed` vs `self._sim_results` selection via `_current_closed_trades()`)
— but that fix was never propagated to this sibling call site.

Fix: `_analyze_new_closed_trades()` now reads `self._current_closed_trades()`
instead of the raw `position_manager.data.get("closed", [])`.
"""
from __future__ import annotations

import importlib
import inspect
from types import SimpleNamespace

import pytest

from agents.orchestrator import Orchestrator


@pytest.fixture
def _trade_analyzer_cls(tmp_path, monkeypatch):
    """TradeAnalyzer class backed by throwaway files (never touch real
    data/trade_analyses.json / data/trade_patterns.json)."""
    import agents.trade_analyzer as ta_module
    importlib.reload(ta_module)
    monkeypatch.setattr(ta_module.TradeAnalyzer, "ANALYSIS_FILE", tmp_path / "trade_analyses.json")
    monkeypatch.setattr(ta_module.TradeAnalyzer, "PATTERN_FILE", tmp_path / "trade_patterns.json")
    return ta_module.TradeAnalyzer


def _make_orchestrator(*, is_live: bool, sim_results: list, closed: list, analyzer_cls) -> Orchestrator:
    orch = Orchestrator.__new__(Orchestrator)
    orch._sim_results = sim_results
    orch.position_manager = SimpleNamespace(data={"positions": {}, "closed": closed})
    orch._is_live_trading = lambda: is_live
    orch.trade_analyzer = analyzer_cls()
    orch._last_analyzed_count = 0
    return orch


def test_sim_mode_trades_reach_trade_analyzer_via_fixed_source(_trade_analyzer_cls):
    sim_trade = {
        "market_id": "0xabc",
        "question": "Bitcoin Up or Down March 16, 9:20AM-9:25AM?",
        "direction": "NO",
        "result": "LOSS",
        "edge": 0.12,
        "confluence_score": 0.5,
        "risk_flags": [],
        "ts": 1000.0,
        "closed_at": 1300.0,
    }
    orch = _make_orchestrator(
        is_live=False, sim_results=[sim_trade], closed=[], analyzer_cls=_trade_analyzer_cls
    )

    # Sanity check: the pre-fix raw source reproduces the bug (always empty
    # in sim mode, so no new trade would ever be seen).
    assert orch.position_manager.data.get("closed", []) == []

    # Fixed source: sees the sim-mode closed trade.
    closed_trades = orch._current_closed_trades()
    assert closed_trades == [sim_trade]

    analysis = orch.trade_analyzer.analyze_trade(
        trade=sim_trade, signal_data=sim_trade, all_closed=closed_trades
    )
    assert analysis.outcome == "LOSS"
    assert orch.trade_analyzer.get_stats()["total_analyzed"] == 1


def test_analyze_new_closed_trades_uses_current_closed_trades_helper():
    src = inspect.getsource(Orchestrator._analyze_new_closed_trades)
    assert "closed_trades = self._current_closed_trades()" in src
    assert 'self.position_manager.data.get("closed", [])' not in src
