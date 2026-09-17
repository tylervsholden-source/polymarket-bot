"""
MakerEngine._cancel_all_standing() unconditionally deleted every standing
order from self._standing every cycle, regardless of whether
client.cancel_order() actually cancelled it — cancel_order() returns False
both for a real error AND for the far more common "order already matched
by the CLOB, nothing left to cancel" case, and the old code treated both
identically: `del self._standing[order_id]` either way.

That silently dropped the fill. MakerEngine.on_fill() — which is the only
thing that updates self._inventory (and therefore check_paired_profit()) —
was never called, so a standing order that actually filled with real USDC
vanished from MakerEngine's own bookkeeping with zero trace.

Fix (47th daily review): when cancel_order() returns False, check the
order's real status via client.get_order_status() before discarding it.

That fix only covered the False branch. cancel_order() == True just means
the order's remaining open quantity was pulled from the book — a GTC order
that partially filled before this cycle's refresh cancels cleanly (True),
so a successful cancel is the COMMON case for a partial fill, not evidence
there wasn't one. The 47th-review code still unconditionally deleted the
order with zero trace whenever cancel_order() returned True, silently
dropping the exact same class of real, spent-USDC fill for that branch.
Fixed here: both branches now check the order's real status via
client.get_order_status() before discarding, and any size_matched > 0 is
recorded via on_fill() regardless of how the cancel call itself resolved.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from strategies.maker_engine import MakerEngine, StandingOrder


def _engine_with_standing_yes_order() -> MakerEngine:
    engine = MakerEngine()
    engine._standing["order-1"] = StandingOrder(
        order_id="order-1",
        market_id="market-1",
        token_id="yes-token-1",
        side="YES",
        price=0.40,
        size=10.0,
        placed_at=1_700_000_000.0,
    )
    return engine


@pytest.mark.asyncio
async def test_cancel_all_standing_records_fill_instead_of_dropping_it():
    engine = _engine_with_standing_yes_order()

    client = MagicMock()
    # CLOB can't cancel it — it already matched, not a real cancel error.
    client.cancel_order = MagicMock(return_value=False)
    client.get_order_status = AsyncMock(
        return_value={"status": "MATCHED", "size_matched": "10.0"}
    )

    cancelled = await engine._cancel_all_standing(client)

    assert cancelled == 0
    # The order must be gone from _standing either way (cancelled or filled)...
    assert "order-1" not in engine._standing
    # ...but a real fill must be reflected in inventory, not silently dropped.
    inv = engine._inventory.get("market-1")
    assert inv is not None, "fill was discarded without updating inventory"
    assert inv.yes_shares == pytest.approx(10.0)
    assert inv.yes_cost == pytest.approx(4.0)


@pytest.mark.asyncio
async def test_cancel_all_standing_drops_order_that_is_still_live():
    """A cancel_order() False that is a real "still live, nothing to fill or
    cancel" case (e.g. transient network error) must not be recorded as a
    phantom fill — inventory stays untouched."""
    engine = _engine_with_standing_yes_order()

    client = MagicMock()
    client.cancel_order = MagicMock(return_value=False)
    client.get_order_status = AsyncMock(return_value={"status": "LIVE", "size_matched": "0"})

    cancelled = await engine._cancel_all_standing(client)

    assert cancelled == 0
    assert "order-1" not in engine._standing
    assert "market-1" not in engine._inventory


@pytest.mark.asyncio
async def test_cancel_all_standing_true_cancel_with_no_fill_does_not_touch_inventory():
    """cancel_order() == True and the order never matched at all (status
    stays CANCELED, size_matched=0) — no fill to record."""
    engine = _engine_with_standing_yes_order()

    client = MagicMock()
    client.cancel_order = MagicMock(return_value=True)
    client.get_order_status = AsyncMock(
        return_value={"status": "CANCELED", "size_matched": "0"}
    )

    cancelled = await engine._cancel_all_standing(client)

    assert cancelled == 1
    assert "order-1" not in engine._standing
    assert "market-1" not in engine._inventory


@pytest.mark.asyncio
async def test_cancel_all_standing_true_cancel_of_partial_fill_records_fill():
    """BUG: cancel_order() returning True only means the order's remaining
    open quantity was pulled from the book — the normal, successful outcome
    for cancelling a GTC order that partially filled before this cycle's
    refresh. The old code treated a successful cancel as proof there was
    nothing to record and deleted the order with zero trace of the real
    USDC already spent on the filled portion (10 of 25 shares here)."""
    engine = MakerEngine()
    engine._standing["order-2"] = StandingOrder(
        order_id="order-2",
        market_id="market-2",
        token_id="yes-token-2",
        side="YES",
        price=0.40,
        size=25.0,
        placed_at=1_700_000_000.0,
    )

    client = MagicMock()
    # CLOB cancels the remaining 15 shares cleanly — no exception, no error.
    client.cancel_order = MagicMock(return_value=True)
    client.get_order_status = AsyncMock(
        return_value={"status": "CANCELED", "size_matched": "10.0"}
    )

    cancelled = await engine._cancel_all_standing(client)

    assert cancelled == 1
    assert "order-2" not in engine._standing
    inv = engine._inventory.get("market-2")
    assert inv is not None, "partial fill was discarded on a successful cancel"
    assert inv.yes_shares == pytest.approx(10.0)
    assert inv.yes_cost == pytest.approx(4.0)
