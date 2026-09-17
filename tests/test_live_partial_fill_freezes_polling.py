"""
Regression test: a still-open (LIVE) order with a partial fill must NOT be
permanently marked "MATCHED" — doing so freezes the position forever and
silently misses every later fill the resting order picks up.

Bug (core/position_manager.py::PositionManager._check_order_filled()):

    if size_matched > 0:
        ...
        pos["amount"] = filled_amount   # correctly updated THIS cycle
        pos["status"] = "MATCHED"       # <-- BUG: frozen even though
        return True                     #     clob_status was still "LIVE"

The function's own docstring says it returns "True if order was
matched/filled, False if still live" — but the code set status="MATCHED"
for a merely partially-filled order that the CLOB itself still reports as
LIVE (resting, matchable) just because size_matched > 0. Since the top of
the function short-circuits on `status in ("MATCHED", "FILLED")` and
returns True WITHOUT ever calling client.get_order_status() again, this
position is never polled again for the rest of its life.

Concretely: agents/orchestrator.py's _bond_cycle() places a passive GTC
limit order (fire-and-forget, no wait) and records it with
status="LIVE" (order_result.get("status", "LIVE")). Say a $10 order for 20
shares @ 0.50 rests on the book. Cycle N: CLOB reports
status="live", size_matched=4 (20% filled, 16 shares still resting).
The old code computed amount=$2.00 then froze status="MATCHED". Cycle
N+1: the order fills another 10 shares (now 14/20, 70%, still real money
being spent) — but update_positions() never calls get_order_status() again
for this position, so pos["amount"] stays wrong at $2.00 forever even
though ~$7 of real USDC has actually left the wallet. available_capital()
and the eventual close_position() P&L (shares = amount/entry_price) both
use this permanently-stale basis.

Fix: only freeze status="MATCHED" when the CLOB itself reports the order
as done (not "LIVE"). A partial fill on an order still resting on the book
updates amount for this cycle but leaves the position pollable next cycle.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture(autouse=True)
def tmp_data_dir(tmp_path, monkeypatch):
    import core.position_manager as pm_module
    monkeypatch.setattr(pm_module, "DATA_FILE", tmp_path / "positions.json")
    monkeypatch.setattr(pm_module, "LOCK_FILE", tmp_path / "positions.lock")


@pytest.mark.asyncio
async def test_live_order_partial_fill_does_not_freeze_status():
    from core.position_manager import PositionManager

    pos = {
        "order_id": "order-1",
        "status": "LIVE",
        "entry_price": 0.50,
        "amount": 10.0,  # target: 20 shares @ 0.50
    }

    client = MagicMock()
    client.get_order_status = AsyncMock(
        return_value={"status": "live", "size_matched": "4", "original_size": "20"}
    )

    filled = await PositionManager._check_order_filled(client, pos)

    assert filled is True
    # Real filled amount this cycle is reflected (4/20 = 20% of $10).
    assert pos["amount"] == pytest.approx(2.0)
    # Order is still resting on the book — must NOT be frozen as MATCHED,
    # or update_positions() will never call get_order_status() for it again.
    assert pos["status"] != "MATCHED"


@pytest.mark.asyncio
async def test_live_partial_fill_keeps_getting_polled_and_updates_further():
    from core.position_manager import PositionManager

    pos = {
        "order_id": "order-1",
        "status": "LIVE",
        "entry_price": 0.50,
        "amount": 10.0,
    }

    client = MagicMock()

    # Cycle 1: 20% filled.
    client.get_order_status = AsyncMock(
        return_value={"status": "live", "size_matched": "4", "original_size": "20"}
    )
    await PositionManager._check_order_filled(client, pos)
    assert pos["amount"] == pytest.approx(2.0)

    # Cycle 2: order picked up more fills (70% total) while still resting.
    client.get_order_status = AsyncMock(
        return_value={"status": "live", "size_matched": "14", "original_size": "20"}
    )
    filled = await PositionManager._check_order_filled(client, pos)

    assert filled is True
    # Must reflect the NEW real filled amount, not stay frozen at $2.00.
    assert pos["amount"] == pytest.approx(7.0)


@pytest.mark.asyncio
async def test_cancelled_order_with_partial_fill_is_frozen_as_matched():
    """Once the CLOB confirms the order is actually done (not LIVE), no
    further fills are possible — freezing status="MATCHED" here is correct
    and intentional, unlike the still-LIVE case above."""
    from core.position_manager import PositionManager

    pos = {
        "order_id": "order-1",
        "status": "LIVE",
        "entry_price": 0.50,
        "amount": 10.0,
    }

    client = MagicMock()
    client.get_order_status = AsyncMock(
        return_value={"status": "cancelled", "size_matched": "4", "original_size": "20"}
    )

    filled = await PositionManager._check_order_filled(client, pos)

    assert filled is True
    assert pos["amount"] == pytest.approx(2.0)
    assert pos["status"] == "MATCHED"
