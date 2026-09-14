"""
Regression test: MAX_DIRECTIONAL cap must be enforced within a single cycle,
not just across cycles.

Bug (agents/orchestrator.py, `Orchestrator._cycle`'s direct-order loop):
    MAX_DIRECTIONAL = 2  # PIVOT: reduced from 5 - maker gets most capital
    directional_count = self.position_manager.pool_position_count("directional")
    for signal, review_decision in coord_result.approved_signals:
        ...
        if directional_count >= MAX_DIRECTIONAL:
            break
        ...
        self.position_manager.add_position(market_id, order, market["question"])
        ...
        open_count += 1
        # directional_count was never incremented here

`directional_count` was read ONCE from disk before the per-signal loop
started, then compared every iteration without ever being updated after a
successful order. `open_count` got the equivalent `open_count += 1` right
after `add_position()`, but `directional_count` didn't, so the
`DIRECTIONAL_CAP` break only fired when 2+ directional positions already
existed BEFORE the cycle began. Within a single cycle, the loop could open
3, 4, or up to `max_open_positions` (5) new directional positions (one per
approved signal, each for a distinct coin/market) before `open_count >=
max_open_positions` ever stopped it - silently exceeding the code's own
documented per-cycle directional-exposure ceiling.

`add_position()` defaults to `strategy="directional"` and the live-order
call site in this loop never passes a different strategy (MAKER_ENABLED/
BOND_ENABLED default to false), so every position opened by this loop
counts against MAX_DIRECTIONAL.
"""
from __future__ import annotations

import inspect

from agents.orchestrator import Orchestrator


def test_directional_count_incremented_alongside_open_count():
    src = inspect.getsource(Orchestrator._cycle)
    idx = src.index("open_count += 1")
    # the increment must appear right after the successful direct-order
    # add_position() call, in the same block as open_count's own increment
    tail = src[idx : idx + 200]
    assert "directional_count += 1" in tail, (
        "directional_count must be incremented alongside open_count after "
        "a successful order, or MAX_DIRECTIONAL only self-corrects on the "
        "next cycle instead of enforcing the cap within this one"
    )


def test_directional_cap_check_precedes_the_missing_increment():
    src = inspect.getsource(Orchestrator._cycle)
    assert "if directional_count >= MAX_DIRECTIONAL:" in src
    # the pre-loop snapshot read must still be there
    assert 'directional_count = self.position_manager.pool_position_count("directional")' in src
