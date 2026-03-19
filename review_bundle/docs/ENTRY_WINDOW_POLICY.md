# Entry Window Policy Specification

## Problem Statement

Previous timing logic was based on time-to-resolution (end time). A 5-minute market with 3 minutes remaining could still be entered, even though the market had already started and the optimal entry window had passed. This led to late entries with poor execution.

## Solution: Start-Time-Based Entry Windows

Entry windows are now defined relative to market **start time**, not resolution time.

### Window Definition

For each horizon, two parameters define the allowed entry window:

| Parameter | Meaning |
|-----------|---------|
| `entry_before_start_sec` | How many seconds BEFORE market start time entry is allowed |
| `entry_after_start_sec` | How many seconds AFTER market start time entry is still allowed |

### Default Policy

| Horizon | Before Start | After Start | Total Window |
|---------|-------------|-------------|--------------|
| 5m | 45 seconds | 90 seconds | 135 seconds |
| 15m | 60 seconds | 180 seconds | 240 seconds |

### Examples

**5-minute market**: "Bitcoin Up or Down - March 16, 7:10PM-7:15PM ET"
- Market start: 7:10PM ET = 23:10:00 UTC
- Window opens: 23:09:15 UTC (45s before start)
- Window closes: 23:11:30 UTC (90s after start)

**15-minute market**: "Bitcoin Up or Down - March 16, 7:00PM-7:15PM ET"
- Market start: 7:00PM ET = 23:00:00 UTC
- Window opens: 22:59:00 UTC (60s before start)
- Window closes: 23:03:00 UTC (180s after start)

### Rejection Reasons

| Rejection | When |
|-----------|------|
| `TOO_EARLY_FOR_ENTRY_WINDOW` | Current time < window open |
| `TOO_LATE_FOR_ENTRY_WINDOW` | Current time > window close |
| `START_TIME_MISSING` | Cannot parse market start time from question text |
| `ENTRY_WINDOW_UNAVAILABLE` | No policy for this horizon (only 5m/15m supported) |
| `APPROVAL_DELAY_PUSHED_OUT_OF_WINDOW` | Re-check after approval delay finds time outside window |

### Horizon Detection

Horizon is computed from the time range in the question text:
- Diff <= 7 minutes → 5m policy
- Diff <= 20 minutes → 15m policy
- Diff > 20 minutes → no policy (rejected)

### Market Start Time Parsing

Supports format: `"Month Day, H:MMAM/PM-H:MMAM/PM ET"`

ET is converted to UTC by adding 4 hours (simplified; no DST handling).

## Integration Points

### Live Gate (Check #9 of 11)

Entry window is enforced as check #9 in `check_live_gate()`:

```
1. process_lock
2. live_trading
3. readiness
4. daily_stop
5. position_count
6. rate_limit
7. reentry_guard
8. expiry_guard
9. entry_window      ← NEW
10. approval
11. capital
```

### Orchestrator

Both order paths pass entry window parameters:
- **Direct orders**: `is_recheck_after_approval=False`
- **Approved orders**: `is_recheck_after_approval=True`

### Approval Delay Re-check

When an order returns from the approval queue, the entry window is re-checked. If the approval delay pushed the current time past the window, the order is rejected with `APPROVAL_DELAY_PUSHED_OUT_OF_WINDOW`.

## Files

| File | Role |
|------|------|
| `control_plane/entry_window_guard.py` | Core implementation |
| `control_plane/live_gate.py` | Enforcement point |
| `agents/orchestrator.py` | Integration |

## Verification

- `tests/test_entry_window_policy.py` — 5m/15m windows, rejections
- `tests/test_entry_window_live_gate.py` — live gate enforcement
- `tests/test_5m_15m_timing_windows.py` — boundary precision per horizon
- `tests/test_approval_delay_recheck.py` — approval delay invalidation
- `tests/test_market_start_time_parsing.py` — time parsing edge cases
