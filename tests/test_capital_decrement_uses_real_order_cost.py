"""
Regression test: live order execution must decrement the in-cycle `capital`
(and `cycle_spent`) by the REAL order cost, not the requested bet size.

Historically `Orchestrator._cycle`'s live branch placed the order directly
(`self.client.place_order(...)`) and this file guarded that its `capital`/
`cycle_spent` decrement used the real, CLOB-min-size-inflated `order["amount"]`
rather than the requested `bet_size`.

The 145th daily review removed that direct-order call entirely: it was a
41-round-standing bypass of the mandatory approval queue (INC-2026-03-15-001
— "Sinyal -> emir arasında insan onayı ZORUNLU"), hardcoding
`is_approved=True` into `check_live_gate()` and then placing a real order
without ever routing it through `core.approval_queue.enqueue()`. The live
branch now only enqueues (`_enqueue_order(...)`) and does not spend real
capital or call `client.place_order()` at all — no position exists, and no
capital is really spent, until an operator approves it via the dashboard and
`Orchestrator._execute_approved_orders()` (the only method that now calls
`client.place_order()` for directional signals) executes it. That method
already decremented by the real order cost before this fix and is unchanged;
`test_approval_queue_path_decrements_by_real_order_amount` below still covers
it.

`core/polymarket_client.py::place_order()` enforces the CLOB's 5-share
minimum order size (`if size < 5.0: size = 5.0`) and returns the REAL cost
as `order["amount"] = round(size * price, 4)`, which can be several times
larger than the requested `amount` whenever the requested notional is small
and/or the (bumped) price is high — e.g. amount=$1 @ price=0.90 floors to
size=5 shares -> real cost ~$4.55, a 4.5x understatement.
"""
from __future__ import annotations

import inspect
import math

from agents.orchestrator import Orchestrator


def test_direct_order_path_no_longer_places_real_orders():
    src = inspect.getsource(Orchestrator._cycle)
    # isolate the live-order branch: the 86th daily review added a legitimate
    # `capital -= bet_size` / `cycle_spent += bet_size` pair to the sim/paper
    # `else:` branch below this one (see test_sim_mode_cycle_budget_counters.py),
    # so the assertions below must not scan the whole function.
    live_branch = src[src.index("if self._is_live_trading():"): src.index("            else:\n")]
    # Directional signals must now go through the approval queue, never a
    # direct client.place_order() call, and must not touch real `capital`.
    assert "_enqueue_order(" in live_branch
    assert "self.client.place_order(" not in live_branch
    assert "capital -= real_cost" not in live_branch
    assert "cycle_spent += real_cost" not in live_branch
    assert "capital -= bet_size\n" not in live_branch


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
