# Drift Monitoring Specification
## Phase 13 — Effective 2026-03-15

---

## Purpose

Drift monitoring detects when the decision pipeline's behavior changes over time.
Changes may indicate:

1. **Signal degradation** — model is producing weaker signals
2. **Market regime shift** — market conditions have changed; edge has narrowed
3. **Data quality deterioration** — stale pricing or low liquidity increasing
4. **Calibration decay** — previously STRONG calibration drifting to WEAK
5. **Bug introduction** — code change altered decision logic unexpectedly

---

## What Is Monitored

### 1. Rejection Rate per Profile

```
rejection_rate = reject_count / total_count
```

A rising rejection rate in live or paper_strict indicates the market is offering
fewer valid candidates. A falling rejection rate without corresponding EV improvement
suggests the edge threshold was accidentally lowered.

### 2. Rejection Reason Distribution

Within the reject population, the breakdown by `rejection_reason` shows *why*
candidates are being rejected. Shifts in this distribution indicate where the
deterioration is occurring:

| Dominant shift | Likely cause |
|----------------|--------------|
| STALE_PRICING ↑ | Data feed latency or market inactivity |
| WEAK_CALIBRATION ↑ | Model degradation |
| NEGATIVE_EDGE ↑ | Market efficiency increased / edge narrowed |
| SUSPICIOUS_UNDERROUND ↑ | Market quality deteriorating |

### 3. EV Distribution for Executes

```
metric: mean execution_adjusted_ev for EXECUTE decisions
```

A falling mean EV for executes indicates the edges being taken are weakening.
Even if rejection rate is stable, this can signal a regime shift.

### 4. Profile Divergence Rate

```
divergence_rate = 1 - (agreement_count / shared_candidates)
```

For each pair of profiles (live/paper_strict, paper_strict/paper_loose), the fraction
of candidates where both profiles disagree on EXECUTE vs REJECT.

High divergence between live and paper_strict indicates the two profiles are seeing
different markets — e.g., paper_strict's lower ask_sum threshold is routing around
many live rejections, inflating paper_strict's execution rate artificially.

---

## Detection Method

### Baseline vs Current Window

DriftMonitor compares a **baseline window** (reference distribution) to a
**current window** (recent records).

```
baseline window: records at construction time (typically last N=200 records)
current window:  records passed to DriftMonitor.check() (typically last M=50 records)
```

Baseline is fixed at construction. To update, create a new DriftMonitor.

### Alert Thresholds

All thresholds are absolute percentage-point changes (current - baseline).

| Metric | Warning | Critical |
|--------|---------|----------|
| Rejection rate delta | ±15pp | ±30pp |
| EV mean drop | -0.005 | -0.015 |
| Profile divergence rate | 20% | 40% |

Rationale for absolute thresholds over statistical tests:
- Rejection rates are not normally distributed in short-horizon crypto markets
- Statistical power is low for the window sizes used (10-200 records)
- Practitioners can interpret percentage-point changes directly

Minimum window size for alert triggering: 10 records.
Below this, alerts are suppressed (insufficient data).

---

## Alert Levels

### WARNING

Drift is notable and warrants review. A single WARNING does not require immediate
action but should be investigated within the current session.

Examples:
- Rejection rate increased from 40% to 57% (Δ=+17pp)
- EV mean dropped from 0.035 to 0.029 (Δ=-0.006)

### CRITICAL

Drift is severe and warrants immediate investigation. Consider pausing execution
until the cause is understood.

Examples:
- Rejection rate jumped from 40% to 75% (Δ=+35pp)
- EV mean dropped from 0.035 to 0.019 (Δ=-0.016)
- Profile divergence between live and paper_strict reached 42%

---

## What Drift Monitoring Does NOT Cover

1. **Per-reason drift alerts** (Phase 13 deferred):
   DriftMonitor tracks total rejection rate, not per-reason breakdown.
   If STALE_PRICING specifically rises while other reasons are stable, no alert fires.
   Planned for Phase 14.

2. **Streaming detection (EWMA)**:
   Current implementation requires explicit window management.
   EWMA would enable continuous monitoring without baseline rotation.
   Planned for Phase 14.

3. **Calibration drift tracking**:
   Tracking ECE or Brier score over time requires calibration metric history.
   The journal stores brier_score and ece if available, but aggregation is not
   yet implemented.

4. **P&L or resolution outcome correlation**:
   Drift monitoring operates on decision outputs, not trade outcomes.
   Correlating EV estimates with actual resolution outcomes requires a separate
   outcome tracking module (Phase 15 scope).

---

## Interpretation Guide

### Rejection rate rising sharply → CRITICAL

```
Baseline: rejection_rate=0.40  (paper_strict)
Current:  rejection_rate=0.72

→ CRITICAL: [paper_strict] rejection rate CRITICAL drift: 40.0% → 72.0% (Δ=+32.0%)
```

Check:
1. Is STALE_PRICING rejection reason increasing? → Data feed issue
2. Is NEGATIVE_EDGE rejection reason increasing? → Edge has narrowed
3. Is WEAK_CALIBRATION increasing? → Model has degraded

### EV dropping but rejection rate stable

```
Baseline: mean_exec_adj_ev=0.038  (live)
Current:  mean_exec_adj_ev=0.028

→ WARNING: [live] EV mean WARNING drop: 0.0380 → 0.0280 (Δ=-0.0100)
```

Interpretation: The pipeline is still executing at a similar rate, but the
edges being taken are weaker. This is a subtle regime shift — the market has
become more efficient and the best available edge has shrunk.

### High divergence between paper_strict and live

```
→ WARNING: [paper_strict/live] profile divergence WARNING:
           23.4% of 85 shared candidates disagree
```

This means paper_strict is executing many candidates that live rejects.
Investigate which rejection reason drives the divergence:
- SUSPICIOUS_UNDERROUND: ask_sum difference between policies
- STALE_PRICING: live's 60s limit rejecting what paper_strict's 120s allows
- NEGATIVE_EDGE: live's 0.030 threshold rejecting what paper_strict's 0.025 allows

---

## Files

| File | Role |
|------|------|
| `monitoring/metrics.py` | Pure metric extractors — no state |
| `monitoring/drift_monitor.py` | DriftMonitor class — baseline vs current |
| `monitoring/alerts.py` | AlertEngine + AlertConfig — threshold checks |
