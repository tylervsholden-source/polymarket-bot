"""
Regression test: a GTC order that times out (never reaches the 95%
in-loop acceptance threshold) but had ALREADY partially matched before
being cancelled must still be recorded as a position — not silently
dropped.

Bug (core/polymarket_client.py::place_order()):

When the 45s poll loop exits without a >=95% fill, the code cancels the
order and re-checks its status once:

    verify = self._clob.get_order(order_id)
    v_status = verify.get("status", "") if verify else ""
    if v_status == "matched":
        status = "matched"; filled = True
    elif v_status in ("cancelled", "expired", ""):
        logger.info(...)          # <-- size_matched never inspected here
    ...
    if not filled:
        return None                # position is NEVER recorded

Cancelling a GTC order only cancels the unfilled remainder — any shares
matched before cancellation already spent real USDC and are irreversible.
Concretely: target size=9.61 shares @ price=0.52 (~$5.00), the book only
ever matches 4.00 shares (41.6%, under the 0.95 acceptance threshold) over
the full 45s, then times out and gets cancelled. The post-cancel verify
call reports {"status": "cancelled", "size_matched": "4.00"}. The old code
matches the `elif v_status in ("cancelled", ...)` branch, never looks at
size_matched, and place_order() returns None.

Live impact: agents/orchestrator.py treats a None return as "no order
placed" and never calls position_manager.add_position() — but ~$2.08 of
real USDC was already spent on 4 real YES shares that now sit untracked
in the wallet: never monitored for resolution, never counted in win/loss
stats, never redeemed, and the loss is silently absorbed as unexplained
balance drift by the next _sync_real_balance() cycle instead of being
attributed to a real position.

Fix: the post-cancel verify step must also check size_matched; if it is
> 0, accept it as a real partial fill (same treatment as the in-loop
>=95% case) instead of discarding it.
"""
from __future__ import annotations

import builtins

import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture(autouse=True)
def _no_real_control_json(monkeypatch):
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
async def test_partial_fill_before_timeout_cancel_is_not_discarded():
    from core.polymarket_client import PolymarketClient

    client = PolymarketClient.__new__(PolymarketClient)
    client.session = MagicMock()
    client._order_dedup = {}

    clob = MagicMock()
    clob.create_order = MagicMock(return_value="SIGNED_ORDER")
    clob.post_order = MagicMock(return_value={"orderID": "order-1", "status": "live"})
    clob.cancel = MagicMock(return_value=True)

    # 9 poll calls (45s loop) all report a stuck 41.6% fill (well under the
    # 0.95 acceptance threshold) -> loop exhausts and falls into cancel().
    # The 10th call is the post-cancel verify: status flips to "cancelled"
    # but size_matched is still 4.00 -- those shares are real and already
    # bought.
    poll_responses = [
        {"status": "live", "size_matched": "4.00"} for _ in range(9)
    ]
    verify_response = {"status": "cancelled", "size_matched": "4.00"}
    clob.get_order = MagicMock(side_effect=poll_responses + [verify_response])
    client._clob = clob

    order = await client.place_order(
        market_id="mkt-1",
        outcome="YES",
        amount=5.0,
        price=0.50,
        token_id="tok-1",
        question="Bitcoin Up or Down",
    )

    assert order is not None, (
        "A real partial fill matched before the timeout-cancel must still "
        "be returned as a position, not silently dropped as None."
    )
    assert order["status"] == "matched"
    # Target size = floor(5.0 / 0.52 * 100) / 100 = 9.61 shares @ price 0.52
    # (0.02 default price bump on a $0.50 entry). Real fill = 4.00 shares.
    assert order["price"] == pytest.approx(0.52)
    assert order["amount"] == pytest.approx(4.00 * 0.52, abs=1e-4)
    assert order["amount"] != pytest.approx(9.61 * 0.52, abs=1e-4)
