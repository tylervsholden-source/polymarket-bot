# Live Pilot Readiness Gate

**Module:** `shadow_runner/readiness.py`
**Verdict enum:** `ReadinessVerdict`

## Four Possible Verdicts

| Verdict | Meaning |
|---|---|
| `TINY_PILOT_CANDIDATE` | All checks pass — constrained pilot may proceed |
| `CONDITIONAL_REVIEW` | Warnings present — human review required first |
| `NO_GO` | Blocker or failure — pilot blocked |
| `INSUFFICIENT_EVIDENCE` | Shadow corpus too small to assess |

## Evidence Requirements (validation.py)

Must be met before any behavioral checks run:

| Requirement | Threshold | Rationale |
|---|---|---|
| live-like evaluated | ≥ 100 | ±10pp CI (95%) on 30% rejection rate |
| live-like executes | ≥ 20 | Minimum for meaningful EV distribution |
| live-like rejects | ≥ 20 | Minimum for rejection reason composition |
| observation days | ≥ 5.0 | Captures weekday/weekend variation |

Only `evidence_source = "live_shadow"` records count.
Synthetic and demo records are excluded.

## Behavioral Checks (readiness_checks.py)

Run against `live_metrics` (live profile records only):

| Check | BLOCKER | FAIL | WARN |
|---|---|---|---|
| execution_rate | — | < 0.05 | < 0.10 |
| ev_haircut_pct | — | > 0.60 | > 0.40 |
| suspicious_underround_rate | > 0.50 | > 0.30 | > 0.10 |
| stale_pricing_rate | — | > 0.30 | > 0.15 |
| partial_fill_rejection_rate | — | > 0.40 | > 0.20 |
| profile_divergence (live vs loose) | — | > 0.50 | > 0.30 |
| rejection_concentration | — | top reason > 0.80 | > 0.65 |
| paper_strict_divergence (optional) | — | > 0.30 | > 0.15 |

## Verdict Logic (Phase 14)

```
if not evidence_sufficient → INSUFFICIENT_EVIDENCE
elif any BLOCKER → NO_GO
elif any FAIL (≥1) → NO_GO          ← Phase 14 change (was: 2+ FAILs)
elif warns ≥ 3 → CONDITIONAL_REVIEW
elif missing strict_metrics or regime_review → CONDITIONAL_REVIEW
else → TINY_PILOT_CANDIDATE
```

## Pilot Constraints (frozen, PilotConstraints dataclass)

If verdict = TINY_PILOT_CANDIDATE, these are the ONLY acceptable conditions:

| Parameter | Value |
|---|---|
| Asset | BTC only |
| Horizon | 15 minutes |
| Max open positions | 1 |
| Max trade size | $10 USDC |
| Daily max loss | $5 USDC |
| Per-trade kill | $3 USDC |
| Mandatory review | every 24 hours |
| Evaluation window | 3 days |

**GO verdict is NOT authorization for general live trading.**
It authorizes a single 3-day, $10-max, BTC-only pilot.

## Daily Review

`monitoring/daily_review.py`:
- Runs `assess_readiness()` with current shadow corpus
- Writes verdict to `data/readiness_verdict.json`
- Must be run manually or scheduled (not automated yet)

Architect Chamber reads `readiness_verdict.json` for the dashboard.
If not run, dashboard shows `NOT_REVIEWED`.
