"""
Regression test: Orchestrator._sync_open_orders_from_clob() hardcodes every
restart-recovered position's `outcome` to "YES", discarding the real
outcome the CLOB order was actually placed on.

Bug (agents/orchestrator.py::Orchestrator._sync_open_orders_from_clob()):

    if status == "MATCHED" and amount > 0:
        self.position_manager.add_position(
            market_id,
            {
                "order_id": order.get("id", market_id),
                "outcome": "YES",  # CLOB doesn't expose side easily
                "amount": amount,
                "price": price,
                "status": "matched",
            },
            question=f"[CLOB_SYNC] {market_id[:40]}",
        )

This runs once at startup (`Orchestrator.run()`) specifically to recover
positions a *previous* process instance already placed live orders for
(restart duplicate-order protection). CLOB's GET /orders response for each
order carries the order's own `outcome` field (e.g. "Yes"/"No", or
"Up"/"Down" for the up/down markets this bot trades) — but that field is
never read; every MATCHED order recovered this way is unconditionally
recorded as a YES position, regardless of which side it actually is.

Concrete failure scenario: the bot places a live $20 NO order, then the
process restarts (deploy, crash-restart, manual bounce) before that
market resolves. On restart, `_sync_open_orders_from_clob()` finds the
MATCHED NO order via CLOB and re-adds it to `position_manager` — but
labels it `outcome: "YES"`. When the market later resolves DOWN (the real
NO position's winning outcome), `PositionManager.update_positions()`'s
resolution logic computes the close price from the (wrong) recorded
outcome:

    resolution == "NO":
        close_price = 0.0 if outcome == "YES" else 1.0

Since `outcome` was mislabeled "YES", `close_price` becomes 0.0 — the
winning $20 NO position is closed as a total LOSS (pnl=-$20) instead of
its real WIN payout, corrupting `position_manager.data["capital"]` and the
CLAUDE.md daily -15% stop-loss denominator in the wrong direction. The
inverse (a losing NO position mislabeled a "win") is equally possible.

Fix: read the order's own `outcome` field (normalizing Yes/Up -> "YES",
No/Down -> "NO"), falling back to "YES" only when the field is missing or
unrecognized (preserving the previous behavior in that edge case, never
regressing it). Also carry through the order's `asset_id` as `token_id`,
which `PositionManager` already prefers as the authoritative source for a
NO position's own orderbook lookup.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def tmp_positions_file(tmp_path, monkeypatch):
    import core.position_manager as pm_mod
    monkeypatch.setattr(pm_mod, "DATA_FILE", tmp_path / "positions.json")
    monkeypatch.setenv("INITIAL_CAPITAL", "1000.0")


def _make_orchestrator(open_orders):
    from agents.orchestrator import Orchestrator
    from core.position_manager import PositionManager

    orch = Orchestrator.__new__(Orchestrator)
    orch.client = MagicMock()
    orch.client.get_open_orders.return_value = open_orders
    orch.position_manager = PositionManager()
    orch._reentry_guard = MagicMock()
    return orch


def test_sync_recovers_real_no_order_as_no_not_yes():
    """A MATCHED NO order recovered after restart must keep its real
    outcome ("NO"), not be silently flipped to "YES"."""
    order = {
        "id": "0xorder1",
        "market": "0xmarketNO",
        "asset_id": "0xnotoken111",
        "status": "MATCHED",
        "price": "0.35",
        "original_size": "57.14",
        "outcome": "No",
    }
    orch = _make_orchestrator([order])

    orch._sync_open_orders_from_clob()

    pos = orch.position_manager.data["positions"]["0xmarketNO"]
    assert pos["outcome"] == "NO", (
        "Restart-recovered NO order must be recorded as outcome=NO, not "
        "hardcoded to YES — the CLOB order's own `outcome` field was "
        "ignored."
    )


def test_sync_recovered_no_win_must_not_be_booked_as_a_loss():
    """End-to-end consequence check, using PositionManager's own
    resolution formula (agents/orchestrator.py::update_positions() /
    core/position_manager.py._close_position() call sites): a recovered
    $20 NO position that goes on to WIN (market resolves DOWN) must be
    credited a positive payout, not booked as a full loss because its
    outcome was mislabeled "YES" at recovery time.
    """
    order = {
        "id": "0xorder2",
        "market": "0xmarketNO2",
        "asset_id": "0xnotoken222",
        "status": "MATCHED",
        "price": "0.50",
        "original_size": "40",
        "outcome": "No",
    }
    orch = _make_orchestrator([order])
    orch._sync_open_orders_from_clob()

    pos = orch.position_manager.data["positions"]["0xmarketNO2"]
    outcome = pos.get("outcome", "YES").upper()

    # Market resolves DOWN -> the real NO position's winning side.
    resolution = "NO"
    if resolution == "YES":
        close_price = 1.0 if outcome == "YES" else 0.0
    else:
        close_price = 0.0 if outcome == "YES" else 1.0

    orch.position_manager._close_position("0xmarketNO2", close_price)
    closed = orch.position_manager.data["closed"][-1]

    assert closed["result"] == "WIN", (
        f"A genuinely winning NO position was booked as {closed['result']} "
        f"(pnl=${closed['pnl']:+.2f}) because _sync_open_orders_from_clob() "
        "mislabeled its outcome as YES instead of the real NO."
    )
    assert closed["pnl"] > 0
