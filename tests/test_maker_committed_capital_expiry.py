"""
Regression test: MakerEngine._inventory entries never expire, so
get_committed_capital() permanently overstates real committed capital for
every market that has already resolved.

Background (56th daily review, see tests/test_maker_capital_committed.py):
MakerEngine.on_fill() records filled-but-unresolved inventory cost in
self._inventory[market_id] (yes_cost/no_cost), and
Orchestrator._cycle() subtracts get_committed_capital() from
pool_available("maker") before handing capital to refresh_quotes() each
cycle, specifically so the same real USDC is never re-committed to a new
order while it is still tied up in an unresolved position. That fix is
correct while the market is still open.

Bug: on_fill() only ever increments inv.yes_cost/no_cost (see
MakerEngine.on_fill()), and nothing in this file ever removes an entry from
self._inventory or decrements those fields. Every crypto up/down market
MakerEngine quotes is a brand-new, unique 5-15 minute market_id
(_select_markets() only considers markets closing within 20 minutes), so
once that window closes the market resolves and Polymarket pays out (or
takes) the real USDC — reflected moments later in the wallet balance that
Orchestrator._sync_real_balance() reads directly from the CLOB. But the
matching self._inventory[market_id] entry is never cleaned up, so its cost
keeps counting against get_committed_capital() forever.

Consequence: get_committed_capital() (and therefore
`maker_capital = pool_available("maker") - get_committed_capital()` in
Orchestrator._cycle()) grows monotonically worse as more markets cycle
through and resolve, even though none of that capital is actually still at
risk. Given enough resolved markets, maker_capital is silently clamped to
$0 forever and MakerEngine can never place another quote again, despite
the real wallet having plenty of free USDC.

Fix: MarketInventory now records when it was first created; MakerEngine
drops any inventory entry old enough that its market must have already
resolved (crypto up/down markets are only ever quoted with <=20 minutes
left, so anything older than a generous buffer past that can only be a
stale, already-settled record) before reporting committed capital.
"""
from __future__ import annotations

import time

from strategies.maker_engine import MakerEngine, MarketInventory


def test_committed_capital_excludes_long_resolved_market_inventory():
    engine = MakerEngine()

    # A market MakerEngine filled into a while ago — its 5-15 minute window
    # closed and resolved long ago (real USDC already returned via the CLOB
    # wallet balance), but the in-memory record was never cleaned up.
    stale = MarketInventory(yes_shares=10.0, yes_cost=4.0, no_shares=8.0, no_cost=3.2)
    stale.first_seen = time.time() - 3600  # 1 hour ago — no crypto up/down
    # market this bot quotes stays open anywhere near that long.
    engine._inventory["resolved-market"] = stale

    assert engine.get_committed_capital() == 0.0, (
        "inventory for a market old enough to have long since resolved must "
        "not still count as committed capital — it silently starves "
        "MakerEngine of real, already-free USDC forever"
    )


def test_committed_capital_still_includes_recent_unresolved_inventory():
    """Non-regression: a market quoted moments ago (still genuinely open)
    must still count — only stale, necessarily-resolved entries expire."""
    engine = MakerEngine()
    fresh = MarketInventory(yes_shares=10.0, yes_cost=4.0, no_shares=8.0, no_cost=3.2)
    fresh.first_seen = time.time()
    engine._inventory["open-market"] = fresh

    assert engine.get_committed_capital() == 4.0 + 3.2


def test_on_fill_stamps_first_seen_at_creation_time():
    """New inventory created by a real fill must start its expiry clock now,
    not at some default/epoch-zero time that would make it look instantly
    stale."""
    engine = MakerEngine()
    from strategies.maker_engine import StandingOrder

    engine._standing["order-1"] = StandingOrder(
        order_id="order-1", market_id="m1", token_id="yes-1",
        side="YES", price=0.40, size=10.0, placed_at=time.time(),
    )
    before = time.time()
    engine.on_fill("order-1", price=0.40, size=10.0)
    after = time.time()

    assert before <= engine._inventory["m1"].first_seen <= after
    # And therefore it must still be counted right after the fill.
    assert engine.get_committed_capital() == 4.0
