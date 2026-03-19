# Partial Fill Policy

**Module:** `execution_realism/fill_simulator.py` (calibration pipeline)
**Live path:** ArbitrageEngine StoikovModel fill fraction estimate

## What Is Partial Fill

A partial fill occurs when a limit order at `ask_price` cannot be fully
filled at the expected price. The fill fraction < 1.0.

`fill_fraction = expected_shares_filled / intended_shares`

## Policy by Profile

| Profile | Policy | fill_fraction behavior |
|---|---|---|
| live | REJECT if partial | fill_fraction < 1.0 → REJECT → PARTIAL_FILL_REJECT |
| paper_strict | EV penalty | execution_adjusted_ev *= fill_fraction |
| paper_loose | EV penalty | execution_adjusted_ev *= fill_fraction |

In live mode: any expected partial fill → trade is rejected.
Rationale: partial fills + unfilled residual create unmanaged exposure.

## EV Penalty Calculation (paper modes)

```
gross_ev = effective_yes_prob - ask_price
net_ev_after_fee = gross_ev - fee
execution_adjusted_ev = net_ev_after_fee * fill_fraction
```

The final gate compares `execution_adjusted_ev` against `min_execution_adjusted_edge`.

## Partial Fill Rejection Rate Alert

Health monitor alerts when:
- partial fill rejection rate > 30% of EXECUTE decisions
- Suggests: market is consistently illiquid or spread is too wide

## partial_fill_penalty in paper modes

`partial_fill_penalty = 50bps = 0.0050`

Applied as: `execution_adjusted_ev -= partial_fill_penalty` when fill_fraction < 1.0
(in addition to fill_fraction scaling).
