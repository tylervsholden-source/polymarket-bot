"""
Regression test (86th daily review): sim/paper mode's approved_signals loop
in Orchestrator._cycle must update the same per-cycle risk counters the live
order-placement path updates.

Bug: the live branch (after a successful real order) does
    open_count += 1
    directional_count += 1
    cycle_spent += real_cost
    capital -= real_cost
so that later signals *in the same cycle* see the caps at the top of the
loop (`open_count >= self.max_open_positions`, `directional_count >=
MAX_DIRECTIONAL`, `cycle_spent >= cycle_budget`) tighten as positions are
taken. The `else:` sim/paper branch — the bot's actual default mode per
CLAUDE.md/docs/architecture.md — never touched any of these four names, so
they stayed frozen at their pre-loop values (which, in sim mode, are
essentially always 0/0/0.0/starting-capital, since no real positions exist).
A single `_cycle()` call could therefore sim-enter arbitrarily many
positions from `coord_result.approved_signals` — one per distinct time slot
present that cycle — with `max_open_positions`, `MAX_DIRECTIONAL=2`, and the
18%-of-capital `cycle_budget` all silently inert, making sim-mode risk
exposure (the basis for CLAUDE.md's "Sim Sonuçları" table and any go-live
decision) systematically more permissive than live trading would ever allow
for the same signals.

A second, related bug: `sim_entry["size"]` was set to `signal.size` (Kelly's
raw, pre-adjustment size) rather than the final `bet_size` after OPT-7
half-kelly / REVIEWER_REDUCE / autonomous-engine / walk-forward / adaptive
multiplier all shrink it — so sim logs didn't reflect what live sizing would
actually have produced for the same signal.

Fix: mirror the live branch's bookkeeping in the sim branch, and use
`bet_size` (not `signal.size`) for the recorded sim entry size.
"""
from __future__ import annotations

import inspect

from agents.orchestrator import Orchestrator


def _sim_branch_source() -> str:
    src = inspect.getsource(Orchestrator._cycle)
    marker = "self._sim_trades.append(sim_entry)"
    idx = src.index(marker)
    # a comfortable window after the append covers the bookkeeping lines
    # and the following logger.success() call
    return src[idx: idx + 600]


def test_sim_branch_updates_open_count():
    assert "open_count += 1" in _sim_branch_source()


def test_sim_branch_updates_directional_count():
    assert "directional_count += 1" in _sim_branch_source()


def test_sim_branch_updates_cycle_spent_by_bet_size():
    assert "cycle_spent += bet_size" in _sim_branch_source()


def test_sim_branch_decrements_capital_by_bet_size():
    assert "capital -= bet_size" in _sim_branch_source()


def test_sim_entry_records_final_bet_size_not_raw_signal_size():
    src = inspect.getsource(Orchestrator._cycle)
    assert '"size": bet_size,' in src
    # the old, buggy form (recording Kelly's pre-adjustment size) must be gone
    assert '"size": signal.size,' not in src
