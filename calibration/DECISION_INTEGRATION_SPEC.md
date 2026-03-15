# Decision Path Integration Spec — Phase 9

## Why Split-Brain Logic Is Unacceptable

Prior to Phase 9, `decide()` used constant-slippage edge estimation as its primary gate.
The execution realism module (`execution_realism/`) existed but was NOT wired into the
main decision chain. This created a split-brain condition:

    decide() → EXECUTE (simple edge says ok)
    execution_realism → REJECT (realistic costs say fail)

This is unacceptable for live trading. The system appeared active but would leak money
on trades that cannot survive realistic execution.

## Primary Decision Flow (Phase 9+)

The ONLY valid pass/fail metric is `executable_ev` from the realistic execution path.

    executable_ev = theoretical_hold_ev - fee - slippage - staleness_penalty
    passes_gate   = executable_ev >= min_execution_adjusted_edge
                    AND NOT staleness.should_reject
                    AND fill_decision != UNFILLABLE

No other EV metric controls final pass/fail.

## Intended Size Contract

`intended_size_usdc` is a first-class parameter in `decide()`.
Default: 20.0 USDC.

Policy:
- Paper mode: default 20.0 is documented and acceptable.
- Live mode: callers SHOULD pass explicit size. Default 20.0 is permitted but flagged
  in documentation. Future: `require_intended_size=True` will enforce this.

Size affects:
- Slippage model (size bucket penalty)
- Fill simulation (size vs liquidity ratio)
- Executable EV computation

## Staleness Policy

Staleness is handled by `execution_realism/staleness_penalty.py`:

| Zone    | 5m horizon  | 15m horizon | Effect                    |
|---------|-------------|-------------|---------------------------|
| FRESH   | ≤30s        | ≤60s        | No penalty                |
| AGING   | 31-90s      | 61-180s     | Small EV degrade (0.003-0.005) |
| STALE   | 91-180s     | 181-300s    | Stronger degrade (0.010-0.015) |
| EXPIRED | >180s       | >300s       | Reject (passes_gate=False) |

Binary staleness gate in `decide()` step 7: snapshot older than `max_age` → REJECT.
Continuous penalty inside executable EV computation compounds this.

## Fill Realism Policy

Policy: PARTIAL fills are ALLOWED with degraded expected notional.

| Fill Decision | Condition           | Effect on pass/fail |
|---------------|---------------------|---------------------|
| FILLABLE      | size ≤ 25% liq      | No impact           |
| PARTIAL       | 25% < size ≤ 50%    | Allowed (slippage already degraded) |
| UNFILLABLE    | size > 50% liq      | REJECT (passes_gate=False) |

## Deprecated Paths

`estimate_yes_edge()` and `estimate_no_edge()` (constant-slippage) are DEPRECATED.
- They may not be called from `decide()`.
- They remain for backward-compat test comparison only.
- Test files that directly call them are in `test_edge_estimation.py` (diagnostic only).

## What Remains Deferred

- Phase 10 (shadow runner): live data → paper decisions → outcome logging
- Phase 11 (calibration lifecycle): drift detection, re-fit
- Kelly/portfolio sizing: only `intended_size_usdc` at the per-trade level
- Live order placement: NOT YET
