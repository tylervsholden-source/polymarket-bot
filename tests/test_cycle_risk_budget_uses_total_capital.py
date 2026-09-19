"""
Regression test: the per-cycle directional risk budget (CLAUDE.md-adjacent
"max 18% of portfolio in directional risk, capped at $20") must be computed
from TOTAL directional-pool capital, not from `capital` =
position_manager.available_capital() (total capital minus every locked
position, including the very `directional_locked` amount that gets
subtracted next).

Bug (agents/orchestrator.py, `_cycle()`'s "TOPLAM RİSK LİMİTİ" block):
    max_risk = min(capital * 0.18, 20.0)          # capital already excludes locked
    remaining_risk = max(0.0, max_risk - directional_locked)   # subtracted again
    cycle_budget = remaining_risk

With total capital=$100 and $15 already locked in open directional
positions (available capital=$85), the old formula gave:
    max_risk = min(85 * 0.18, 20) = 15.30
    cycle_budget = max(0, 15.30 - 15) = 0.30

versus the documented "max 18% of the $100 portfolio in directional risk"
reading:
    max_risk = min(100 * 0.18, 20) = 18.00
    cycle_budget = max(0, 18.00 - 15) = 3.00

a ~10x-tighter budget than intended — and one that gets silently tighter
still (relative to the true 18% target) as more positions accumulate,
exactly when this cap is meant to matter most, potentially blocking
legitimate EXECUTE candidates that are still well within the real 18%
notional risk ceiling.

Fix: `compute_cycle_risk_budget()` takes TOTAL capital explicitly, and the
call site passes `position_manager.data["capital"]` (total capital,
unlocked+locked) instead of the already-locked-excluded `capital`.
"""
from __future__ import annotations

import inspect

from agents.orchestrator import Orchestrator, compute_cycle_risk_budget


def test_budget_uses_total_capital_not_available_capital():
    """With $100 total capital and $15 already locked, the budget must be
    $3.00 (18% of TOTAL capital minus what's locked), not $0.30 (18% of the
    $85 already-net-of-locked available capital, minus locked again)."""
    total_capital = 100.0
    directional_locked = 15.0

    budget = compute_cycle_risk_budget(total_capital, directional_locked)

    assert budget == 3.0, f"expected $3.00 (18% of $100 minus $15 locked), got ${budget:.2f}"

    # The old, buggy computation using available (post-locked) capital:
    available_capital = total_capital - directional_locked  # $85
    buggy_max_risk = min(available_capital * 0.18, 20.0)     # $15.30
    buggy_budget = max(0.0, buggy_max_risk - directional_locked)  # $0.30
    assert budget != buggy_budget
    assert buggy_budget < 1.0  # sanity: the bug produced a near-zero budget


def test_budget_respects_hard_cap():
    """Large accounts are still capped at $20, regardless of total capital."""
    budget = compute_cycle_risk_budget(total_capital=1000.0, directional_locked=0.0)
    assert budget == 20.0


def test_budget_never_negative_when_overlocked():
    """If directional_locked already exceeds the ceiling, budget floors at 0."""
    budget = compute_cycle_risk_budget(total_capital=50.0, directional_locked=100.0)
    assert budget == 0.0


def test_budget_zero_locked_equals_full_ceiling():
    budget = compute_cycle_risk_budget(total_capital=100.0, directional_locked=0.0)
    assert budget == 18.0


def test_cycle_uses_total_account_capital_for_budget_ceiling():
    """Source-level guard: _cycle() must derive the ceiling from TOTAL
    account capital (position_manager.data["capital"]), not from the
    in-cycle `capital` variable (available_capital(), already net of
    locked positions)."""
    src = inspect.getsource(Orchestrator._cycle)
    assert 'self.position_manager.data.get("capital", capital)' in src
    assert "compute_cycle_risk_budget(total_capital, directional_locked)" in src
    # The old buggy call site must be gone.
    assert "max_risk = min(capital * 0.18, 20.0)" not in src
