# Decision Flow — Phase 9

## Input Contract

```python
decide(
    cal_signal: CalibratedSignal,      # calibrated probability + mapping
    pricing: MarketPricingSnapshot,    # live market prices (spread derived)
    config: CalibrationConfig,         # policy thresholds
    now_utc: datetime,                 # current time for staleness
    intended_size_usdc: float = 20.0,  # trade size for realism
) -> TradeDecision
```

## Step-by-Step Flow

### Signal Validation (Steps 1–4)
1. **WEAK calibration** → REJECT if `reject_on_weak_calibration=True`
2. **UNKNOWN calibration** → REJECT if `reject_on_unknown_calibration=True`
3. **class_probabilities** → REJECT if `require_class_probabilities=True` and None
4. **Probability boundary** → REJECT if any prob ∉ [0,1] or sum ∉ [0.50, 1.01]

### Signal Contract (Step 5)
5. **Horizon enforcement** → REJECT if horizon_minutes ∉ {5, 15}

### Pricing Validation (Steps 6–8)
6. **Structural pricing** → REJECT if:
   - Any price ∉ [0,1]
   - ask < bid (inverted spread)
   - ask_yes + ask_no ∉ [0.85, 1.15] (binary sanity)
   - Future timestamp
7. **Snapshot staleness** → REJECT if age > max_age (horizon-aware)
8. **Liquidity floor** → REJECT if liquidity < min_liquidity

### Bridge Validation (Step 9)
9. **Bridge intent** → REJECT if bridge_intent_side ∉ {"YES", "NO"}

### Market Quality Checks (Steps 10–11)
10. **Spread** → REJECT if derived_spread > max_spread (side-aware)
    - `derived_spread = ask - bid` (NOT from snapshot field)
11. **Min confidence** → REJECT if effective_prob < min_calibrated_confidence

### Realistic EV Gate (Step 12)
12. **Executable EV** →
    ```
    theoretical_hold_ev = calibrated_prob - ask_price
    fee_cost            = assumed_taker_fee_pct
    slippage            = f(side, liquidity, intended_size_usdc)
    staleness_penalty   = f(snapshot_age_seconds, horizon_minutes)
    fill_sim            = f(ask_price, intended_size_usdc, liquidity)

    total_friction = fee + slippage + staleness_penalty
    executable_ev  = theoretical_hold_ev - total_friction

    passes_gate    = executable_ev >= min_execution_adjusted_edge
                     AND NOT staleness.should_reject
                     AND fill_decision != UNFILLABLE
    ```
    REJECT if NOT passes_gate

## Output Contract

```python
TradeDecision(
    decision=EXECUTE_YES | EXECUTE_NO | REJECT,
    rationale=str,                          # human-readable explanation
    rejection_reason=CalibrationRejectionReason | None,

    # Audit trail
    theoretical_hold_ev=float | None,       # p - ask
    net_ev_after_fee=float | None,          # theoretical - fee
    final_gate_metric="executable_ev",      # always this in Phase 9+
    final_gate_threshold=float | None,      # config.min_execution_adjusted_edge
    passes_final_gate=bool,                 # True iff EXECUTE_*
    policy_mode="live" | "paper" | "default",
    intended_size_usdc_used=float,          # size used in computation

    # Full breakdown
    edge_estimate=EdgeEstimate,             # gross/net/exec EV
    executable_cost_breakdown=ExecutableCostBreakdown,  # slippage/staleness/fill
    calibrated_signal=CalibratedSignal,     # input signal
)
```

## Authoritative Metric

`executable_ev` is the SOLE pass/fail metric.

There is NO fallback to simple/gross/net EV for pass/fail decisions.
