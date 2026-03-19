# Contract Hardening Spec

**Phase:** 14 (current)
**Reference commit:** `22ffcc7`

## Changes Made in Phase 14

### 1. 1-FAIL → NO_GO (readiness.py)

Before: 1 FAIL → CONDITIONAL_REVIEW, 2+ FAILs → NO_GO
After: ANY FAIL (≥1) → NO_GO

Rationale: A single behavioral failure is a meaningful signal that the system
is not ready. Allowing conditional review with a FAIL was too permissive.

### 2. Evidence Minimums Raised (validation.py)

| Parameter | Before | After |
|---|---|---|
| min_live_like_evaluated | 50 | 100 |
| min_live_like_executes | 10 | 20 |
| min_live_like_rejects | — | 20 |
| min_observation_days | 3 | 5 |

Rationale:
- 50 records gave ±14pp CI — too wide
- 100 records gives ±10pp CI at 95% confidence
- 5 days captures weekday/weekend variation

### 3. evidence_source Gate (validation.py + journal.py)

Only `evidence_source = "live_shadow"` records count as valid evidence.
`"demo"` and `"synthetic"` records explicitly excluded.

Prevents a corpus of test fixtures from satisfying the readiness gate.

### 4. missing_required Cap (readiness.py)

If `strict_metrics` or `regime_review` not provided:
- Behavioral checks may pass
- But verdict capped at CONDITIONAL_REVIEW
- Cannot issue TINY_PILOT_CANDIDATE without both inputs

Rationale: GO requires evidence of paper_strict behavior and regime stability.

### 5. Git Freeze Marker

Commit `22ffcc7` tagged as v1 snapshot in `artifacts/v1_snapshot_commit.txt`.
Shadow evidence collected after this commit is the reference corpus.

## Test Coverage

```
tests/test_live_pilot_readiness.py
  TestAnyFailNoGo.test_single_fail_produces_no_go  ← Phase 14
  TestAnyFailNoGo.test_multiple_fails_no_go
  TestMissingRequiredCapsVerdict
  TestInsufficientEvidenceShortCircuit
  ...
```

All 1143 tests pass on current codebase.
