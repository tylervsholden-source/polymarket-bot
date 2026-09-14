"""
Regression test: an accepted GTC partial fill must charge the position for
the shares actually filled, not the originally requested order size.

Bug (core/polymarket_client.py::place_order()):

While polling a still-open GTC order, the code accepts anything at or above
95% filled as fully "matched" (to avoid waiting out edge-case dust left on
the book):

    if cur_status == "matched" or size_matched >= size * 0.95:
        status = "matched"
        filled = True
        break
    ...
    actual_amount = round(size * price, 4)   # <-- uses the TARGET size,
                                              #     not size_matched

So a 95-99% partial fill was reported back to the orchestrator/position
manager as if the FULL target size had filled. Concretely: target size=9.61
shares @ price=0.52 but the book only actually filled size_matched=9.40
shares (97.8% — comfortably over the 0.95 acceptance threshold). The old
code returned amount = round(9.61 * 0.52, 4) = $5.00 even though only
9.40 * 0.52 = $4.89 of USDC actually left the wallet.

Live impact:
  - agents/orchestrator.py does `capital -= order.get("amount", bet_size)`
    right after place_order() returns, so the in-cycle capital ledger is
    drained by phantom spend that was never actually sent to the CLOB.
  - core/position_manager.add_position() stores this inflated `amount` as
    the position's cost basis; since shares = amount / entry_price, the bot
    believes it holds more shares than it actually owns on-chain, silently
    overstating unrealized/realized P&L on every close.
  - PositionManager._check_order_filled() short-circuits on
    `status in ("MATCHED", "FILLED")` (already set by place_order), so this
    inflated amount is NEVER corrected later — the position-level partial-
    fill reconciliation code path is dead for orders place_order already
    marked "matched".

Fix: track the real `size_matched` value when a partial fill is accepted,
and use THAT (not the target `size`) to compute `actual_amount`.
"""
from __future__ import annotations

import builtins

import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture(autouse=True)
def _no_real_control_json(monkeypatch):
    """Force place_order's price_bump read to fall back to its 0.02 default,
    regardless of whatever data/control.json happens to contain on disk,
    so the size/price math in this test is deterministic."""
    real_open = builtins.open

    def fake_open(path, *args, **kwargs):
        if str(path).endswith("control.json"):
            raise FileNotFoundError("blocked for test determinism")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    import asyncio
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())


@pytest.fixture(autouse=True)
def _tmp_journal(monkeypatch, tmp_path):
    import core.polymarket_client as pc_mod
    monkeypatch.setattr(pc_mod, "TRADE_JOURNAL_FILE", tmp_path / "journal.jsonl")


@pytest.mark.asyncio
async def test_partial_fill_amount_reflects_size_matched_not_target_size():
    from core.polymarket_client import PolymarketClient

    client = PolymarketClient.__new__(PolymarketClient)
    client.session = MagicMock()
    client._order_dedup = {}

    clob = MagicMock()
    clob.create_order = MagicMock(return_value="SIGNED_ORDER")
    # Order does not fill instantly — goes to the polling loop.
    clob.post_order = MagicMock(return_value={"orderID": "order-1", "status": "live"})
    # Poll reports a 97.8% fill (9.40 / 9.61) — over the 0.95 acceptance
    # threshold, so it gets treated as "matched", but it is NOT 100%.
    clob.get_order = MagicMock(
        return_value={"status": "live", "size_matched": "9.40"}
    )
    client._clob = clob

    order = await client.place_order(
        market_id="mkt-1",
        outcome="YES",
        amount=5.0,
        price=0.50,
        token_id="tok-1",
        question="Bitcoin Up or Down",
    )

    assert order is not None
    assert order["status"] == "matched"
    # Target size = floor(5.0 / 0.52 * 100) / 100 = 9.61 shares @ price 0.52
    # (0.02 default price bump on a $0.50 entry).
    # Real fill = 9.40 shares -> real cost = 9.40 * 0.52 = $4.888
    assert order["price"] == pytest.approx(0.52)
    assert order["amount"] == pytest.approx(9.40 * 0.52, abs=1e-4)
    # Locks in the bug: the full-target-size cost must NOT be charged.
    assert order["amount"] != pytest.approx(9.61 * 0.52, abs=1e-4)
