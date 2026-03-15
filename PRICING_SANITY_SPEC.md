# Pricing Sanity Specification
## Phase 12 — Effective 2026-03-15

---

## What Binary Market Sanity Means

A Polymarket binary market resolves to YES or NO. It has two tradeable tokens:

- YES token: pays $1.00 if event resolves YES
- NO token:  pays $1.00 if event resolves NO

In a fair, liquid market with a market maker charging a spread:

```
ask_yes + ask_no  ≈  1.00 + overround     (overround = market maker profit margin)
bid_yes + bid_no  ≈  1.00 - spread        (below 1.00 — no risk-free arb)
```

Typical ranges:
- ask_sum ∈ [1.02, 1.10]  — healthy binary market with normal vig
- bid_sum ∈ [0.90, 1.00]  — normal bid-side
- spread per side ∈ [0.01, 0.05]  — reasonable market-maker margin

---

## Why Suspicious Underround Is Dangerous

Underround means `ask_yes + ask_no < 1.00`. Buying both sides costs less than $1.00,
but at resolution one side always pays $1.00. This appears to be risk-free profit.

**This is not real edge. It is a warning sign.**

Possible causes:
1. **Stale data**: one or both sides have stale quotes that haven't been updated
2. **Thin market**: extremely low liquidity on one side, causing quote anomalies
3. **Data error**: feed or API returned corrupt or cached values
4. **Market close imminence**: quotes drift as market nears resolution
5. **Off-hours gap**: no market makers active, spread has blown out asymmetrically

In all cases, the apparently cheap pricing is not reliably executable. Attempting
to trade on underround pricing typically results in worse actual fills than the
snapshot suggests, or failed transactions.

**The signal from underround pricing is "data quality suspect", not "free money".**

---

## Why High Overround Is Also Suspicious

`ask_sum > 1.10–1.15` (mode-dependent) means the combined vig exceeds reasonable
market-maker margins. This is suspicious because:

1. Thin or off-hours markets have artificially wide spreads
2. Quotes may be stale — the true fair value is unknown
3. Apparent edge disappears entirely when actual execution costs are paid

---

## Hard Structural Limits (All Modes — INVALID_PRICING)

These are structurally impossible market states. If they appear, the data is corrupt.
They apply regardless of policy mode and are caught in `_validate_pricing_snapshot()`.

| Condition                        | Limit     | Rationale                              |
|----------------------------------|-----------|----------------------------------------|
| ask_sum < BINARY_HARD_MIN_ASK_SUM | < 0.85   | Physically impossible pricing          |
| bid_sum > BINARY_HARD_MAX_BID_SUM | > 1.01   | Risk-free arb impossible in real markets |
| ask_yes < bid_yes                 | any       | Inverted spread — data error           |
| ask_no  < bid_no                  | any       | Inverted spread — data error           |
| any price outside [0, 1]          | any       | Invalid probability encoding           |
| timestamp in future               | any       | Snapshot from the future — impossible  |

These produce `CalibrationRejectionReason.INVALID_PRICING`.

---

## Policy-Specific Sanity Checks (SUSPICIOUS_UNDERROUND)

These checks are run after structural validation, in `_check_binary_sanity()`.
They are policy-dependent: live and paper_strict reject; paper_loose annotates only.

### Ask-Sum Band

```
ask_sum = ask_yes + ask_no
```

| Mode         | Minimum | Maximum | Rationale                                   |
|--------------|---------|---------|---------------------------------------------|
| live         | 0.97    | 1.10    | Tight band — only healthy, liquid markets   |
| paper_strict | 0.93    | 1.15    | Moderate — realistic research conditions    |
| paper_loose  | 0.88    | 1.25    | Loose — exploratory, annotated if violated  |

Values below minimum → `SUSPICIOUS_UNDERROUND` (underround detected)
Values above maximum → `SUSPICIOUS_UNDERROUND` (excessive vig / stale market)

### Bid-Sum Ceiling

```
bid_sum = bid_yes + bid_no
```

| Mode         | Maximum | Checked? |
|--------------|---------|----------|
| live         | 1.00    | Yes      |
| paper_strict | 1.00    | Yes      |
| paper_loose  | N/A     | No       |

`bid_sum > max` → `SUSPICIOUS_UNDERROUND` (near risk-free arb territory, data suspect)

### Individual Ask Floor (One-Sided Quote Pathology)

If one side has a very low ask price while the other is near 1.0, the market is
near-certain on one side. This is a legitimate market state near resolution, but
it makes the pricing extremely sensitive to small calibration errors.

| Mode         | min_single_ask | Checked? |
|--------------|----------------|----------|
| live         | 0.05           | Yes      |
| paper_strict | 0.03           | Yes      |
| paper_loose  | N/A            | No       |

Example: `ask_yes=0.03, ask_no=0.98` → asks YES to buy near-certainty at 3¢.
Any calibration error in the 3% range swings the entire expected value.

---

## Where These Checks Live

```
calibration/decision_policy.py
  _validate_pricing_snapshot()  ← hard structural limits (all modes)
  _check_binary_sanity()        ← policy-specific sanity (live/strict reject; loose annotates)

Step 6  → _validate_pricing_snapshot → INVALID_PRICING if hard violation
Step 6b → _check_binary_sanity:
            if mode ∈ {live, paper_strict}: REJECT with SUSPICIOUS_UNDERROUND
            if mode ∈ {paper_loose, paper, default}: set _sanity_note, continue
```

---

## What Remains Heuristic

1. **Thresholds are chosen heuristically**, not derived from market microstructure theory.
   - 0.97 as live min: empirically, real liquid markets rarely have ask_sum < 0.97.
   - 1.10 as live max: markets with > 10% total vig are functionally untradeable.
   - These were chosen to be conservative enough to catch obvious problems without
     over-filtering healthy markets.

2. **Low liquidity does not tighten sanity bands** (Phase 12).
   - Rationale: liquidity is already gated at step 8 (LOW_LIQUIDITY rejection).
   - Interaction effects between thin liquidity and pricing sanity are deferred to Phase 13.

3. **fill_fraction=0.90 for all PARTIAL fills** (Phase 11 carryover).
   - Exact fill fractions within the PARTIAL band are not yet modeled.

4. **bid-sum policy threshold=1.00** is soft: values in (1.00, 1.01] trigger
   `SUSPICIOUS_UNDERROUND`; values > 1.01 trigger `INVALID_PRICING`.
   The gap is intentional — data feeds sometimes report bid_sum slightly above 1.00
   due to rounding. The hard limit catches actual impossible states.

---

## Rejection Reason Taxonomy

| Reason                | Fires when                                     | Mode sensitivity |
|-----------------------|------------------------------------------------|-----------------|
| `INVALID_PRICING`     | Hard structural violation                      | All modes        |
| `SUSPICIOUS_UNDERROUND` | Policy-specific ask/bid band violation       | live, paper_strict |
| `STALE_PRICING`       | Snapshot age > policy max_snapshot_age         | Mode-dependent   |
| `LOW_LIQUIDITY`       | liquidity < config.min_liquidity               | Config-dependent |
| `HIGH_SPREAD`         | spread > config.max_spread_*                   | Config-dependent |

`SUSPICIOUS_UNDERROUND` is economically distinct from `INVALID_PRICING`.
An `INVALID_PRICING` reject means "the data is broken".
A `SUSPICIOUS_UNDERROUND` reject means "the data may be valid but the pricing is
economically suspicious — do not trade on it at this policy level".

---

## Configuration Override

All policy-specific thresholds can be overridden in `CalibrationConfig`:

```python
from calibration.types import CalibrationConfig

custom = CalibrationConfig(
    mode="live",
    min_ask_sum_binary=0.98,    # stricter than default 0.97
    max_ask_sum_binary=1.08,    # stricter than default 1.10
    max_bid_sum_binary=0.99,    # stricter than default 1.00
    min_single_ask_binary=0.06, # stricter than default 0.05
    allow_suspicious_underround=False,
    check_bid_overround=True,
)
```

`None` values use the mode-based defaults from constants in `calibration/types.py`.
