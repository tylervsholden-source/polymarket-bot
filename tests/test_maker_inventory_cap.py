"""
MakerEngine.MAX_INVENTORY_PER_SIDE is documented as a per-market, per-side
ceiling on *held* inventory ("max $ per side per market"), but
_quote_market() only ever used it to size THIS cycle's fresh order budget
(refresh_quotes()'s per_market = min(capital/len(candidates),
MAX_INVENTORY_PER_SIDE * 2)) — it never checked it against
inv.yes_cost / inv.no_cost, the dollars already filled and held from
earlier cycles.

A market that keeps getting re-selected across consecutive ~60-120s
orchestrator cycles (a 15m window easily spans several) therefore got a
brand-new real CLOB order placed on top of already-filled inventory every
single cycle, so real held inventory could grow far past the stated $15
per-side limit instead of being capped by it.

Fix: _quote_market() now computes yes_room/no_room = MAX_INVENTORY_PER_SIDE
minus already-held inv.yes_cost/inv.no_cost, skips a side once it is at (or
over) the cap, and clamps the new order's dollar size to whatever room
remains.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from strategies.maker_engine import MakerEngine, MarketInventory


def _market() -> dict:
    return {
        "condition_id": "market-1",
        "question": "Bitcoin Up or Down - 4:30PM-4:45PM ET",
        "yes_token_id": "yes-token-1",
        "no_token_id": "no-token-1",
        "best_ask": 0.55,
        "best_bid": 0.50,
    }


def _client_no_orderbook_no_best_bid(no_best_bid: float = 0.40) -> MagicMock:
    client = MagicMock()
    client.get_orderbook = MagicMock(return_value={"best_ask": 0.0, "best_bid": no_best_bid})
    client.place_passive_order = AsyncMock(
        side_effect=lambda **kwargs: {"order_id": f"order-{kwargs['token_id']}"}
    )
    return client


@pytest.mark.asyncio
async def test_yes_side_already_at_cap_is_not_re_quoted():
    """inv.yes_cost is already at MAX_INVENTORY_PER_SIDE ($15) from earlier
    fills — a new YES order must NOT be placed on top of it, even though
    capital_per_side/yes_factor alone would size a >=5-share order."""
    engine = MakerEngine()
    engine._inventory["market-1"] = MarketInventory(
        yes_shares=0.0, no_shares=0.0, yes_cost=15.0, no_cost=0.0,
    )
    client = _client_no_orderbook_no_best_bid()

    placed = await engine._quote_market(_market(), capital_per_side=10.0, client=client)

    # Only the NO side (still has full $15 of room) should have been quoted.
    assert placed == 1
    called_tokens = [c.kwargs["token_id"] for c in client.place_passive_order.call_args_list]
    assert "yes-token-1" not in called_tokens
    assert "no-token-1" in called_tokens


@pytest.mark.asyncio
async def test_no_side_already_at_cap_is_not_re_quoted():
    engine = MakerEngine()
    engine._inventory["market-1"] = MarketInventory(
        yes_shares=0.0, no_shares=0.0, yes_cost=0.0, no_cost=15.0,
    )
    client = _client_no_orderbook_no_best_bid()

    placed = await engine._quote_market(_market(), capital_per_side=10.0, client=client)

    assert placed == 1
    called_tokens = [c.kwargs["token_id"] for c in client.place_passive_order.call_args_list]
    assert "no-token-1" not in called_tokens
    assert "yes-token-1" in called_tokens


@pytest.mark.asyncio
async def test_fresh_market_with_no_inventory_quotes_both_sides():
    """Sanity check: with no prior inventory, both sides still get quoted
    normally (the cap fix must not block the ordinary case)."""
    engine = MakerEngine()
    client = _client_no_orderbook_no_best_bid()

    placed = await engine._quote_market(_market(), capital_per_side=10.0, client=client)

    assert placed == 2
    called_tokens = {c.kwargs["token_id"] for c in client.place_passive_order.call_args_list}
    assert called_tokens == {"yes-token-1", "no-token-1"}


@pytest.mark.asyncio
async def test_partial_room_clamps_order_size_instead_of_full_budget():
    """inv.yes_cost has $10 of the $15 cap already used — the new YES order
    must be sized to the remaining $5 of room, not the full capital_per_side
    budget (which would push held inventory past the cap)."""
    engine = MakerEngine()
    engine._inventory["market-1"] = MarketInventory(
        yes_shares=0.0, no_shares=0.0, yes_cost=10.0, no_cost=0.0,
    )
    client = _client_no_orderbook_no_best_bid()

    placed = await engine._quote_market(_market(), capital_per_side=10.0, client=client)

    assert placed == 2
    yes_call = next(
        c for c in client.place_passive_order.call_args_list
        if c.kwargs["token_id"] == "yes-token-1"
    )
    # yes_bid_price is 0.51 in this fixture (see StoikovExecutor.maker_quotes
    # with yes_best_bid=0.50) — $5 of remaining room / 0.51 ≈ 9.80 shares,
    # well under the ~19.6 shares a full $10 budget would have produced.
    assert yes_call.kwargs["size"] < 15.0
    assert yes_call.kwargs["price"] * yes_call.kwargs["size"] <= 5.0 + 1e-6
