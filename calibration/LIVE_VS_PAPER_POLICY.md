# Live vs Paper Policy Matrix

## Overview

Two calibration configurations are defined in `calibration/types.py`:
- `LIVE_CAL_CONFIG` — enforces strict quality and financial risk gates for real-money trading.
- `PAPER_CAL_CONFIG` — permissive for research and simulation; allows UNKNOWN/WEAK calibration through.

Both configs use the same `decide()` function in `calibration/decision_policy.py`. The difference is entirely in configuration values — there is no separate code path.

---

## Policy Matrix

| Gate (decide() step) | LIVE_CAL_CONFIG | PAPER_CAL_CONFIG | Reason for Difference |
|---|---|---|---|
| **Step 1: WEAK calibration** | REJECT (`reject_on_weak_calibration=True`) | PASS (`False`) | Live: weak ECE signals unreliable probability estimates; real money at stake. Paper: need data to improve calibration. |
| **Step 2: UNKNOWN calibration** | REJECT (`reject_on_unknown_calibration=True`) | PASS (`False`) | Live: raw confidence proxy is not a calibrated probability; edge estimate is meaningless. Paper: exploring signals before calibration data exists. |
| **Step 3: class_probabilities** | REQUIRED (`require_class_probabilities=True`) | NOT REQUIRED (`False`) | Live: raw_confidence fallback forbidden — it is a scalar max(predict_proba), not a calibrated distribution. Paper: allows testing with older model outputs. |
| **Step 4: horizon** | {5, 15} only (shared) | {5, 15} only (shared) | Shared constraint: staleness and cost models only validated for these horizons. |
| **Step 5-6: pricing validation** | Same as paper | Same as live | Structural integrity check — no config difference. |
| **Step 7: min_liquidity** | 1,000 USDC (shared default) | 1,000 USDC (shared default) | Same floor; market depth required for both modes. |
| **Step 8: bridge_intent_side** | Literal["YES","NO"] (shared) | Literal["YES","NO"] (shared) | Side ambiguity is a data integrity issue in both modes. |
| **Step 9: spread** | max 0.05 (shared default) | max 0.05 (shared default) | Spread cost is real in paper too (for accurate simulation). |
| **Step 10: min_calibrated_confidence** | 0.55 (shared default) | 0.55 (shared default) | Below 0.55 the effective probability is near coin-flip level. |
| **Step 11: edge threshold** | `min_execution_adjusted_edge=0.03` | `min_execution_adjusted_edge=0.02` | Live: 3% minimum accounts for real execution noise above model; Paper: 2% floor allows more signals through for evaluation. |

---

## Exact Threshold Values

### LIVE_CAL_CONFIG
```python
reject_on_unknown_calibration = True
reject_on_weak_calibration    = True
min_execution_adjusted_edge   = 0.03   # 3% post-friction edge minimum
require_class_probabilities   = True
assumed_taker_fee_pct         = 0.01   # shared
assumed_slippage_pct          = 0.005  # shared (constant fallback)
min_calibrated_confidence     = 0.55   # shared
min_liquidity                 = 1000.0 # shared
max_spread_yes                = 0.05   # shared
max_spread_no                 = 0.05   # shared
max_snapshot_age_seconds      = 300    # shared (5 min)
```

### PAPER_CAL_CONFIG
```python
reject_on_unknown_calibration = False
reject_on_weak_calibration    = False
min_execution_adjusted_edge   = 0.02   # 2% post-friction edge minimum
require_class_probabilities   = False
# All other fields: same as CalibrationConfig defaults
```

---

## Why Each Difference Exists

### reject_on_weak_calibration (live=True, paper=False)
A WEAK calibration (ECE ≥ 0.10) means the model's stated confidence of 0.72 might correspond to a true win rate of 0.60. An edge estimate built on this is systematically off. In paper mode this miscalibration is useful to measure and improve; in live mode it translates directly to financial loss.

### reject_on_unknown_calibration (live=True, paper=False)
UNKNOWN quality means the system is using `raw_confidence` (the max class probability from `predict_proba`) as the event probability. This is not a calibrated estimate — it is the classifier's discrimination score. Polymarket prices already embed market-consensus probabilities; using a raw discriminator score as P(event) produces unreliable edge estimates. Paper mode allows this for exploration but it must not reach real orders.

### require_class_probabilities (live=True, paper=False)
The calibration mapper needs `class_probabilities = {"UP": p1, "DOWN": p2, "NO_TRADE": p3}` to correctly validate the probability space (sum checks, PROB_MIN_SUM floor). Without this dict the system uses raw_confidence as a proxy. In live mode this proxy is explicitly forbidden; the model must provide full class probability output.

### min_execution_adjusted_edge (live=0.03, paper=0.02)
The 1% difference (3% vs 2%) accounts for execution risk that is harder to model:
- Real CLOB fill uncertainty beyond the slippage model.
- Adversarial pricing around signal timestamps.
- Latency between signal generation and order placement.

Paper simulation does not have these risks, so the 2% threshold is appropriate to observe more trade opportunities for evaluation. Live trading requires the extra buffer.

---

## Shared Constraints (same in both modes)

These are not config-dependent — they are structural integrity requirements:

1. `SUPPORTED_HORIZONS = {5, 15}` — cost models only exist for these horizons.
2. Pricing structural validation (prices in [0,1], ask >= bid, no future timestamps).
3. `bridge_intent_side` must be "YES" or "NO" exactly.
4. Probability sum rules (PROB_MIN_SUM=0.50, PROB_MAX_OVER=0.01).

---

## How to Switch

```python
from calibration.types import LIVE_CAL_CONFIG, PAPER_CAL_CONFIG
from calibration.decision_policy import decide

# Paper mode
decision = decide(cal_signal, pricing, config=PAPER_CAL_CONFIG)

# Live mode
decision = decide(cal_signal, pricing, config=LIVE_CAL_CONFIG)
```

Do not use `DEFAULT_CAL_CONFIG` in production. It has `reject_on_unknown_calibration=False` (same as paper) and `min_execution_adjusted_edge=0.02`. It exists for backward compatibility in tests and development only.
