"""
Regression test (38th daily review): Orchestrator._bond_cycle must decrement
its in-cycle `bond_capital` counter by the REAL order cost, not the requested
bet size — the exact same bug already fixed for the directional order path
(`Orchestrator._cycle`) and the approval-queue path
(`Orchestrator._execute_approved_orders`) in an earlier daily review (see
tests/test_capital_decrement_uses_real_order_cost.py), but left unfixed in
the bond-scanning path.

Bug: `core/polymarket_client.py::place_passive_order()` enforces the CLOB's
5-share minimum order size (`if size < 5.0: size = 5.0`) and returns the REAL
cost as `order["amount"] = round(size * price, 4)`. Bond opportunities buy at
price 0.93-0.97 (BondScanner.PROB_THRESHOLD..MAX_PRICE); a bet_size just above
the $3.0 floor (e.g. $4.00 @ price=0.95 -> size=4.21 shares) gets floored to
5 shares -> real cost ~$4.75, well above the requested bet_size.

`_bond_cycle()`'s loop did `bond_capital -= bet_size` instead of
`bond_capital -= order_result.get("amount", bet_size)`, so its local
remaining-capital tracker overstated what was actually left after an order
whose real cost got floored up. Within a single `_bond_cycle()` call (which
places up to BondScanner.MAX_POSITIONS=3 orders back-to-back before the next
`_sync_real_balance()` reconciles against the real CLOB balance), this could
let the loop place another bond order the real bond pool could not actually
cover.

Each *position's* stored cost basis was already correct
(`"amount": order_result.get("amount", bet_size)` passed to
`add_position()`), so this only affected the same-cycle running
`bond_capital` counter used to decide whether to keep placing more orders.
"""
from __future__ import annotations

import inspect
import math

from agents.orchestrator import Orchestrator


def test_bond_cycle_decrements_by_real_order_amount():
    src = inspect.getsource(Orchestrator._bond_cycle)
    assert 'real_cost = order_result.get("amount", bet_size)' in src
    assert "bond_capital -= real_cost" in src
    # the old, buggy form must be gone
    assert "bond_capital -= bet_size\n" not in src


def test_clob_min_size_floor_can_inflate_bond_cost_past_requested_amount():
    """Demonstrates the exact scenario: a bond bet just above the $3.0 floor
    at a typical bond price (0.93-0.97), floored to the CLOB's 5-share
    minimum, costs more than requested — the gap `bond_capital -=` must
    account for so the loop doesn't overspend the real bond pool.
    """
    def real_cost(bet_size: float, price: float) -> float:
        size = round(bet_size / price, 2)
        if size < 5.0:
            size = 5.0
        return round(size * price, 4)

    bet_size, price = 4.0, 0.95
    cost = real_cost(bet_size, price)
    assert cost > bet_size, "expected the 5-share floor to inflate cost past the request"
    assert math.isclose(cost, 4.75, abs_tol=0.01)
