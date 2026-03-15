# Execution Cost Model

## Full Cost Formula

```
theoretical_hold_ev = calibrated_probability - ask_price

total_friction = fee_pct
              + slippage.total_slippage
              + staleness.penalty

executable_ev  = theoretical_hold_ev - total_friction

passes_gate    = (executable_ev >= required_threshold)
                 AND (NOT staleness.should_reject)
                 AND (fill_decision != UNFILLABLE)
```

Where `slippage.total_slippage = base_slippage + liquidity_penalty + size_penalty`.

---

## Slippage Components

### Base Slippage (by side)

| Side | Base Slippage |
|------|---------------|
| YES  | 0.003 (0.3%)  |
| NO   | 0.003 (0.3%)  |

Both sides use the same base. This reflects minimum CLOB friction; the symmetry is a simplifying assumption.

### Liquidity Penalty (by bucket)

| Liquidity (USDC) | Bucket    | Penalty |
|------------------|-----------|---------|
| > 100,000        | very_high | 0.001   |
| > 20,000         | high      | 0.002   |
| > 5,000          | medium    | 0.005   |
| > 1,000          | low       | 0.010   |
| ≤ 1,000          | very_low  | 0.020   |

Larger markets have lower per-unit slippage because there is more depth to absorb the order.

### Size Penalty (by intended order size)

| Size (USDC) | Penalty |
|-------------|---------|
| ≥ 200       | 0.007   |
| ≥ 50        | 0.003   |
| ≥ 10        | 0.001   |
| < 10        | 0.000   |

Larger orders consume more book depth, increasing average fill price.

---

## Staleness Zone Tables

### 5-minute horizon (tighter thresholds)

| Age (seconds) | Zone    | Penalty | should_reject |
|---------------|---------|---------|---------------|
| ≤ 30          | FRESH   | 0.000   | False         |
| 31 – 90       | AGING   | 0.005   | False         |
| 91 – 180      | STALE   | 0.015   | False         |
| > 180         | EXPIRED | 0.015   | **True**      |

### 15-minute horizon (looser thresholds)

| Age (seconds) | Zone    | Penalty | should_reject |
|---------------|---------|---------|---------------|
| ≤ 60          | FRESH   | 0.000   | False         |
| 61 – 180      | AGING   | 0.003   | False         |
| 181 – 300     | STALE   | 0.010   | False         |
| > 300         | EXPIRED | 0.010   | **True**      |

**Rationale:** A 5-minute signal with a 150-second-old price snapshot has data that covers 50% of the signal horizon. The same age on a 15-minute signal covers only 17%. The tighter 5m thresholds reflect this decay.

---

## Fill Policy Rules

| Condition                              | Decision   | Fill Fraction |
|----------------------------------------|------------|---------------|
| liquidity = 0                          | UNFILLABLE | 0.00          |
| size > 50% of liquidity                | UNFILLABLE | 0.00          |
| size > 25% and ≤ 50% of liquidity      | PARTIAL    | 0.90          |
| size ≤ 25% of liquidity                | FILLABLE   | 1.00          |

Fill price is always the ask price (conservative; no price improvement modeled).

---

## Example Calculations

### Scenario 1: Good trade — fresh, high liquidity, small size

```
side             = YES
calibrated_prob  = 0.72
ask_price        = 0.44
fee_pct          = 0.01
intended_size    = $10
liquidity        = $50,000
snapshot_age     = 15s
horizon          = 15m

theoretical_hold_ev = 0.72 - 0.44 = 0.280

slippage:
  base              = 0.003
  liquidity_bucket  = high (50k > 20k)  → penalty = 0.002
  size_penalty      = 0.001 (10 >= 10)
  total_slippage    = 0.006

staleness (15m, 15s):
  zone = FRESH → penalty = 0.000

fill:
  ratio = 10/50000 = 0.02% → FILLABLE

total_friction = 0.01 + 0.006 + 0.000 = 0.016
executable_ev  = 0.280 - 0.016 = 0.264
passes_gate    = 0.264 >= threshold (e.g. 0.03) → True
```

### Scenario 2: Aging snapshot penalty

```
same as above, but snapshot_age = 120s, horizon = 5m

staleness (5m, 120s):
  90 < 120 ≤ 180 → STALE → penalty = 0.015
  should_reject = False

total_friction = 0.01 + 0.006 + 0.015 = 0.031
executable_ev  = 0.280 - 0.031 = 0.249
passes_gate    = True (still above threshold)
```

### Scenario 3: Expired snapshot — forced reject

```
snapshot_age = 200s, horizon = 5m

staleness (5m, 200s):
  200 > 180 → EXPIRED → penalty = 0.015, should_reject = True

executable_ev  = 0.280 - (0.01 + 0.006 + 0.015) = 0.249
passes_gate    = False  ← staleness.should_reject overrides positive EV
```

### Scenario 4: Low liquidity, large size — thin market

```
calibrated_prob = 0.65
ask_price       = 0.50
intended_size   = $300
liquidity       = $500
snapshot_age    = 10s
horizon         = 15m
fee_pct         = 0.01

theoretical_hold_ev = 0.65 - 0.50 = 0.150

slippage:
  base              = 0.003
  liquidity_bucket  = very_low (500 ≤ 1000) → penalty = 0.020
  size_penalty      = 0.007 (300 >= 200)
  total_slippage    = 0.030

fill:
  ratio = 300/500 = 60% > 50% → UNFILLABLE

total_friction = 0.01 + 0.030 + 0.000 = 0.040
executable_ev  = 0.150 - 0.040 = 0.110
passes_gate    = False  ← fill=UNFILLABLE overrides positive EV
```

---

## Comparison: Constant vs Model Slippage

The calibration layer's `assumed_slippage_pct` is a **constant** (default 0.005). It applies the same cost regardless of market conditions.

The execution realism model replaces this with:

| Market Condition | Constant Slippage | Model Slippage |
|------------------|-------------------|----------------|
| $100k liquidity, $10 order | 0.005 | 0.004 (better) |
| $5k liquidity, $10 order   | 0.005 | 0.009 (worse) |
| $500 liquidity, $10 order  | 0.005 | 0.026 (much worse) |
| $50k liquidity, $300 order | 0.005 | 0.013 (worse) |

The constant slippage is optimistic on thin markets and pessimistic on deep ones. The model slippage is more accurate in both directions, which produces better edge filtering.
