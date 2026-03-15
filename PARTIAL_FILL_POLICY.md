# Partial Fill Policy
## Phase 11 — Effective 2026-03-15

---

## Policy Decision: Option C — Live Strict / Paper Flexible

### Live Mode (`mode="live"`)
**PARTIAL fills are rejected outright.**

Any order where the fill simulator returns `FillDecision.PARTIAL` causes `decide()` to return
`REJECT` with reason `PARTIAL_FILL_REJECTED` before the edge gate.

**Rationale:** In live trading, a partial fill leaves the unfilled portion as residual exposure.
That residual must be closed in a subsequent order at an uncertain — likely worse — price.
The residual risk is economically unquantifiable in the current model. For real capital,
this uncertainty is unacceptable. Only `FILLABLE` (100% fill) orders proceed to execution.

```
Live:  FILLABLE   → proceeds to edge gate → may EXECUTE
Live:  PARTIAL    → REJECT (PARTIAL_FILL_REJECTED) — before edge gate
Live:  UNFILLABLE → REJECT (NEGATIVE_EDGE via passes_gate=False)
```

### Paper Mode (`mode="paper"` or `"default"`)
**PARTIAL fills are modeled economically and may proceed.**

A `PARTIAL` fill applies the residual risk penalty to `executable_ev`:
```
partial_fill_penalty = (1 - fill_fraction) * 0.050
```
For `fill_fraction=0.90`: `0.10 × 0.050 = 0.005` (50 bps penalty on unfilled portion).

The penalized `executable_ev` is then evaluated against `min_execution_adjusted_edge`.
If it still passes, the trade proceeds as `EXECUTE_*`.

```
Paper: FILLABLE   → no penalty → proceeds to edge gate → may EXECUTE
Paper: PARTIAL    → 50bps penalty applied → may still EXECUTE if EV ≥ threshold
Paper: UNFILLABLE → REJECT (passes_gate=False)
```

---

## Why Not Option A (Always Economic Rescaling)?

Option A applies EV rescaling in live mode, allowing partial fills through with a
penalty. This is incorrect for live trading because:
1. The penalty is calibrated for research (conservative but not prohibitive).
2. 50bps does not fully price the open-execution risk in a real order book.
3. Live mode requires certainty of fill, not a statistical approximation.

## Why Not Option B (Global Threshold Rejection)?

Option B rejects partial fills below a fixed fill_fraction threshold in all modes.
This breaks paper-mode research workflows where partial fills are informative observations.
Option C preserves paper-mode flexibility while hardening live mode.

---

## Fill Bucket Thresholds

| Bucket     | Condition              | fill_fraction | Live outcome       | Paper outcome           |
|------------|------------------------|---------------|--------------------|-------------------------|
| FILLABLE   | size ≤ 25% liquidity   | 1.00          | → edge gate        | → edge gate             |
| PARTIAL    | 25% < size ≤ 50% liq   | 0.90          | REJECT immediately | EV - 50bps → edge gate  |
| UNFILLABLE | size > 50% liquidity   | 0.00          | REJECT (gate)      | REJECT (gate)           |

---

## Audit Trail

Every `TradeDecision` now carries:

| Field                   | Type           | Populated when     |
|-------------------------|----------------|--------------------|
| `fill_fraction`         | `Optional[float]` | EXECUTE or PARTIAL_FILL_REJECTED |
| `executable_notional_usdc` | `Optional[float]` | EXECUTE or PARTIAL_FILL_REJECTED |
| `intended_size_usdc_used` | `float`       | Always (incl. steps 1-9 rejects) |
| `policy_mode`           | `str`          | Always (incl. steps 1-9 rejects) |

`ExecutableCostBreakdown` carries:

| Field                      | Meaning                                      |
|----------------------------|----------------------------------------------|
| `fill_fraction`            | Copy of `fill_sim.expected_fill_fraction`    |
| `executable_notional_usdc` | `fill_fraction × intended_size_usdc`         |
| `ev_before_fill_adjustment`| EV before partial fill penalty (fee+slip+stale only) |
| `partial_fill_penalty`     | 0.0 for FILLABLE; (1-fill_fraction)×0.05 for PARTIAL |

---

## Rejection Reason Ordering

In `decide()`, `PARTIAL_FILL_REJECTED` fires **after** step 11 (confidence check)
and **before** step 12 (edge gate). This means:

- Steps 1–11 pass → edge is computed → fill is checked → if PARTIAL in live → REJECT
- The edge is available in the audit trail even for partial fill rejections

---

## What Is Not Done

- PARTIAL fills are NOT normalized (residual not re-queued in simulation)
- fill_fraction is fixed at 0.90 for all PARTIAL fills (not size-continuous)
- No live mode configurable threshold (future: `CalibrationConfig.live_min_fill_fraction`)
- UNFILLABLE rejection reason remains `NEGATIVE_EDGE` (via `passes_gate=False`), not a
  separate `UNFILLABLE_REJECTED` reason — deferred to Phase 12

---

## Tests

| Test File | Covers |
|-----------|--------|
| `execution_realism/tests/test_partial_fill_policy.py` | `compute_executable_ev` live/paper divergence |
| `execution_realism/tests/test_executable_notional.py` | `executable_notional_usdc` computation |
| `execution_realism/tests/test_fill_fraction_effect.py` | `ev_before_fill_adjustment` formula |
| `calibration/tests/test_live_vs_paper_fill_behavior.py` | `decide()` live/paper divergence |
| `calibration/tests/test_partial_fill_audit.py` | Audit trail completeness |
