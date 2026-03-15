# Execution Realism Spec

## Purpose

The `execution_realism` module exists to distinguish **theoretical EV** from **executable EV**.

Theoretical EV (from calibration layer):
```
theoretical_hold_ev = calibrated_probability - ask_price
```
This is the frictionless upper bound: it assumes perfect fill at ask, no fees, no slippage, and perfectly fresh data.

Executable EV adds realistic costs:
```
executable_ev = theoretical_hold_ev - fee - slippage - staleness_penalty
passes_gate   = executable_ev >= required_threshold
                AND NOT staleness.should_reject
                AND fill_decision != UNFILLABLE
```

A trade that looks attractive at the theoretical level may fail the executable gate due to combined friction. This module makes that friction explicit and measurable.

---

## Components

### 1. Slippage Model (`slippage_model.py`)

Three additive components:
- **Base slippage** — fixed per side (YES=0.003, NO=0.003). Accounts for minimum CLOB friction.
- **Liquidity penalty** — bucket-based: larger markets have lower per-unit slippage.
- **Size penalty** — larger orders consume more of the book.

The slippage model is **side-aware**: YES and NO tokens have independent order books and potentially different depths.

### 2. Staleness Penalty (`staleness_penalty.py`)

Horizon-aware classification of pricing snapshot age:
- **5m signals** use tighter thresholds (fresh ≤ 30s) because a stale price on a 5-minute horizon represents a large fraction of the signal lifetime.
- **15m signals** use looser thresholds (fresh ≤ 60s).

Zones: FRESH (no penalty) → AGING (small penalty) → STALE (larger penalty) → EXPIRED (reject).

EXPIRED snapshots cause `passes_gate=False` regardless of EV.

### 3. Fill Simulation (`fill_simulator.py`)

Conservative approximation of order fill likelihood:
- Size > 50% of available liquidity → UNFILLABLE
- Size 25-50% of liquidity → PARTIAL (90% fill assumed)
- Size ≤ 25% of liquidity → FILLABLE (100% fill)

Entry price is always the ask. No fill improvement (price improvement) is modeled.

UNFILLABLE causes `passes_gate=False` regardless of EV.

---

## Scope

This is a **research/paper model**. It provides:
- Realistic cost estimates for backtesting and paper trading
- A framework for understanding where theoretical EV goes in real markets
- Calibration of minimum edge thresholds (how much theoretical EV do we need to survive friction?)

It does **not** provide:
- Real CLOB order routing
- Actual execution instructions
- Live order book depth feeds
- Dynamic slippage calibration from real fills

---

## Known Approximations and Limitations

1. **Static slippage buckets** — The liquidity bucket thresholds (100k, 20k, 5k, 1k) are fixed estimates, not market-fitted. Real Polymarket markets vary.

2. **Constant base slippage** — YES=NO=0.003. In practice YES and NO order books on the same market have different depths.

3. **Fill simulation is binary** — PARTIAL is always 90%, not order-book-aware. Real partial fills depend on book depth at each price level.

4. **Fee is constant** — `fee_pct` is a config constant. Polymarket taker fees may vary by market or volume tier.

5. **No adverse selection model** — Large orders on thin markets may move the price before fill. This impact is not modeled.

6. **Single-point staleness** — The model uses snapshot age as a proxy for information decay. Real information half-life depends on the specific market's volatility.

---

## Deferred

The following are explicitly out of scope for this implementation:

- **Real CLOB fill data integration** — connecting to actual Polymarket CLOB API to observe fill rates
- **Dynamic slippage calibration** — learning bucket thresholds from historical fill data
- **Adaptive threshold learning** — adjusting `required_threshold` based on realized vs estimated EV
- **Multi-leg execution** — simultaneous YES/NO orders or hedging
- **Gas/network cost modeling** — Polygon transaction costs are not included
- **Market-specific liquidity models** — different thresholds for different market types
