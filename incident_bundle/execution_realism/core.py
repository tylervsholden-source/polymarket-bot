"""execution_realism/core.py — Top-level compute_executable_ev function."""
from __future__ import annotations

from execution_realism.slippage_model import compute_slippage
from execution_realism.staleness_penalty import compute_staleness_penalty
from execution_realism.liquidity_model import assess_liquidity
from execution_realism.fill_simulator import simulate_fill
from execution_realism.types import ExecutableCostBreakdown, FillDecision

# Phase 11: PARTIAL fill economics — Option C (Live Strict / Paper Flexible).
#
# Policy:
#   Live mode  : PARTIAL fill → passes_gate=False (rejected at decide() as PARTIAL_FILL_REJECTED).
#                EV is still computed for audit trail; penalty is included for completeness.
#   Paper/default: PARTIAL fill → EV penalty applied; trade passes if EV ≥ threshold.
#
# Penalty formula (paper mode):
#   penalty = (1 - fill_fraction) * _PARTIAL_FRICTION_RATE
#   fill_fraction=0.90 → penalty = 0.10 * 0.050 = 0.005 (50 bps on unfilled portion)
#
# Rationale: residual risk — the unfilled portion of a PARTIAL order requires a subsequent
# order at an uncertain (likely worse) price. In live mode this risk is unacceptable.
_PARTIAL_FRICTION_RATE: float = 0.050


def compute_executable_ev(
    side: str,
    calibrated_event_probability: float,
    ask_price: float,
    fee_pct: float,
    intended_size_usdc: float,
    liquidity_usdc: float,
    snapshot_age_seconds: float,
    horizon_minutes: int,
    required_threshold: float,
    policy_mode: str = "default",  # "live" | "paper" | "default"
) -> ExecutableCostBreakdown:
    """
    Full executable EV computation with Phase 11 partial fill policy.

    theoretical_hold_ev    = calibrated_prob - ask_price  (frictionless upper bound)
    ev_before_fill_adjustment = theoretical - fee - slippage - staleness  (pre-fill EV)
    executable_ev          = ev_before_fill_adjustment - partial_fill_penalty
    passes_gate            = executable_ev >= threshold
                             AND NOT staleness.should_reject
                             AND fill_decision != UNFILLABLE
                             AND NOT (policy_mode="live" AND fill_decision=PARTIAL)

    policy_mode="live"    : PARTIAL fill forces passes_gate=False (hard reject).
    policy_mode="paper"   : PARTIAL fill applies 50bps penalty; passes if EV >= threshold.
    policy_mode="default" : same as "paper" (backward compatible).
    """
    theoretical = calibrated_event_probability - ask_price
    slippage     = compute_slippage(side, liquidity_usdc, intended_size_usdc)
    staleness    = compute_staleness_penalty(snapshot_age_seconds, horizon_minutes)
    liquidity    = assess_liquidity(liquidity_usdc)
    fill_sim     = simulate_fill(ask_price, intended_size_usdc, liquidity_usdc)

    fill_fraction            = fill_sim.expected_fill_fraction
    executable_notional_usdc = fill_fraction * intended_size_usdc

    # EV before any fill adjustment: theoretical minus base frictions.
    ev_before_fill = theoretical - (fee_pct + slippage.total_slippage + staleness.penalty)

    if fill_sim.fill_decision == FillDecision.UNFILLABLE:
        partial_fill_penalty = 0.0
        executable_ev        = ev_before_fill
        passes_gate          = False

    elif fill_sim.fill_decision == FillDecision.PARTIAL:
        partial_fill_penalty = (1.0 - fill_fraction) * _PARTIAL_FRICTION_RATE
        executable_ev        = ev_before_fill - partial_fill_penalty
        if policy_mode == "live":
            # Live strict: PARTIAL fill rejected outright — residual risk unacceptable.
            passes_gate = False
        else:
            # Paper flexible: EV penalty applied; passes if still above threshold.
            passes_gate = (
                executable_ev >= required_threshold
                and not staleness.should_reject
            )

    else:  # FILLABLE
        partial_fill_penalty = 0.0
        executable_ev        = ev_before_fill
        passes_gate          = (
            executable_ev >= required_threshold
            and not staleness.should_reject
        )

    total_friction = fee_pct + slippage.total_slippage + staleness.penalty + partial_fill_penalty

    return ExecutableCostBreakdown(
        side=side,
        theoretical_hold_ev=round(theoretical, 6),
        fee_cost=fee_pct,
        slippage=slippage,
        staleness=staleness,
        liquidity=liquidity,
        fill_sim=fill_sim,
        total_friction=round(total_friction, 6),
        executable_ev=round(executable_ev, 6),
        passes_gate=passes_gate,
        required_threshold=required_threshold,
        partial_fill_penalty=round(partial_fill_penalty, 6),
        executable_notional_usdc=round(executable_notional_usdc, 6),
        fill_fraction=fill_fraction,
        ev_before_fill_adjustment=round(ev_before_fill, 6),
        diagnostics={
            "theoretical_hold_ev":       round(theoretical, 6),
            "executable_ev":             round(executable_ev, 6),
            "ev_before_fill_adjustment": round(ev_before_fill, 6),
            "total_friction":            round(total_friction, 6),
            "partial_fill_penalty":      round(partial_fill_penalty, 6),
            "executable_notional_usdc":  round(executable_notional_usdc, 6),
            "fill_fraction":             fill_fraction,
            "policy_mode":               policy_mode,
            "staleness_zone":            staleness.zone.value,
            "fill_decision":             fill_sim.fill_decision.value,
            "slippage_bucket":           slippage.diagnostics.get("liquidity_bucket"),
        },
    )
