# Executable Notional Specification

**Module:** `execution_realism/`

## What Is Executable Notional

`executable_notional_usdc = intended_size_usdc * fill_fraction`

The dollar amount that will actually be executed given realistic fill expectations.

`intended_size_usdc` = Kelly-sized position (before fill realism)
`fill_fraction` = fraction of intended size that can actually be filled
`executable_notional_usdc` = final realistic position size

## EV Computation Chain

```
gross_ev = effective_yes_prob - ask_price
  ↓ (fee deduction)
net_ev_after_fee = gross_ev - fee_rate
  ↓ (fill realism)
execution_adjusted_ev = net_ev_after_fee * fill_fraction - partial_fill_penalty
  ↓ (gate check)
passes if execution_adjusted_ev ≥ min_execution_adjusted_edge
```

## Fill Fraction Sources

In the calibration pipeline (ShadowRunner path):
- `execution_realism/fill_simulator.py` computes fill_fraction from:
  - liquidity depth
  - order size relative to market
  - Stoikov model spread estimate

In the live orchestrator path (ArbitrageEngine):
- StoikovModel provides fill realism estimate directly

## Recording in Journal

`decision_summary.fill_fraction`:
- null when decision is REJECT before fill estimation
- 1.0 when full fill expected
- 0.0 to 1.0 when partial fill expected

`decision_summary.execution_adjusted_ev`:
- null when decision is REJECT before EV computation
- the primary EV metric for monitoring and drift detection

## Thresholds

| Profile | min_execution_adjusted_edge |
|---|---|
| live | 0.030 |
| paper_strict | 0.025 |
| paper_loose | 0.020 |
