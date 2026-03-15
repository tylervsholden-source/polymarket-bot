# Executable Notional Specification
## Phase 11 — Effective 2026-03-15

---

## Definitions

### `intended_size_usdc`
The USDC amount the caller *intends* to deploy if the trade executes.
- Set by the caller of `decide()`.
- In live mode: **required** — `None` raises `ValueError`.
- In paper mode: `None` → defaults to 20.0 USDC.
- Used as the input to slippage computation and fill simulation.

### `executable_notional_usdc`
The USDC amount expected to actually fill, given market conditions.

```
executable_notional_usdc = fill_fraction × intended_size_usdc
```

| Fill outcome | fill_fraction | executable_notional |
|--------------|---------------|---------------------|
| FILLABLE     | 1.00          | = intended_size     |
| PARTIAL      | 0.90          | = 0.90 × intended   |
| UNFILLABLE   | 0.00          | = 0.0               |

---

## Where These Fields Live

### `ExecutableCostBreakdown` (execution_realism layer)

```python
@dataclass
class ExecutableCostBreakdown:
    ...
    intended_size_usdc:         float   # passed as parameter (not stored currently — see note)
    executable_notional_usdc:   float   # fill_fraction × intended_size_usdc
    fill_fraction:              float   # = fill_sim.expected_fill_fraction
    ev_before_fill_adjustment:  float   # theoretical - fee - slippage - staleness
    partial_fill_penalty:       float   # 0.0 or (1-fill_fraction) × 0.050
```

Note: `intended_size_usdc` is passed to `compute_executable_ev()` but not stored in the
breakdown — it can be recovered as `executable_notional_usdc / fill_fraction` when
fill_fraction > 0.

### `TradeDecision` (calibration layer)

```python
@dataclass
class TradeDecision:
    ...
    intended_size_usdc_used:    float          # size passed to decide()
    executable_notional_usdc:   Optional[float] # fill_fraction × intended_size
    fill_fraction:              Optional[float] # 1.0=FILLABLE, 0.90=PARTIAL, None=early reject
```

---

## EV Computation Chain

```
theoretical_hold_ev       = calibrated_prob - ask_price          (frictionless)
ev_before_fill_adjustment = theoretical - fee - slippage - stale (base frictions only)
partial_fill_penalty      = (1 - fill_fraction) × 0.050          (0.0 if FILLABLE)
executable_ev             = ev_before_fill - partial_fill_penalty (decision metric)
total_friction            = fee + slippage + staleness + partial_fill_penalty
```

For FILLABLE:
```
ev_before_fill_adjustment == executable_ev  (penalty=0)
```

For PARTIAL (paper mode):
```
executable_ev < ev_before_fill_adjustment   (penalty reduces EV by 50bps)
```

For PARTIAL (live mode):
```
executable_ev is computed for audit, but passes_gate=False regardless
```

---

## Design Notes

### Why not rescale slippage by fill_fraction?
Slippage is computed using `intended_size_usdc` (conservative: full size is attempted).
If only 90% fills, the actual slippage may be slightly lower — but this is uncertain.
Conservative choice: compute slippage on full intended size, then apply a residual penalty.

### Why fill_fraction=0.90 for all PARTIAL fills?
The fill simulator uses a binary classification: FILLABLE/PARTIAL/UNFILLABLE.
All PARTIAL fills are assigned `expected_fill_fraction=0.90` regardless of where in the
25%–50% liquidity band the order falls. This is intentionally conservative and simple.
A continuous fill_fraction model is deferred to Phase 12.

### Relationship to ExecutableCostBreakdown.diagnostics
All Phase 11 fields are also mirrored in `diagnostics`:
```python
diagnostics = {
    "executable_notional_usdc": ...,
    "fill_fraction": ...,
    "ev_before_fill_adjustment": ...,
    "partial_fill_penalty": ...,
    "policy_mode": ...,
    ...
}
```

---

## Usage Example

```python
from calibration.decision_policy import decide
from calibration.types import LIVE_CAL_CONFIG, PAPER_CAL_CONFIG

# Live mode — partial fill rejected
result = decide(cal_signal, pricing,
                config=LIVE_CAL_CONFIG,
                now_utc=now,
                intended_size_usdc=50.0)

if result.decision == TradeDecisionType.REJECT:
    if result.rejection_reason == CalibrationRejectionReason.PARTIAL_FILL_REJECTED:
        # Inspect what actually would have filled
        print(f"Fill fraction:       {result.fill_fraction:.2%}")
        print(f"Executable notional: {result.executable_notional_usdc:.1f} USDC")
        print(f"Intended size:       {result.intended_size_usdc_used:.1f} USDC")

# Paper mode — partial fill allowed with penalty
result = decide(cal_signal, pricing,
                config=PAPER_CAL_CONFIG,
                now_utc=now)

if result.decision in (TradeDecisionType.EXECUTE_YES, TradeDecisionType.EXECUTE_NO):
    breakdown = result.executable_cost_breakdown
    print(f"Fill fraction:       {breakdown.fill_fraction:.2%}")
    print(f"Executable notional: {breakdown.executable_notional_usdc:.1f} USDC")
    print(f"EV before fill adj:  {breakdown.ev_before_fill_adjustment:.4f}")
    print(f"Partial fill penalty:{breakdown.partial_fill_penalty:.4f}")
    print(f"Executable EV:       {breakdown.executable_ev:.4f}")
```
