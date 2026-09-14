"""
Regression test: the AutonomousDecisionEngine's risk-based size_multiplier
must actually shrink the live bet_size, not get silently re-clamped back up.

Bug (22nd daily review): agents/orchestrator.py's per-signal loop applied the
autonomous engine's size_multiplier like this:

    if _auto_size_mult < 1.0:
        bet_size = max(_effective_min, bet_size * _auto_size_mult)

`_effective_min` is `compute_bet_size()`'s capital/dashboard-derived floor
for a *Kelly-derived* signal_size (e.g. $2-4 for a small account) — nothing
to do with how far the autonomous engine is allowed to shrink bet_size once
it has decided a signal is risky. Because compute_bet_size() itself floors
signal_size up to effective_min whenever Kelly's own recommendation is at or
below that floor (the common case for small accounts and marginal-edge
signals — exactly when risk protection matters most), bet_size very often
enters this block already equal to effective_min. In that case:

    bet_size * multiplier < effective_min   (multiplier < 1.0)
    max(effective_min, bet_size * multiplier) == effective_min

i.e. the reduction is thrown away completely. A REVIEWER_VETO (×0.25), a
CRITICAL/DRAWDOWN cap (×0.3-0.5), a LOSS_STREAK cap (×0.5-0.6), or
SURVIVAL_MODE (×0.3) all silently placed the SAME size order as an
unreduced, fully-approved signal — defeating the exact protection the
autonomous engine exists to apply.

Contrast with the walk-forward and adaptive-bet-multiplier adjustments a few
lines below this call site in the same loop, which apply their own
multipliers directly (`bet_size *= wf_mult`) with no re-clamp — this fix
(`apply_risk_size_multiplier`) makes the autonomous engine's adjustment
behave the same way.

Pre-fix: the second assertion in each test below fails (the multiplier has
no effect once bet_size sits at effective_min).
Post-fix: `apply_risk_size_multiplier()` applies the multiplier directly.
"""
from __future__ import annotations

from agents.orchestrator import apply_risk_size_multiplier, compute_bet_size


def _old_buggy_adjustment(bet_size: float, effective_min: float, multiplier: float) -> float:
    """The exact pre-fix formula, kept here so the bug is asserted explicitly
    even without checking out the prior file revision (matching the pattern
    used by prior daily-review regression tests, e.g.
    test_reduce_verdict_size_not_double_applied.py)."""
    return max(effective_min, bet_size * multiplier)


def test_reviewer_veto_reduction_survives_effective_min_floor():
    # A small account where Kelly's own signal_size is weak: compute_bet_size
    # floors bet_size up to effective_min (the common, real-world case).
    bet_size, effective_min = compute_bet_size(
        capital=50.0,
        signal_size=0.50,  # far below the floor
        min_bet=3.0,
        max_bet=8.0,
        max_position_pct=0.20,
    )
    assert bet_size == effective_min, "test setup must start at the floor"

    # The old formula: REVIEWER_VETO's ×0.25 has literally zero effect here.
    old_result = _old_buggy_adjustment(bet_size, effective_min, 0.25)
    assert old_result == effective_min, (
        "sanity check on the bug: old formula silently discards the VETO reduction"
    )

    # The fix: the multiplier must actually apply.
    fixed_result = apply_risk_size_multiplier(bet_size, 0.25)
    assert fixed_result == bet_size * 0.25
    assert fixed_result < effective_min, (
        f"REVIEWER_VETO (x0.25) must actually shrink the order below the "
        f"Kelly-derived floor (${effective_min}), got ${fixed_result}"
    )


def test_drawdown_critical_reduction_survives_effective_min_floor():
    bet_size, effective_min = compute_bet_size(
        capital=15.0,  # survival-mode band
        signal_size=0.0,
        min_bet=3.0,
        max_bet=8.0,
        max_position_pct=0.20,
    )
    assert bet_size == effective_min

    fixed_result = apply_risk_size_multiplier(bet_size, 0.30)  # CRITICAL/SURVIVAL cap
    assert fixed_result == bet_size * 0.30
    assert fixed_result < effective_min


def test_multiplier_of_one_is_a_no_op():
    assert apply_risk_size_multiplier(4.0, 1.0) == 4.0


def test_normal_capital_high_signal_size_also_respects_multiplier():
    # Even when bet_size is well above effective_min (large Kelly signal),
    # the multiplier must still apply in full, not be softened by the floor.
    bet_size, effective_min = compute_bet_size(
        capital=1000.0,
        signal_size=100.0,
        min_bet=3.0,
        max_bet=8.0,
        max_position_pct=0.20,
    )
    assert bet_size > effective_min  # not at the floor this time
    fixed_result = apply_risk_size_multiplier(bet_size, 0.5)
    assert fixed_result == bet_size * 0.5
