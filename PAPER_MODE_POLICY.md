# Paper Mode Policy
## Phase 12 — Effective 2026-03-15

---

## Three Policy Profiles

Phase 12 introduces an explicit three-way split of policy profiles:

| Profile        | Mode string      | Purpose                                  |
|----------------|------------------|------------------------------------------|
| `LIVE`         | `"live"`         | Real capital deployment — strictest      |
| `PAPER_STRICT` | `"paper_strict"` | Honest research — close to live          |
| `PAPER_LOOSE`  | `"paper_loose"`  | Exploration / diagnostics — permissive   |
| `PAPER` (legacy) | `"paper"`      | Alias for paper_loose behaviour (Phase ≤11) |

---

## Full Comparison Table

| Parameter                       | LIVE    | PAPER_STRICT | PAPER_LOOSE |
|---------------------------------|---------|--------------|-------------|
| **Signal quality**              |         |              |             |
| reject_on_weak_calibration      | True    | True         | False       |
| reject_on_unknown_calibration   | True    | True         | False       |
| require_class_probabilities     | True    | False        | False       |
| min_prob_sum                    | 0.90    | 0.75         | 0.50        |
| **Pricing sanity**              |         |              |             |
| min_ask_sum (ask_yes + ask_no)  | 0.97    | 0.93         | 0.88        |
| max_ask_sum                     | 1.10    | 1.15         | 1.25        |
| check_bid_overround             | True    | True         | False       |
| max_bid_sum (bid_yes + bid_no)  | 1.00    | 1.00         | N/A         |
| min_single_ask                  | 0.05    | 0.03         | N/A         |
| allow_suspicious_underround     | False   | False        | True        |
| **Staleness**                   |         |              |             |
| max_snapshot_age_seconds        | 60      | 120          | 300         |
| **Edge threshold**              |         |              |             |
| min_execution_adjusted_edge     | 0.030   | 0.025        | 0.020       |
| **Fill behaviour**              |         |              |             |
| PARTIAL fill treatment          | REJECT  | EV penalty   | EV penalty  |
| **Size**                        |         |              |             |
| intended_size_usdc required     | Yes     | No           | No          |

Hard structural limits (INVALID_PRICING) apply to all modes equally — see PRICING_SANITY_SPEC.md.

---

## What Each Profile Is For

### LIVE

Actual money is at risk. Every check is at maximum strictness.

- Calibration MUST be strong (PLATT or ISOTONIC, ECE < 0.10)
- class_probabilities MUST be present — raw_confidence proxy forbidden
- Probability mass MUST be near-complete (sum ≥ 0.90)
- Pricing MUST be fresh (≤ 60s old)
- Ask-sum MUST be in [0.97, 1.10] — suspicious underround is REJECTED
- PARTIAL fills are REJECTED outright — residual risk unacceptable for real capital
- Caller MUST pass explicit `intended_size_usdc` — no silent default

Interpretation: a LIVE EXECUTE_YES decision means the system is willing to commit
real capital. Every filter that can be tightened has been tightened.

### PAPER_STRICT

Honest research. Used to evaluate strategy quality before going live.

- Same calibration quality requirements as live (WEAK and UNKNOWN rejected)
- Probability mass ≥ 0.75 (slightly looser than live to allow research conditions)
- class_probabilities not required (research may use proxy signals)
- Pricing must be ≤ 120s old
- Ask-sum must be in [0.93, 1.15] — suspicious underround REJECTED
- PARTIAL fills allowed with 50bps EV penalty

Interpretation: if a trade fails PAPER_STRICT, it will almost certainly fail LIVE.
PAPER_STRICT results can be used to inform go-live decisions. Results that only
appear in PAPER_STRICT but not LIVE typically indicate edge_threshold or size differences.

### PAPER_LOOSE

Exploratory / observational. Used for diagnostics, market scanning, hypothesis testing.

- WEAK and UNKNOWN calibration allowed — useful when exploring signal quality
- class_probabilities not required
- Probability mass ≥ 0.50 — sparse signals permitted
- Pricing up to 300s old
- Ask-sum [0.88, 1.25] — suspicious underround is **annotated** but NOT rejected
  (pricing_sanity_notes field set on TradeDecision)
- bid_sum NOT checked
- PARTIAL fills allowed with 50bps EV penalty

Interpretation: PAPER_LOOSE results cannot be used to justify live deployment.
A trade that only passes in PAPER_LOOSE is a research observation, not a signal.

---

## Why Paper_Loose Must Not Flatter the Strategy

Paper_loose is designed to let through as much as possible so researchers can
observe what the system *would* do under various conditions. This permissiveness
is a feature for exploration, but it creates a trap:

**If you optimize a strategy on paper_loose results, you are optimizing for
decisions that would be rejected in live mode.**

Common self-deception patterns:
1. Observing high win-rate in paper_loose → assuming live will perform similarly
2. Ignoring pricing_sanity_notes on EXECUTE decisions → treating suspicious markets as real edge
3. Using paper_loose as a "dry run" → it is not — it allows many things live rejects

The correct workflow:
```
paper_loose  → explore and understand signal behavior
paper_strict → validate that apparent edge survives realistic conditions
live         → deploy only after paper_strict results are satisfactory
```

---

## How to Interpret Paper Results Honestly

### pricing_sanity_notes

If `result.pricing_sanity_notes is not None`, the trade passed in paper_loose
despite suspicious pricing. The field content describes exactly what was suspicious.

```python
if result.pricing_sanity_notes is not None:
    # This trade only passed because pricing was suspicious.
    # Do NOT count this as a valid signal.
    log.warning("Suspicious pricing annotated: %s", result.pricing_sanity_notes)
```

### policy_mode

Every `TradeDecision` carries `policy_mode`. Always check it:

```python
if result.decision == TradeDecisionType.EXECUTE_YES:
    if result.policy_mode == "paper_loose":
        # Observational only — do not size up
    elif result.policy_mode == "paper_strict":
        # Can inform live strategy — treat as honest estimate
    elif result.policy_mode == "live":
        # Ready for execution — all filters satisfied
```

### Comparing Paper_Strict vs Paper_Loose

If the same scenario produces different outcomes in paper_strict vs paper_loose,
the difference tells you *why* the loose result passed:

- Paper_strict rejects WEAK calibration → paper_loose allows it → signal quality issue
- Paper_strict rejects ask_sum=0.92 → paper_loose annotates it → pricing quality issue
- Paper_strict rejects age=150s → paper_loose allows it → data freshness issue

---

## Importing Policy Configs

```python
from calibration.types import (
    LIVE_CAL_CONFIG,           # production
    PAPER_STRICT_CAL_CONFIG,   # honest research
    PAPER_LOOSE_CAL_CONFIG,    # exploration
    PAPER_CAL_CONFIG,          # legacy alias → paper_loose behaviour
)
```

See also: `config/policies.py` for the full threshold table as code comments.

---

## Policy Choices Made Explicit

1. **paper_strict rejects WEAK calibration** (same as live).
   Rationale: a strategy that requires weak calibration to look profitable is
   not a good strategy. Paper_strict is supposed to be honest, not flattering.

2. **paper_loose allows suspicious underround but annotates it**.
   Rationale: completely blocking suspicious underround in exploratory mode would
   prevent studying why certain markets appear interesting. The annotation makes
   the suspicion explicit in the output so it can't be silently ignored.

3. **paper_loose does not check bid_sum**.
   Rationale: exploratory mode may scan thin markets where bid_sum > 1.00 occurs
   due to data quality. Rejecting these in exploratory mode reduces observability.
   Live and paper_strict always check bid_sum.

4. **max_snapshot_age differs: 60s / 120s / 300s**.
   Rationale: live trades at the 60s mark are already 60s stale at execution.
   In a 5-minute horizon, 60s is 20% of the signal window — too much for real capital.
   Paper_loose allows 300s to support batch evaluation scenarios where data freshness
   is not the focus of the study.

5. **edge thresholds differ: 0.030 / 0.025 / 0.020**.
   Rationale: live requires higher certainty of edge to compensate for execution risk
   not fully captured in the model. Paper_loose uses the minimum viable threshold
   for exploratory observation.
