"""
Regression test (85th daily review): `Orchestrator._analyze_new_closed_trades()`
read `self.position_manager.data.get("closed", [])` directly instead of the
mode-conditional `self._current_closed_trades()` helper, so TradeAnalyzer
(root-cause analysis, pattern matching, adaptive recommendations — the
CLAUDE.md-documented "post-trade analiz" stage of the live cycle) never ran
in sim/paper mode.

Bug: `_is_live_trading()` is False by default (no `LIVE_TRADING_ENABLED=true`,
no readiness clearance). In that mode `_cycle()`'s order branch never writes
to `position_manager` — closed trades land in `self._sim_results` instead
(see `_check_sim_resolutions()`). `_analyze_new_closed_trades()` computed

    closed_trades = self.position_manager.data.get("closed", [])
    new_count = len(closed_trades) - self._last_analyzed_count

which was `0 - 0 = 0` every single cycle no matter how many trades piled up
in `self._sim_results`, so it returned early and `self.trade_analyzer.
analyze_trade()` was never invoked — this is the same "wrong state source"
bug family the 82nd/83rd/84th reviews fixed for `_update_loss_streak()`,
`kelly.update_streak()`, `walk_forward.validate()` and
`autonomous_engine.evaluate()`/`get_adaptive_params()`, but it had not been
propagated to this sibling call site.

Fix: `_analyze_new_closed_trades()` now calls `self._current_closed_trades()`
(mode-conditional: `position_manager.data["closed"]` when live,
`self._sim_results` otherwise), matching every other consumer of closed-trade
history in the orchestrator.
"""
from __future__ import annotations

from types import SimpleNamespace

from agents.orchestrator import Orchestrator


class _FakeAnalysis:
    def __init__(self, order_id: str):
        self.order_id = order_id
        self.lessons: list[str] = []

    def summary(self) -> str:
        return f"analysis:{self.order_id}"


class _FakeTradeAnalyzer:
    def __init__(self):
        self.calls: list[dict] = []

    def analyze_trade(self, trade, signal_data=None, all_closed=None):
        self.calls.append({"trade": trade, "all_closed": all_closed})
        return _FakeAnalysis(trade.get("order_id", ""))

    def get_pattern_report(self):
        return {}

    def get_recommendations(self):
        return []


def _make_orchestrator(*, is_live: bool, sim_results: list, closed: list) -> Orchestrator:
    orch = Orchestrator.__new__(Orchestrator)
    orch._sim_results = sim_results
    orch.position_manager = SimpleNamespace(data={"positions": {}, "closed": closed})
    orch._is_live_trading = lambda: is_live
    orch._last_analyzed_count = 0
    orch.trade_analyzer = _FakeTradeAnalyzer()
    return orch


def test_sim_mode_new_closed_trades_are_analyzed():
    """Sim/paper mode (the default): trades live in self._sim_results, not
    position_manager.data["closed"]. TradeAnalyzer must still see them."""
    sim_results = [
        {"order_id": "sim-1", "result": "LOSS", "direction": "NO"},
        {"order_id": "sim-2", "result": "WIN", "direction": "NO"},
    ]
    orch = _make_orchestrator(is_live=False, sim_results=sim_results, closed=[])

    orch._analyze_new_closed_trades()

    analyzed_ids = [c["trade"]["order_id"] for c in orch.trade_analyzer.calls]
    assert analyzed_ids == ["sim-1", "sim-2"]
    assert orch._last_analyzed_count == 2


def test_live_mode_still_reads_real_closed_and_ignores_stray_sim_results():
    closed = [{"order_id": "live-1", "result": "WIN", "direction": "YES"}]
    sim_results = [{"order_id": "stray-sim", "result": "LOSS", "direction": "NO"}]
    orch = _make_orchestrator(is_live=True, sim_results=sim_results, closed=closed)

    orch._analyze_new_closed_trades()

    analyzed_ids = [c["trade"]["order_id"] for c in orch.trade_analyzer.calls]
    assert analyzed_ids == ["live-1"]
    assert orch._last_analyzed_count == 1


def test_sim_mode_no_new_trades_is_a_noop():
    orch = _make_orchestrator(is_live=False, sim_results=[], closed=[])
    orch._analyze_new_closed_trades()
    assert orch.trade_analyzer.calls == []
    assert orch._last_analyzed_count == 0
