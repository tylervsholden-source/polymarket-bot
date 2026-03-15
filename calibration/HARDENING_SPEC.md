# Calibration Hardening Spec

## 1. bridge_intent_side — Literal["YES", "NO"]

`CalibratedSignal.bridge_intent_side` is typed as `Literal["YES", "NO"]`.

**Why strict:** The calibration decision layer (step 8 in `decide()`) checks `intent not in ("YES", "NO")` and rejects with `AMBIGUOUS_MAPPING`. Any value outside this exact set — including lowercase "yes"/"no", "MAYBE", empty string, or None — is treated as an ambiguous mapping and the trade is rejected. This prevents silent misconfiguration from the bridge layer from reaching the order placement logic with an undefined side.

**Contract:** `probability_mapper.map_to_event_probability()` is the only function that sets this field. It derives the value exclusively from `polarity` + `predicted_class`. No caller may inject an arbitrary string.

---

## 2. SUPPORTED_HORIZONS = frozenset({5, 15})

Only 5-minute and 15-minute signal horizons are accepted by `decide()` (step 4). Any other value — including 1, 10, 30, 60, 120 — is rejected with `UNSUPPORTED_HORIZON`.

**Why these only:**
- The execution cost model (staleness thresholds, slippage calibration) has been parameterized and validated only for 5m and 15m horizons.
- Staleness zones (`STALENESS_5M`, `STALENESS_15M`) are concrete data structures. There is no interpolation or extrapolation for other horizons.
- Expanding SUPPORTED_HORIZONS requires adding new staleness threshold objects and re-calibrating the cost model — this is an explicit design gate, not an oversight.

**Why frozenset:** Immutable at runtime. Import-time constant. Cannot be monkey-patched accidentally.

---

## 3. Live vs Paper Contract Differences

See `LIVE_VS_PAPER_POLICY.md` for the full policy matrix.

Summary:
- `LIVE_CAL_CONFIG` enforces stricter quality gates and a higher edge threshold.
- `PAPER_CAL_CONFIG` is permissive for research purposes — UNKNOWN/WEAK calibration passes.
- The separation exists so paper trading can explore signal quality data without gatekeeping that would prevent learning.

---

## 4. Probability Validity Rules

### PROB_MIN_SUM = 0.50
If `calibrated_up_prob + calibrated_down_prob + calibrated_no_trade_prob < 0.50`, the probability space is under-defined.

**What "under-summed" means:** The model's stated probabilities account for less than 50% of outcomes. This cannot be a well-calibrated distribution — it implies either missing class outputs or severe numerical error. The calibration layer cannot safely compute edge from such a distribution and rejects with `INCONSISTENT_PROBS`.

**Rationale for 0.50 floor (not 1.0):** `NO_TRADE` mass can be large. A model that says "UP=0.55, DOWN=0.15, NO_TRADE=0.30" is valid (total=1.00). A model that says "UP=0.72, DOWN=0.18, NO_TRADE=0.10" is also valid (total=1.00). The floor of 0.50 handles intentional partial outputs (e.g., only UP and DOWN provided, NO_TRADE omitted: 0.72+0.18=0.90 ≥ 0.50 → valid).

### PROB_MAX_OVER = 0.01
If total > 1.01, the probabilities are inconsistent — they exceed unity beyond floating-point rounding tolerance. Rejected with `INCONSISTENT_PROBS`.

**Tolerance rationale:** 0.01 absorbs float rounding from isotonic regression and Platt scaling without masking real errors. A total of 1.003 from rounding is fine; a total of 1.02 from a bug is not.

---

## 5. min_execution_adjusted_edge — Naming Rationale

This field name was chosen to be maximally unambiguous:

- `min_` — it is a lower bound (floor), not a target.
- `execution_adjusted_` — the value has already had taker fee and slippage deducted. It is not a gross or net EV; it is the realistic post-friction value.
- `edge` — final decision metric; the name `edge` alone (without prefix) was avoided because it is overloaded in trading literature.

The full computation chain:
```
gross_EV           = calibrated_prob - ask_price
net_EV             = gross_EV - taker_fee
execution_adjusted = net_EV - slippage
```

`execution_adjusted_ev >= min_execution_adjusted_edge` is the final gate (step 11 in `decide()`).

---

## 6. Intentionally Deferred

The following are **not** enforced by current guardrails:

### Execution Realism
- `execution_realism/` module computes a more detailed cost model (slippage by liquidity bucket, staleness penalties, fill simulation) but this is not yet wired into `decide()`. The calibration layer still uses `assumed_slippage_pct` as a constant.
- Integration: `ExecutableCostBreakdown` is available as an optional field on `TradeDecision` but is not required by any guardrail.

### Calibration Lifecycle
- No automatic re-calibration trigger. `CalibrationQuality` is set externally by the model trainer. The decision layer enforces quality gates but does not manage the training pipeline.
- No sample count enforcement at decision time (only at training time).

### Market Templates
- Polarity detection is done by the bridge layer (`signal_bridge/market_matcher.py`). The calibration layer accepts the polarity result as a string and trusts it. No calibration-layer contract validates whether the polarity was correctly inferred from the market question text.
- Extending to non-crypto-directional markets (sports, politics, etc.) is out of scope and explicitly filtered at the market scanner level.

### Dynamic Threshold Adaptation
- `min_execution_adjusted_edge` is a static config value. There is no Bayesian threshold adaptation or regime-aware adjustment. This is deferred.

### Horizon-Aware Slippage Calibration
- Current slippage is a constant per config. Horizon-aware slippage (5m signals may need tighter slippage models than 15m) is deferred to Phase 8+.
