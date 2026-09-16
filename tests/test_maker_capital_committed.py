"""
Regression test (56th daily review): pool_available("maker") only reads
PositionManager's shared ledger. Maker fills never enter that ledger —
MakerEngine.on_fill() only ever updates self._inventory in memory — so
`maker_capital = self.position_manager.pool_available("maker")` in
Orchestrator._cycle() never shrank as real USDC was committed to standing
orders or filled-but-unresolved inventory. Every cycle, refresh_quotes()
was handed the full pool again and could commit new real capital on top of
whatever was already outstanding from the previous cycle, so cumulative
real maker exposure was unbounded instead of capped at the configured
maker pool.

Fix: MakerEngine.get_committed_capital() reports standing-order notional
plus filled-but-unresolved inventory cost, and Orchestrator._cycle()
subtracts it from pool_available("maker") before calling refresh_quotes().
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import agents.orchestrator as orchestrator_module
from agents.orchestrator import Orchestrator
from strategies.maker_engine import MakerEngine, MarketInventory, StandingOrder


def test_get_committed_capital_includes_standing_orders():
    engine = MakerEngine()
    engine._standing["order-1"] = StandingOrder(
        order_id="order-1", market_id="m1", token_id="yes-1",
        side="YES", price=0.40, size=10.0, placed_at=0.0,
    )
    assert engine.get_committed_capital() == pytest.approx(4.0)


def test_get_committed_capital_includes_unresolved_filled_inventory():
    """A filled order stops being a standing order but its cost is real
    USDC still at risk until the market resolves — it must still count."""
    engine = MakerEngine()
    engine._inventory["m1"] = MarketInventory(yes_shares=10.0, yes_cost=4.0,
                                               no_shares=8.0, no_cost=3.2)
    assert engine.get_committed_capital() == pytest.approx(7.2)


def test_get_committed_capital_sums_standing_and_filled():
    engine = MakerEngine()
    engine._standing["order-1"] = StandingOrder(
        order_id="order-1", market_id="m1", token_id="no-1",
        side="NO", price=0.30, size=5.0, placed_at=0.0,
    )
    engine._inventory["m2"] = MarketInventory(yes_shares=10.0, yes_cost=4.0)
    assert engine.get_committed_capital() == pytest.approx(1.5 + 4.0)


def _make_orchestrator_for_maker_capital(committed_capital: float) -> Orchestrator:
    orch = Orchestrator.__new__(Orchestrator)

    orch._is_live_trading = lambda: True
    orch._is_simulation_running = lambda: True
    orch._read_control = lambda: {"min_bet": 1.0}

    orch._cycle_count = 0
    orch._order_timestamps = []
    orch._consecutive_losses = 0
    orch._max_consecutive_losses = 3
    orch._consecutive_wins_per_coin = {}
    orch._last_loss_slots = set()

    orch.position_manager = SimpleNamespace(
        data={"capital": 100.0, "closed": [], "positions": {}},
        update_positions=AsyncMock(),
        open_position_count=lambda: 0,
        available_capital=lambda: 100.0,
        locked_capital=lambda: 0.0,
        pool_position_count=lambda pool: 0,
        pool_available=lambda pool: 100.0,
        daily_loss_exceeded=lambda threshold: False,
    )
    orch.max_open_positions = 5
    orch.min_market_volume = 5000.0
    orch.daily_stop_loss = 0.15
    orch.interval = 60
    orch._process_lock = SimpleNamespace(is_mine=lambda: True)

    fake_market = {"condition_id": "maker-capital-test-market", "question": "Bitcoin Up or Down"}
    orch.client = SimpleNamespace(
        get_active_markets=AsyncMock(return_value=[fake_market]),
        get_orderbook=lambda token_id: None,
    )
    orch._pre_filter = lambda markets: list(markets)

    fake_coord_result = SimpleNamespace(
        signal_result=SimpleNamespace(signals=[]),
        approved_signals=[],
        summary=lambda: "no signals",
    )
    orch.coordinator = SimpleNamespace(run_cycle=AsyncMock(return_value=fake_coord_result))

    orch._record_shadow_decisions = lambda *a, **k: None
    orch._sim_trades = []

    orch.arb_engine = SimpleNamespace(kelly=SimpleNamespace(update_streak=lambda closed: None))
    orch._walk_forward = SimpleNamespace(validate=lambda closed: {"recommendation": "OK", "test_wr": 0.0})

    orch.latency_arb = SimpleNamespace(_running=False)

    orch._maker_enabled = True
    orch._maker_engine = SimpleNamespace(
        refresh_quotes=AsyncMock(return_value={"placed": 0, "cancelled": 0, "skipped": 0}),
        get_committed_capital=lambda: committed_capital,
    )
    orch._bond_enabled = False
    orch._bond_scanner = None

    orch._finalize_cycle = AsyncMock()

    return orch


@pytest.mark.asyncio
async def test_maker_capital_excludes_already_committed_real_capital():
    """pool is $100, but $35 of it is already outstanding on real standing
    orders / unresolved fills — refresh_quotes() must only be handed the
    remaining $65, not the full pool again."""
    orch = _make_orchestrator_for_maker_capital(committed_capital=35.0)

    with patch.object(orchestrator_module, "_get_approved_orders", return_value=[]), \
         patch.object(orchestrator_module, "_cleanup_expired_orders"):
        await orch._cycle()

    orch._maker_engine.refresh_quotes.assert_called_once()
    _, kwargs = orch._maker_engine.refresh_quotes.call_args
    assert kwargs["capital"] == pytest.approx(65.0)


@pytest.mark.asyncio
async def test_maker_capital_never_goes_negative_when_overcommitted():
    """If committed capital ever exceeds the pool (e.g. capital shrank after
    a loss elsewhere), refresh_quotes() must get $0, not a negative number."""
    orch = _make_orchestrator_for_maker_capital(committed_capital=150.0)

    with patch.object(orchestrator_module, "_get_approved_orders", return_value=[]), \
         patch.object(orchestrator_module, "_cleanup_expired_orders"):
        await orch._cycle()

    orch._maker_engine.refresh_quotes.assert_called_once()
    _, kwargs = orch._maker_engine.refresh_quotes.call_args
    assert kwargs["capital"] == pytest.approx(0.0)
