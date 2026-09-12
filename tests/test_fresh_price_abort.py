"""
Regression test for FRESH_PRICE_ABORT on the NO side.

Bug: PolymarketClient.get_market() (Gamma API) only normalizes YES-side
best_ask/best_bid — it never sets `no_best_ask`. The old inline check read
`_fresh.get("no_best_ask", 0)` from get_market()'s result, which was always
0, so the stale-price slippage guard silently never fired for NO orders
(while it worked correctly for YES). Fix: for NO direction, fetch the NO
token's own orderbook via get_orderbook(), same source used during market
scanning.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock

import pytest

from agents.orchestrator import Orchestrator


def _make_orchestrator(client: MagicMock) -> Orchestrator:
    orch = Orchestrator.__new__(Orchestrator)
    orch.client = client
    return orch


def _signal(direction: str, entry_price: float, edge: float) -> SimpleNamespace:
    return SimpleNamespace(direction=direction, entry_price=entry_price, edge=edge)


@pytest.mark.asyncio
async def test_get_market_never_provides_no_best_ask():
    """Locks in the root cause: Gamma-normalized get_market() has no NO-side price."""
    from core.polymarket_client import PolymarketClient

    client = PolymarketClient.__new__(PolymarketClient)
    client.session = MagicMock()
    client.session.get = AsyncMock(
        return_value=MagicMock(
            raise_for_status=lambda: None,
            json=lambda: {"conditionId": "mkt-1", "bestAsk": "0.50", "bestBid": "0.48"},
        )
    )
    fresh = await client.get_market("mkt-1")
    assert "no_best_ask" not in fresh


@pytest.mark.asyncio
async def test_no_direction_uses_orderbook_not_dead_get_market_field():
    """NO orders must pull their fresh ask from get_orderbook(no_token_id)."""
    client = MagicMock()
    client.get_market = AsyncMock(return_value={"best_ask": 0.50})  # no no_best_ask, ever
    client.get_orderbook = MagicMock(return_value={"best_ask": 0.90})  # moved far from entry

    orch = _make_orchestrator(client)
    market = {"question": "Test market", "no_token_id": "NO-tok-1"}
    signal = _signal("NO", entry_price=0.40, edge=0.20)  # slippage 0.50 > edge*0.6=0.12

    ok = await orch._fresh_price_ok(market, "mkt-1", signal)

    assert ok is False
    client.get_orderbook.assert_called_once_with("NO-tok-1")
    client.get_market.assert_not_called()


@pytest.mark.asyncio
async def test_no_direction_passes_when_price_stable():
    client = MagicMock()
    client.get_market = AsyncMock(return_value={"best_ask": 0.50})
    client.get_orderbook = MagicMock(return_value={"best_ask": 0.41})  # small move

    orch = _make_orchestrator(client)
    market = {"question": "Test market", "no_token_id": "NO-tok-1"}
    signal = _signal("NO", entry_price=0.40, edge=0.20)  # slippage 0.01 <= edge*0.6=0.12

    ok = await orch._fresh_price_ok(market, "mkt-1", signal)

    assert ok is True


@pytest.mark.asyncio
async def test_yes_direction_still_uses_get_market():
    client = MagicMock()
    client.get_market = AsyncMock(return_value={"best_ask": 0.90})  # moved far from entry
    client.get_orderbook = MagicMock(return_value={"best_ask": 0.10})

    orch = _make_orchestrator(client)
    market = {"question": "Test market", "yes_token_id": "YES-tok-1"}
    signal = _signal("YES", entry_price=0.40, edge=0.20)  # slippage 0.50 > edge*0.6=0.12

    ok = await orch._fresh_price_ok(market, "mkt-1", signal)

    assert ok is False
    client.get_market.assert_called_once_with("mkt-1")
    client.get_orderbook.assert_not_called()
