# Drift Monitoring Specification

**Module:** `monitoring/drift_monitor.py`

## Purpose

Detect statistical drift in shadow decision behavior over time.
Compares a baseline window (older records) vs a current window (recent records).

## Metrics Monitored

### 1. Rejection Rate Drift
`rejection_rate_delta = current_rejection_rate - baseline_rejection_rate`

Alert thresholds:
- WARNING: |delta| > 0.10 (10pp)
- CRITICAL: |delta| > 0.20 (20pp)

### 2. EV Mean Drift
`ev_mean_delta = current_mean_ev - baseline_mean_ev`

Alert thresholds:
- WARNING: |delta| > 0.010 (1pp EV drop)
- CRITICAL: |delta| > 0.020

### 3. Profile Divergence Drift
`divergence_delta = current_divergence_rate - baseline_divergence_rate`

Alert if paper_loose diverges significantly more from live than baseline.

## DriftReport Structure

```python
DriftReport:
  baseline_window_size  # N records from earlier period
  current_window_size   # N records from recent period
  profiles_checked      # ["live", "paper_strict", "paper_loose"]
  rejection_rate_delta  # {profile: float}
  ev_mean_delta         # {profile: float}
  divergence_delta      # {"live/paper_loose": float, ...}
  alerts                # list[DriftAlert]
  ts_computed_utc
```

## DriftAlert Structure

```python
DriftAlert:
  level           # "WARNING" | "CRITICAL"
  metric          # "rejection_rate" | "ev_mean" | "divergence"
  policy_profile  # which profile triggered it
  message         # human-readable
  baseline_value  # float
  current_value   # float
  delta           # current - baseline
  threshold       # alert threshold crossed
```

## Integration

`monitoring/daily_review.py` calls `DriftMonitor.check()` and includes the
result in `DailyReviewReport`. Drift alerts flow to:
- `artifacts/drift_report.json`
- `HealthState.drift_alerts` in Architect Chamber
- Health tab alerts panel
