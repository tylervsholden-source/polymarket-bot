# LIVE_PILOT_READINESS.md
# Phase 14: Live Pilot Readiness

## What This Document Is

This document defines the formal criteria for declaring a live pilot ready. It is not a promise of profitability. It is a minimum bar for system behavior coherence under constrained conditions.

**A GO verdict means:** the system's shadow behavior is coherent enough to risk a tiny, heavily constrained live pilot for 3 days.

**A GO verdict does NOT mean:** the strategy is profitable, the edge is real, or the system is ready for general live trading.

---

## The Pass/Warn/Fail Framework

Implemented in `shadow_runner/readiness.py → assess_readiness()`.

### Verdict Levels

| Verdict | Condition | Meaning |
|---------|-----------|---------|
| `GO` | 0 BLOCKERs, 0 FAILs, ≤ 2 WARNs | Tiny constrained pilot may proceed |
| `CONDITIONAL` | 0 BLOCKERs, 1 FAIL or ≥ 3 WARNs | Proceed with heightened caution; address warnings before scaling |
| `NO_GO` | Any BLOCKER or ≥ 2 FAILs | Pilot blocked — do not proceed |

### Criteria Table

| Check | Metric | GREEN | WARN | FAIL | BLOCKER |
|-------|--------|-------|------|------|---------|
| Execution rate | live exec_rate | 5–60% | < 5% or > 60% | — | > 85% |
| EV haircut | (gross - exec) / gross | < 30% | 30–60% | 60–80% | > 80% |
| Suspicious underround | underround / total | < 10% | 10–25% | 25–40% | > 40% |
| Stale pricing | stale / total | < 10% | 10–30% | > 30% | — |
| Partial fill rejection | partial_rej / total | < 20% | 20–40% | > 40% | — |
| paper_loose vs live divergence | loose_exec - live_exec (pp) | < 20pp | 20–40pp | 40–60pp | > 60pp |
| Rejection concentration | top reason / total | < 70% | > 70% | — | — |

---

## Hard Blockers

If any of the following is true, readiness verdict is NO_GO regardless of all other metrics:

1. **Insufficient shadow evidence** — corpus does not meet minimum requirements from SHADOW_VALIDATION_GATE.md
2. **Execution rate > 85%** — pricing, EV, and calibration gates are likely not functioning
3. **EV haircut > 80%** — execution friction consumes > 80% of theoretical edge; pilot trades are economic noise
4. **Suspicious underround rate > 40%** — majority of apparent opportunities are pricing artefacts
5. **paper_loose vs live divergence > 60pp** — paper_loose results are dangerously misleading

Blockers are listed explicitly in `ReadinessReport.blockers`. No amount of good metrics elsewhere overrides a blocker.

---

## Tiny Live Pilot Constraints

These constraints apply to ANY live pilot declared GO. They are pre-defined and frozen. A GO verdict authorizes exactly this pilot — nothing more.

Defined in `shadow_runner/readiness.py → LIVE_PILOT_CONSTRAINTS`.

| Constraint | Value | Rationale |
|-----------|-------|-----------|
| Max open positions | 1 | No portfolio effects; isolated single position |
| Asset | BTC | Most liquid market; most signal coverage |
| Horizon | 15m | More stable than 5m for first pilot |
| Max nominal size | $10 USDC | Minimum meaningful amount; loss is bounded |
| Daily max loss | $5 USDC | 50% of pilot capital; triggers review |
| Single-loss kill | $3 USDC | Any single loss > $3 triggers immediate halt |
| Mandatory review | Every 24 hours | Human must review journal daily |
| Pilot duration | 3 days | First evaluation window |

**GO verdict = GO for this tiny pilot only.**

After the first 3-day pilot, a separate evaluation must occur before any relaxation of these constraints.

---

## Why Readiness Is Not Profitability Proof

The readiness framework checks behavioral coherence, not economic profitability:
- The shadow corpus uses synthetic `ts_recorded_utc` — market outcomes are not observed
- EV is computed from calibrated probabilities, not from realized outcomes
- A system with coherent rejection behavior and reasonable EV haircut may still lose money if the underlying signal is wrong

Phase 14 produces a **behavioral readiness gate**, not a **profit forecast**. The first live pilot is the first step toward actual outcome evidence.

---

## Profile Interpretation Policy

**live (live-like)**
Primary readiness reference. All readiness criteria are evaluated against live profile metrics. This is the only profile that authorizes real capital deployment.

**paper_strict**
Secondary reference. Should be close to live (divergence < 25pp). If paper_strict significantly outperforms live, this is a warning about execution friction, not a signal of live profitability.

**paper_loose**
Exploratory only. **Cannot be used to justify readiness.** If paper_loose execution rate is significantly higher than live, this is expected — not encouraging. The correct response is to ask why live is so much more selective.

The reporting module (`shadow_runner/reporting.py`) explicitly warns when paper_loose executes are significantly above live. This warning is printed in every report.

---

## Code References

| Concept | File |
|---------|------|
| Readiness assessment | `shadow_runner/readiness.py` |
| Individual checks | `monitoring/readiness_checks.py` |
| Pilot constraints | `shadow_runner/readiness.py → LIVE_PILOT_CONSTRAINTS` |
| Tests | `tests/test_live_pilot_readiness.py` |
