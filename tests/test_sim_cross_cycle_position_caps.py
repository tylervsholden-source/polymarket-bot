"""
Regression test (94th daily review): sim/paper mode's per-cycle position
caps (max_open_positions, MAX_DIRECTIONAL) must account for sim positions
still open from *previous* cycles, not just the current one.

Bug history: the 86th daily review fixed the *intra-cycle* half of this —
the sim branch inside Orchestrator._cycle's approved_signals loop now does
`open_count += 1` / `directional_count += 1` after sim-entering a position,
so later signals *in the same cycle* see the tightened caps (see
tests/test_sim_mode_cycle_budget_counters.py).

But `open_count`/`directional_count` are re-initialized at the *top* of
every `_cycle()` call from `position_manager.open_position_count()` /
`pool_position_count("directional")` — real/live positions only.
Sim/paper positions live in `self._sim_trades` (a plain list on the
Orchestrator instance, never written to `position_manager.data`), and stay
there for 5-45 minutes until `_check_sim_resolutions()` resolves them
(20-45 cycles at the 60-120s cycle interval). So at the start of the very
next `_cycle()` call, both counters silently reset to 0 (their typical
sim-mode value, since no real positions exist) even though many sim
positions from earlier cycles are still open — letting the bot accumulate
far more concurrent sim exposure than CLAUDE.md's hard "max 5 open
positions" / the code's own MAX_DIRECTIONAL=2 cap ever intends to allow,
and silently inflating the apparent riskiness that sim/paper results (used
to judge live-readiness) are supposed to bound.

Fix: seed both counters with `len(self._sim_trades)` whenever the cycle is
not running in live-trading mode (live mode never populates
`self._sim_trades` at all — the sim branch is the `else` of
`if self._is_live_trading():`).
"""
from __future__ import annotations

import inspect

from agents.orchestrator import Orchestrator


def _cycle_source() -> str:
    return inspect.getsource(Orchestrator._cycle)


def test_open_count_seeded_with_carried_over_sim_trades():
    src = _cycle_source()
    anchor = "open_count: int = self.position_manager.open_position_count()"
    idx = src.index(anchor)
    window = src[idx: idx + 900]
    assert "if not self._is_live_trading():" in window
    assert "open_count += len(self._sim_trades)" in window
    # must be seeded before the approved_signals loop can read open_count
    assert src.index("open_count += len(self._sim_trades)") < src.index(
        "for signal, review_decision in coord_result.approved_signals:"
    )


def test_directional_count_seeded_with_carried_over_sim_trades():
    src = _cycle_source()
    anchor = 'directional_count = self.position_manager.pool_position_count("directional")'
    idx = src.index(anchor)
    window = src[idx: idx + 500]
    assert "if not self._is_live_trading():" in window
    assert "directional_count += len(self._sim_trades)" in window
    assert src.index("directional_count += len(self._sim_trades)") < src.index(
        "for signal, review_decision in coord_result.approved_signals:"
    )


def test_carry_over_logic_precedes_the_intra_cycle_increment():
    """Sanity check both fixes coexist: the cross-cycle seed happens once at
    the top, the intra-cycle `+= 1` (86th review) still happens per sim entry
    inside the loop — they are additive, not a replacement of each other."""
    src = _cycle_source()
    seed_idx = src.index("open_count += len(self._sim_trades)")
    per_entry_idx = src.index("self._sim_trades.append(sim_entry)")
    increment_idx = src.index("open_count += 1", per_entry_idx)
    assert seed_idx < per_entry_idx < increment_idx
