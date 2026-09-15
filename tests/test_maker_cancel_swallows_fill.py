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

Fix: when cancel_order() returns False, check the order's real status via
client.get_order_status() (the same CLOB fill-detection pattern already
used by PositionManager._check_order_filled()) before discarding it. A
genuine fill now calls on_fill(), which records it in self._inventory.
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
async def test_cancel_all_standing_true_cancel_does_not_touch_inventory():
    engine = _engine_with_standing_yes_order()

    client = MagicMock()
    client.cancel_order = MagicMock(return_value=True)
    client.get_order_status = AsyncMock()

    cancelled = await engine._cancel_all_standing(client)

    assert cancelled == 1
    assert "order-1" not in engine._standing
    assert "market-1" not in engine._inventory
    client.get_order_status.assert_not_called()
