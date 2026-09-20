"""
Regression test: the per-cycle directional risk budget ("TOPLAM RİSK
LİMİTİ" — CLAUDE.md's "max 18% of portfolio in directional risk, capped at
$20") never accounted for capital locked in still-open SIM/PAPER positions,
only real (live) ones.

Background: `_cycle()`'s TOPLAM RİSK LİMİTİ block computes
`directional_locked` from `self.position_manager.locked_capital()` (i.e.
`position_manager.data["positions"]`) minus bond positions, then passes it
to `compute_cycle_risk_budget(total_capital, directional_locked)` (fixed for
a different double-counting bug in the 99th daily review).

Bug: sim/paper trades — the bot's actual default running mode (see
Orchestrator._sim_trades' own docstring, and the 94th daily review, which
fixed the analogous gap for the open_count/directional_count POSITION-COUNT
caps) are never written to position_manager.data via add_position(). They
live only in `self._sim_trades` for 5-45 minutes until
`_check_sim_resolutions()` closes them. So `position_manager.locked_capital()`
— and therefore `directional_locked` — was always 0 in sim/paper mode no
matter how much capital earlier cycles' still-open sim positions had
already committed. cycle_budget silently reset to the FULL 18%-of-capital
(or $20) ceiling every single cycle instead of shrinking by what was
already at risk.

Concretely, with $30 total capital and one already-open $4 sim position
carried over from a prior cycle:
    correct budget = min(30 * 0.18, 20) - 4 = 5.40 - 4.00 = $1.40
    buggy budget   = min(30 * 0.18, 20) - 0 = $5.40

— enough headroom to let a second ~$4 trade through in the same cycle
(MAX_DIRECTIONAL=2 still allows it), pushing real directional exposure to
~$8 on $30 capital (~27%), well past the documented 18% ceiling.

Fix: `compute_sim_trades_locked(sim_trades)` sums the `size` field of every
open sim trade, and `_cycle()` adds it to `directional_locked` whenever the
cycle is not running in live-trading mode (mirroring the 94th review's
`if not self._is_live_trading(): ... += len(self._sim_trades)` pattern for
the position-count caps).
"""
from __future__ import annotations

import inspect

from agents.orchestrator import Orchestrator, compute_sim_trades_locked, compute_cycle_risk_budget


def test_compute_sim_trades_locked_sums_size_field():
    sim_trades = [{"size": 4.0}, {"size": 1.5}, {"size": 0.0}]
    assert compute_sim_trades_locked(sim_trades) == 5.5


def test_compute_sim_trades_locked_empty_is_zero():
    assert compute_sim_trades_locked([]) == 0.0


def test_compute_sim_trades_locked_ignores_missing_size_key():
    # Defensive: a malformed/legacy sim_entry without "size" contributes 0,
    # never raises.
    assert compute_sim_trades_locked([{"market_id": "x"}]) == 0.0


def test_budget_with_carried_over_sim_position_matches_intended_18pct_cap():
    """End-to-end arithmetic check of the fixed call site's math."""
    total_capital = 30.0
    sim_trades = [{"size": 4.0}]  # one open sim position from a prior cycle

    directional_locked = 0.0  # position_manager.locked_capital() in sim mode
    directional_locked += compute_sim_trades_locked(sim_trades)
    assert directional_locked == 4.0

    budget = compute_cycle_risk_budget(total_capital, directional_locked)
    assert round(budget, 2) == 1.4, f"expected $1.40 (18% of $30 minus $4 already locked), got ${budget:.2f}"

    # The bug's behavior: sim locked capital never added in.
    buggy_budget = compute_cycle_risk_budget(total_capital, 0.0)
    assert round(buggy_budget, 2) == 5.4
    assert buggy_budget != budget


def test_cycle_adds_sim_trades_locked_capital_when_not_live():
    """Source-level guard: _cycle() must fold compute_sim_trades_locked()
    into directional_locked for sim/paper mode, before computing
    cycle_budget."""
    src = inspect.getsource(Orchestrator._cycle)

    idx_locked = src.index("directional_locked = locked - bond_locked")
    idx_budget = src.index("cycle_budget = compute_cycle_risk_budget(total_capital, directional_locked)")
    assert idx_locked < idx_budget

    window = src[idx_locked:idx_budget]
    assert "if not self._is_live_trading():" in window
    assert "directional_locked += compute_sim_trades_locked(self._sim_trades)" in window
