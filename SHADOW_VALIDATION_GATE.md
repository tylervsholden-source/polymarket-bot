# SHADOW_VALIDATION_GATE.md
# Phase 14: Shadow Validation Gate

## What This Gate Is

The Shadow Validation Gate is a formal pre-condition that must be satisfied before any live pilot readiness discussion is permitted. It has two layers:

**Layer 1 — Evidence Sufficiency**
Do we have enough shadow data to evaluate system behavior at all?

**Layer 2 — Behavioral Validation**
Does the observed behavior make economic sense?

Passing both layers produces a ReadinessReport. Failing either produces NO_GO.

---

## Minimum Evidence Requirements

These are enforced by `shadow_runner/validation.py → check_evidence_sufficiency()`.

| Dimension | Minimum | Rationale |
|-----------|---------|-----------|
| Live-like evaluated decisions | 50 | ±14pp CI (95%, binomial) on a 30% rejection rate. Below 50, confidence intervals span [0,1]. |
| Live-like execute decisions | 10 | Minimum to observe EV distribution. Below 10, mean EV is dominated by single outliers. |
| Live-like reject decisions | 20 | Minimum to observe rejection reason composition. Below 20, any single reason can look dominant by chance. |
| Assets covered | 1 | At minimum BTC must be covered. Override to require more assets when multi-asset shadow runs begin. |
| Horizons covered | 1 | At minimum one horizon (15m recommended for first observation). |
| Observation duration | 3 calendar days | A single-session sample cannot distinguish "stable" from "lucky". 3 days exposes temporal variation. |

**Insufficient evidence = automatic NO_GO.** The system will report which dimension(s) are unmet and by how much.

---

## Shadow Validation Metrics

Computed by `shadow_runner/summary_metrics.py → compute_summary_metrics()`.

### 1. Decision Volume Metrics
- `total_evaluated` — total candidates evaluated by the profile
- `execute_count` / `reject_count` — decision split
- `execution_rate` — execute_count / total_evaluated

### 2. Rejection Composition Metrics
- `rejection_counts` — {reason: count} dictionary
- `rejection_rates` — {reason: fraction of total_evaluated}
- Coverage: assets and horizons seen in rejected decisions

### 3. EV Realism Metrics
- `mean_gross_ev` — average calibrated edge before execution friction
- `mean_executable_ev` — average edge after fee + fill discount
- `mean_ev_haircut_abs` — gross - executable (absolute)
- `mean_ev_haircut_pct` — (gross - executable) / gross (fraction consumed by friction)
- `ev_haircut_sample_size` — records contributing to EV stats

### 4. Fillability Metrics
- `full_fill_execute_count` — executes with fill_fraction >= 0.95
- `partial_fill_execute_count` — executes with fill_fraction < 0.95
- `partial_fill_rejected_count` — rejected with reason PARTIAL_FILL_REJECTED
- `partial_fill_rejection_rate` — fraction of all evaluated decisions

### 5. Pricing Sanity Metrics
- `suspicious_underround_count` / `suspicious_underround_rate`
- `stale_pricing_count` / `stale_pricing_rate`
- `annotated_execute_count` — paper_loose executes despite suspicious pricing

### 6. Coverage Metrics
- `assets_seen` — sorted list of asset strings
- `horizons_seen` — sorted list of horizon_minutes
- `ts_first` / `ts_last` / `observation_days`

---

## Healthy vs Unhealthy Shadow Behavior

### GREEN — Healthy

| Signal | Interpretation |
|--------|---------------|
| Execution rate 5–60% | System is selective but finding opportunities |
| EV haircut < 30% | Execution friction is not destroying the edge |
| Underround rate < 10% | Pricing feed is clean |
| Stale pricing rate < 10% | Feed latency is acceptable |
| Partial fill rejection rate < 20% | Size is compatible with liquidity |
| paper_loose vs live divergence < 20pp | Profiles are aligned |
| Stable rolling metrics | No temporal instability detected |

### YELLOW — Warning

| Signal | Interpretation |
|--------|---------------|
| Execution rate < 5% | Over-filtering? Signal quality collapsed? Check thresholds. |
| Execution rate > 60% | Under-filtering? Edge gate too permissive? |
| EV haircut 30–60% | Large fraction of edge lost to friction. Investigate fill model. |
| Underround rate 10–25% | Pricing quality degrading. Monitor feed. |
| Stale pricing rate 10–30% | Feed latency elevated. Check data pipeline. |
| Partial fill rejection rate 20–40% | Size vs liquidity mismatch. Consider reducing size. |
| paper_loose vs live divergence 20–40pp | paper_loose is materially more permissive. Use paper_strict as reference. |

### RED — Fail / Blocker

| Signal | Interpretation |
|--------|---------------|
| Execution rate > 85% | **BLOCKER** — almost nothing is being rejected. Pipeline gates are not functioning. |
| EV haircut > 80% | **BLOCKER** — execution friction consumes >80% of theoretical edge. Pilot trades are economic noise. |
| Underround rate > 40% | **BLOCKER** — majority of opportunities are likely pricing artefacts. |
| paper_loose vs live divergence > 60pp | **BLOCKER** — paper_loose is dangerously misleading. |
| EV haircut 60–80% | **FAIL** — executable EV is < 40% of gross EV. Edge depends on unproven theoretical number. |
| Underround rate 25–40% | **FAIL** — many trades may be pricing errors. |
| Partial fill rejection rate > 40% | **FAIL** — liquidity consistently insufficient. |

---

## What "Insufficient Evidence" Means

Insufficient evidence means the shadow corpus cannot yet distinguish:
- Normal variation from systematic problems
- Lucky early results from sustainable performance
- Sample-specific patterns from general behavior

The system will explicitly report `"Insufficient shadow evidence: [gap descriptions]"` rather than silently issuing a readiness assessment on an inadequate sample.

**Do not interpret a small sample of clean results as a green light.**

---

## Code References

| Concept | File |
|---------|------|
| Evidence sufficiency | `shadow_runner/validation.py` |
| Summary metrics computation | `shadow_runner/summary_metrics.py` |
| Individual readiness checks | `monitoring/readiness_checks.py` |
| Readiness assessment | `shadow_runner/readiness.py` |
| Rolling stability | `monitoring/regime_review.py` |
| Tests | `tests/test_shadow_validation_gate.py` |
