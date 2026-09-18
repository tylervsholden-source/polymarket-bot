"""
Regression test: an order that MATCHED after an earlier partial fill must
reconcile pos["amount"] to the full size_matched, not stay frozen at the
smaller amount recorded during the earlier partial-fill poll.

Bug (core/position_manager.py::PositionManager._check_order_filled()):

    if clob_status in ("MATCHED", "FILLED"):
        pos["status"] = "MATCHED"
        # Tam dolum — amount doğru zaten
        return True

    if size_matched > 0:
        ... pos["amount"] = filled_amount ...   # only reached when NOT
                                                  # already MATCHED/FILLED

This assumed pos["amount"] was already correct whenever CLOB reports
MATCHED/FILLED — true only on the very first poll. But an earlier cycle can
see the order as still LIVE with a partial fill (e.g. 20% filled): per the
already-fixed LIVE-freeze bug, that cycle correctly shrinks pos["amount"] to
the partial size_matched*entry_price and leaves the position pollable. On a
LATER cycle, once the order finishes filling and CLOB reports MATCHED, the
old code hit the early-return branch above and NEVER re-read size_matched —
pos["amount"] stayed frozen at the earlier partial-fill snapshot forever,
even though the full order amount was actually spent.

Live impact: available_capital() (= capital - sum(position amounts))
overstates free cash for every subsequent sizing decision, and
_close_position()'s shares = amount/entry_price pays out for fewer shares
than the bot actually holds — silently understating realized P&L and
corrupting the capital ledger.

Fix: reconcile size_matched -> amount for every poll, before checking
whether to freeze the status, regardless of what clob_status says.
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
async def test_matched_after_partial_reconciles_to_full_size_matched():
    from core.position_manager import PositionManager

    # Target: 20 shares @ 0.50 = $10.00 requested.
    pos = {
        "order_id": "order-1",
        "status": "LIVE",
        "entry_price": 0.50,
        "amount": 10.0,
    }

    client = MagicMock()

    # Cycle 1: still resting, 20% filled — amount correctly shrinks to $2.00
    # and status must NOT freeze (regression already covered elsewhere, but
    # needed here to set up the stale-amount precondition).
    client.get_order_status = AsyncMock(
        return_value={"status": "live", "size_matched": "4", "original_size": "20"}
    )
    filled = await PositionManager._check_order_filled(client, pos)
    assert filled is True
    assert pos["amount"] == pytest.approx(2.0)
    assert pos["status"] != "MATCHED"

    # Cycle 2: order finished filling — CLOB now reports MATCHED with the
    # FULL size_matched (20/20). amount must reconcile up to the real $10
    # spent, not stay frozen at the $2.00 partial-fill snapshot.
    client.get_order_status = AsyncMock(
        return_value={"status": "matched", "size_matched": "20", "original_size": "20"}
    )
    filled = await PositionManager._check_order_filled(client, pos)

    assert filled is True
    assert pos["status"] == "MATCHED"
    assert pos["amount"] == pytest.approx(10.0), (
        "amount must reconcile to the real filled size on MATCHED, not stay "
        "frozen at an earlier partial-fill snapshot"
    )


@pytest.mark.asyncio
async def test_matched_first_poll_with_no_prior_partial_is_unaffected():
    """Sanity: the common case (order matches on the very first poll, no
    prior partial-fill snapshot) must keep behaving exactly as before."""
    from core.position_manager import PositionManager

    pos = {
        "order_id": "order-2",
        "status": "LIVE",
        "entry_price": 0.50,
        "amount": 10.0,
    }
    client = MagicMock()
    client.get_order_status = AsyncMock(
        return_value={"status": "matched", "size_matched": "20", "original_size": "20"}
    )

    filled = await PositionManager._check_order_filled(client, pos)

    assert filled is True
    assert pos["status"] == "MATCHED"
    assert pos["amount"] == pytest.approx(10.0)
