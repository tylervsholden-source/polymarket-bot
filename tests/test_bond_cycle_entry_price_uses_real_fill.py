"""
Regression test (66th daily review): Orchestrator._bond_cycle must store the
REAL order price returned by place_passive_order() as the position's
entry_price, not the pre-adjustment BondScanner opportunity price — the same
"recorded price must match the order call's actual return" bug class already
fixed for the directional order path (`Orchestrator._cycle`, `order["price"]`
from `place_order()` passed straight into `add_position()`), but left unfixed
on the bond path.

Bug: `core/polymarket_client.py::place_passive_order()` re-quantizes the
requested price to `round(min(max(price, 0.01), 0.99), 2)` before placing the
order and returns that REAL price as `order_result["price"]`. BondScanner
opportunity prices are not 2-decimal-clean (NO side:
`round(1.0 - yes_price + 0.02, 4)`; YES side: raw Gamma best_ask), so the
value actually filled at can differ from `opp.price`.

`_bond_cycle()` stored `"price": opp.price` (the stale, pre-adjustment
intent) instead of `order_result.get("price", opp.price)` (the real fill
price). `PositionManager.add_position()` writes this straight through as
`entry_price`, and every downstream P&L/close calculation derives
`shares = amount / entry_price`, so this silently misstated share count and
payout on every bond trade.
"""
from __future__ import annotations

import inspect

from agents.orchestrator import Orchestrator


def test_bond_cycle_stores_real_order_price_as_entry_price():
    src = inspect.getsource(Orchestrator._bond_cycle)
    assert 'order_result.get("price", opp.price)' in src
    # the old, buggy form must be gone
    assert '"price": opp.price,' not in src
