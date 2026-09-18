"""
Regression test (86th daily review): `Orchestrator._finalize_cycle()` — which
runs unconditionally at the end of every cycle regardless of trading mode —
wrote the dashboard status file (`core.status_writer`) with

    closed=self.position_manager.data.get("closed", [])

instead of the mode-conditional `self._current_closed_trades()` helper. Since
`_is_live_trading()` is False by default (no `LIVE_TRADING_ENABLED=true`, no
readiness clearance — the bot's documented default mode per CLAUDE.md),
`_cycle()`'s order branch never writes to `position_manager` in sim/paper
mode; closed trades land in `self._sim_results` instead (see
`_check_sim_resolutions()`). So the dashboard's closed-trades list stayed
permanently empty in the bot's actual default operating mode, no matter how
many sim trades closed — the same "wrong state source" bug family the
82nd-85th reviews fixed for `_update_loss_streak()`, `kelly.update_streak()`,
`walk_forward.validate()`, `autonomous_engine.evaluate()`/
`get_adaptive_params()`, and `_analyze_new_closed_trades()`, but it had not
been propagated to this sibling call site (nor to the sibling max-open-
positions status write, also fixed here for consistency).

Fix: both `_sw.update(..., closed=...)` call sites now use
`self._current_closed_trades()`.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agents.orchestrator import Orchestrator


def _make_orchestrator(*, is_live: bool, sim_results: list, closed: list) -> Orchestrator:
    orch = Orchestrator.__new__(Orchestrator)
    orch._sim_results = sim_results
    orch.position_manager = SimpleNamespace(
        data={"positions": {}, "closed": closed},
        update_positions=AsyncMock(return_value=None),
        available_capital=lambda: 100.0,
        open_position_count=lambda: 0,
        print_status=lambda: None,
    )
    orch._is_live_trading = lambda: is_live
    orch.client = SimpleNamespace()
    orch._cycle_count = 1
    orch.max_open_positions = 5
    orch.interval = 60
    orch._shadow_detect_asset = lambda q: "UNKNOWN"
    orch.binance_feed = SimpleNamespace(_cache={})
    orch._fetch_crypto_prices = AsyncMock(return_value=None)
    orch._reentry_guard = SimpleNamespace(mark_closed=lambda mid: None)
    return orch


@pytest.mark.asyncio
async def test_finalize_cycle_dashboard_uses_sim_results_in_sim_mode():
    """Sim/paper mode (the default): closed trades live in self._sim_results,
    not position_manager.data["closed"]. The dashboard must still see them."""
    sim_results = [{"order_id": "sim-1", "result": "LOSS", "direction": "NO"}]
    orch = _make_orchestrator(is_live=False, sim_results=sim_results, closed=[])

    with patch("agents.orchestrator._sw") as mock_sw, \
         patch("agents.orchestrator.market_watcher") as mock_watcher:
        mock_watcher._data = {}
        await orch._finalize_cycle(markets=[], candidates=[])

    _, kwargs = mock_sw.update.call_args
    assert kwargs["closed"] == sim_results


@pytest.mark.asyncio
async def test_finalize_cycle_dashboard_uses_real_closed_in_live_mode():
    closed = [{"order_id": "live-1", "result": "WIN", "direction": "YES"}]
    sim_results = [{"order_id": "stray-sim", "result": "LOSS", "direction": "NO"}]
    orch = _make_orchestrator(is_live=True, sim_results=sim_results, closed=closed)

    with patch("agents.orchestrator._sw") as mock_sw, \
         patch("agents.orchestrator.market_watcher") as mock_watcher:
        mock_watcher._data = {}
        await orch._finalize_cycle(markets=[], candidates=[])

    _, kwargs = mock_sw.update.call_args
    assert kwargs["closed"] == closed
