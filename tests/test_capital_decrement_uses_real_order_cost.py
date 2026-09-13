"""
Regression test: live order execution must decrement the in-cycle `capital`
(and `cycle_spent`) by the REAL order cost, not the requested bet size.

Bug (agents/orchestrator.py, both live order-placement sites —
`Orchestrator._cycle`'s direct-order path and
`Orchestrator._execute_approved_orders`'s approval-queue path):
    capital -= bet_size       # direct-order path
    cycle_spent += bet_size
    ...
    capital -= amount         # approval-queue path

`core/polymarket_client.py::place_order()` enforces the CLOB's 5-share
minimum order size (`if size < 5.0: size = 5.0`) and returns the REAL cost
as `order["amount"] = round(size * price, 4)`, which can be several times
larger than the requested `amount`/`bet_size` whenever the requested notional
is small and/or the (bumped) price is high — e.g. bet_size=$1 @ price=0.90
floors to size=5 shares -> real cost ~$4.55, a 4.5x understatement.

Because the code decremented `capital`/`cycle_spent` by the requested size
instead of `order["amount"]` (already sitting on `order` after the call, but
never read back), a multi-signal cycle could:
  1. blow straight through its own `cycle_budget` (min(capital*0.18, $20))
     without `CYCLE_CAP` ever tripping, and
  2. size every later signal in the same cycle off of `capital` that
     overstates what's actually left, so `compute_bet_size()`'s position_cap
     (CLAUDE.md's non-negotiable "max tek pozisyon: %20") is computed from
     capital that isn't really there.

`core/position_manager.add_position()` already stores the correct
`order["amount"]` as the position's cost basis, so cross-cycle accounting
(`available_capital()`) was never affected — only the same-cycle running
`capital`/`cycle_spent` counters used while placing further orders in that
same `_cycle()` call.
"""
from __future__ import annotations

import inspect
import math

from agents.orchestrator import Orchestrator


def test_direct_order_path_decrements_by_real_order_amount():
    src = inspect.getsource(Orchestrator._cycle)
    assert 'real_cost = order.get("amount", bet_size)' in src
    assert "capital -= real_cost" in src
    assert "cycle_spent += real_cost" in src
    # the old, buggy form must be gone
    assert "capital -= bet_size\n" not in src
    assert "cycle_spent += bet_size\n" not in src


def test_approval_queue_path_decrements_by_real_order_amount():
    src = inspect.getsource(Orchestrator._execute_approved_orders)
    assert 'capital -= order.get("amount", amount)' in src
    assert "capital -= amount\n" not in src


def test_clob_min_size_floor_can_inflate_real_cost_far_past_requested_amount():
    """Demonstrates the exact scenario that made this bug matter: a small
    requested amount at a high price, floored to the CLOB's 5-share minimum,
    costs much more than requested — the gap `capital -=` must account for.
    """
    def real_cost(amount: float, price: float) -> float:
        size = math.floor(amount / price * 100) / 100
        if size < 5.0:
            size = 5.0
        return round(size * price, 4)

    bet_size, price = 1.0, 0.90
    cost = real_cost(bet_size, price)
    assert cost > bet_size * 3, "expected the 5-share floor to inflate cost well past the request"
