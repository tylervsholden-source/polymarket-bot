# Contract Hardening Specification
## Phase 10 — Effective 2026-03-15

This document describes the exact contract rules enforced at each layer of the
decision system. It reflects **actual code behavior** — not aspirational policy.

---

## 1. Live vs Paper Contract Differences

| Contract                  | Live (`LIVE_CAL_CONFIG`)           | Paper (`PAPER_CAL_CONFIG`)          |
|---------------------------|-------------------------------------|--------------------------------------|
| `intended_size_usdc`      | **Required** — None raises ValueError | Optional — None → 20.0 USDC default |
| Calibration quality       | STRONG only (WEAK/UNKNOWN → REJECT) | Any quality accepted                 |
| `class_probabilities`     | **Required** — None → REJECT        | Optional                             |
| `min_prob_sum`            | **0.90** (near-complete mass)        | 0.50 (sparse distributions allowed) |
| Edge threshold            | 0.03 (3%)                            | 0.02 (2%)                            |
| Calibration method        | Any (PLATT/ISOTONIC recommended)    | Any including IDENTITY               |

---

## 2. Explicit Size Requirement in Live Mode

**Why required:** Silently sizing at 20 USDC in a live context is dangerous.
The intended position size affects slippage, fill simulation, and execution cost.
Allowing a default hides the caller's failure to specify this critical parameter.

**Contract:**
```python
# This raises ValueError immediately:
decide(cal_signal, pricing, config=LIVE_CAL_CONFIG, now_utc=now)
# ↑ intended_size_usdc=None (default) → ValueError

# This is valid:
decide(cal_signal, pricing, config=LIVE_CAL_CONFIG, now_utc=now,
       intended_size_usdc=50.0)
```

**Paper / Default behavior:**
```python
# None → silently uses 20.0 USDC. Documented here, not silent.
decide(cal_signal, pricing, config=PAPER_CAL_CONFIG, now_utc=now)
# intended_size_usdc_used = 20.0 in TradeDecision audit trail
```

---

## 3. Probability Boundary Policy

### At the mapper (`map_to_event_probability`):
- Any individual prob < 0 or > 1 → `INCONSISTENT_PROBS`
- Total > 1 + `PROB_MAX_OVER` (0.01) → `INCONSISTENT_PROBS`
- Total < `PROB_MIN_SUM` (0.50) → `INCONSISTENT_PROBS`

### At the decision gate (`decide()` step 4) — **independent of mapper**:
- Same individual-prob checks (defense in depth)
- Total > 1.01 → `INCONSISTENT_PROBS`
- Total < `config.min_prob_sum` → `INCONSISTENT_PROBS`
  - Live: `min_prob_sum = 0.90` → distributions like (0.72, 0.18, 0.00) = 0.90 ✓ pass
  - Live: (0.60, 0.12, 0.00) = 0.72 ✗ REJECT
  - Paper: `min_prob_sum = 0.50` → (0.60, 0.12, 0.00) = 0.72 ✓ pass

**Rationale for 0.90 live threshold:**
Under-summed distributions indicate a raw-confidence proxy is being used
(not a full softmax output). In live mode this is forbidden — the edge
calculation is only trustworthy with near-complete probability mass.

**What is NOT done:**
Malformed live probabilities are NOT silently normalized. 0.72 total is not
rescaled to 1.0. It is rejected.

---

## 4. Supported Horizons Policy

**Supported:** `{5, 15}` minutes (constant `SUPPORTED_HORIZONS`).

**Enforcement layers (defense in depth):**

| Layer | Where | Mechanism |
|-------|-------|-----------|
| 1 | `DirectionalSignal.__post_init__` | `ValueError` at signal construction |
| 2 | `decide()` step 5 | `UNSUPPORTED_HORIZON` rejection |

**Paper mode:** Still rejects unsupported horizons. There is no "observation-only"
bypass — horizon constraints apply regardless of mode.

**Future horizons:** Only added by updating `SUPPORTED_HORIZONS` constant in
`calibration/types.py`. The execution realism staleness model also needs
corresponding `StalenessThresholds` entries.

---

## 5. Type Contract Enforcement

### `RawSignalOutput.__post_init__` validates:
- `predicted_class` ∈ `{"UP", "DOWN", "NO_TRADE"}` — ValueError otherwise
- `raw_confidence` ∈ [0, 1] — ValueError otherwise

### `CalibratedSignal.__post_init__` validates:
- `bridge_intent_side` ∈ `{"YES", "NO"}` — ValueError otherwise
  - "MAYBE", "", "yes", "no", "REJECT" all fail
  - `dataclasses.replace()` also triggers validation
- `effective_yes_prob` ∈ [0, 1] — ValueError
- `effective_no_prob` ∈ [0, 1] — ValueError

### `CalibrationConfig.__post_init__` validates:
- `min_calibrated_confidence` ∈ [0, 1]
- `min_execution_adjusted_edge` ≥ 0
- `assumed_taker_fee_pct` ∈ [0, 1)
- `min_prob_sum` ∈ [0, 1]

### `MarketPricingSnapshot.__post_init__`:
- `spread_yes = ask_yes - bid_yes` (derived, cannot be injected)
- `spread_no  = ask_no  - bid_no`  (derived, cannot be injected)
- Any externally-passed spread value is silently overridden

### `DirectionalSignal.__post_init__`:
- `horizon_minutes` ∈ `SUPPORTED_HORIZONS` — ValueError otherwise

---

## 6. Config Source of Truth

**Python constants are authoritative.** See `config/loader.py`.

`config/live.yaml` and `config/paper.yaml` were removed in Phase 10.
They were decorative (never loaded). Editing them had zero effect.

---

## 7. What Remains Intentionally Deferred

| Feature | Deferred to Phase |
|---------|-------------------|
| Shadow runner (live data, paper decisions) | Phase 13 |
| Calibration lifecycle (drift, re-fit) | Phase 14 |
| Live order placement | Phase 15 |
| Widening supported horizons | Not planned |
| Kelly position sizing | Not in calibration layer |
