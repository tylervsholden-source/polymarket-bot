"""
MakerEngine.refresh_quotes() re-allocated the FULL caller-supplied `capital`
argument on every cycle, even when previous fills already committed real
USDC into self._inventory (populated by on_fill(), only ever cleared when
a market's inventory is resolved — see check_paired_profit()).

The `capital` argument is agents/orchestrator.py's
`position_manager.pool_available("maker")`, which is computed from
PositionManager's own `positions` dict. MakerEngine never registers a fill
there (on_fill() only touches self._inventory), so pool_locked("maker") is
always 0 and pool_available("maker") always returns the full pool total —
MakerEngine's own bookkeeping is the *only* place a fill is ever recorded.

Concrete scenario: MAKER_CAPITAL_PCT allocates a $300 pool. Cycle 1 fills
$250 of YES/NO inventory (recorded only in self._inventory). Cycle 2:
pool_available("maker") still reports $300 (unchanged), so refresh_quotes()
computed per-market allocations off the full $300 again — re-committing
capital on top of the $250 already spent, with no cap across cycles.

Fix: refresh_quotes() must subtract self._committed_inventory_cost() (the
real $ already spent on filled inventory) from the incoming `capital`
before allocating it to new quotes, mirroring what pool_available() would
report if MakerEngine's fills were visible to PositionManager.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from strategies.maker_engine import MakerEngine, MarketInventory


def _crypto_market(question: str, condition_id: str) -> dict:
    return {
        "condition_id": condition_id,
        "question": question,
        "best_ask": 0.60,
        "best_bid": 0.50,
        "no_best_ask": 0.45,
        "yes_token_id": "yes-tok",
        "no_token_id": "no-tok",
        "volume": 10_000,
    }


def _passthrough_client() -> MagicMock:
    client = MagicMock()
    client.cancel_order = MagicMock(return_value=True)
    client.get_order_status = AsyncMock(return_value={"status": "LIVE", "size_matched": "0"})
    client.get_orderbook = MagicMock(return_value={"best_ask": 0.45, "best_bid": 0.35})
    client.place_passive_order = AsyncMock(return_value=None)
    return client


def test_committed_inventory_cost_sums_filled_inventory():
    engine = MakerEngine()
    engine._inventory["market-1"] = MarketInventory(yes_shares=100, yes_cost=40.0, no_shares=50, no_cost=20.0)
    engine._inventory["market-2"] = MarketInventory(no_shares=200, no_cost=90.0)

    assert engine._committed_inventory_cost() == pytest.approx(150.0)


@pytest.mark.asyncio
async def test_refresh_quotes_deducts_filled_inventory_from_pool_capital(monkeypatch):
    engine = MakerEngine()
    # $30 already spent on a previous fill, never reported to PositionManager.
    engine._inventory["already-filled-market"] = MarketInventory(
        yes_shares=50, yes_cost=20.0, no_shares=20, no_cost=10.0,
    )

    captured_capital_per_side = []

    async def fake_quote_market(market, capital_per_side, client):
        captured_capital_per_side.append(capital_per_side)
        return None

    monkeypatch.setattr(engine, "_quote_market", fake_quote_market)

    market = _crypto_market("Bitcoin Up or Down - 4:00PM-4:05PM ET", "new-market")
    monkeypatch.setattr(engine, "_minutes_to_close", lambda q: 3)

    # Full pool is $40; $30 of it is already committed to filled inventory,
    # so only $10 should be available for this refresh's new quotes.
    await engine.refresh_quotes(markets=[market], capital=40.0, client=_passthrough_client())

    assert captured_capital_per_side, "refresh_quotes did not attempt to quote the candidate market"
    # Fixed: per_market = min(10 / 1, MAX_INVENTORY_PER_SIDE * 2 == 30) = 10
    #        -> capital_per_side = 5.
    # Buggy (capital not reduced by committed inventory): per_market =
    #        min(40 / 1, 30) = 30 -> capital_per_side = 15.
    assert captured_capital_per_side[0] == pytest.approx(5.0)
