# Shadow Validation Gate

**Module:** `shadow_runner/validation.py`

## Purpose

Before readiness review is allowed, the shadow corpus must meet minimum
evidence requirements. This gate answers:

> "Do we have enough data to meaningfully evaluate the system?"

## Requirements (EvidenceRequirements dataclass)

| Field | Default | Rationale |
|---|---|---|
| min_live_like_evaluated | 100 | ±10pp CI (95%) on 30% rejection rate |
| min_live_like_executes | 20 | Min to see meaningful EV distribution |
| min_live_like_rejects | 20 | Min to see rejection reason composition |
| min_assets_covered | 1 | One asset (e.g., BTC) |
| min_horizons_covered | 1 | One horizon (e.g., 15m) |
| min_observation_days | 5.0 | Captures weekday/weekend variation |
| live_like_profile | "live" | Reference profile |

All thresholds are documented in module docstring rationale.

## Evidence Source Filter

Only `evidence_source == "live_shadow"` records are counted.
`"demo"` and `"synthetic"` records are explicitly excluded.

**A corpus of only test fixtures or demo runs MUST NOT satisfy this gate.**

## Output: EvidenceSufficiencyResult

```python
EvidenceSufficiencyResult:
  sufficient: bool        # True iff all gaps are empty
  gaps: list[EvidenceGap] # list of unmet requirements
  live_like_evaluated: int
  live_like_executes:  int
  live_like_rejects:   int
  assets_covered:      list[str]
  horizons_covered:    list[int]
  observation_days:    float
  requirements_used:   EvidenceRequirements
```

## What Happens When Insufficient

`assess_readiness()` short-circuits immediately:
→ `ReadinessVerdict.INSUFFICIENT_EVIDENCE`
→ No behavioral checks are run
→ `verdict_reason` lists all unmet gaps

This is NOT a NO_GO. It means: "not enough data yet — keep collecting."

## Current State (2026-03-15)

Shadow runner integrated as of commit `1115cb3`.
Records accumulating since integration.
As of snapshot: ~60 live_like records, ~1 observation day.
Requirements: 100 records, 5 days.
Expected to reach sufficiency: ~4 more days of operation.
