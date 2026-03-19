# Paper Mode Policy

**Source:** `config/policies.py`, `calibration/types.py`

## Three Policy Profiles

### LIVE (`LIVE_CAL_CONFIG`)

For real capital deployment only. Every check at maximum strictness.

- `min_execution_adjusted_edge = 0.030`
- `min_calibrated_confidence = 0.550`
- `min_prob_sum = 0.900`
- `reject_on_weak_calibration = True`
- `reject_on_unknown_calibration = True`
- `require_class_probabilities = True`
- `max_snapshot_age_seconds = 60`
- `min_ask_sum = 0.97` → REJECT below
- `partial fill → REJECT`
- Caller MUST pass `intended_size_usdc` or ValueError

### PAPER_STRICT (`PAPER_STRICT_CAL_CONFIG`)

For honest paper evaluation before going live.

- `min_execution_adjusted_edge = 0.025`
- `min_calibrated_confidence = 0.550`
- `min_prob_sum = 0.750`
- `reject_on_weak_calibration = True`
- `reject_on_unknown_calibration = True`
- `max_snapshot_age_seconds = 120`
- `min_ask_sum = 0.93`
- `partial fill → EV penalty (50bps)`

If a trade fails paper_strict, it will almost certainly fail live.

### PAPER_LOOSE (`PAPER_LOOSE_CAL_CONFIG`)

For hypothesis exploration and debugging only.

- `min_execution_adjusted_edge = 0.020`
- `min_calibrated_confidence = 0.550`
- `min_prob_sum = 0.500`
- `reject_on_weak_calibration = False`
- `reject_on_unknown_calibration = False`
- `require_class_probabilities = False`
- `max_snapshot_age_seconds = 300`
- `min_ask_sum = 0.88` (annotate, not reject)
- `suspicious_underround → annotate only`
- `partial fill → EV penalty only`

**paper_loose results CANNOT be used to justify going live.**
If a trade only passes in paper_loose, that is a WARNING, not a signal.

### PAPER (legacy)

Alias for paper_loose. `mode="paper"`. Preserved for backward compatibility.
New code must use `PAPER_LOOSE_CAL_CONFIG`.

## Profile Comparison in Practice

When shadow runner records diverge:
- `live REJECT / paper_loose EXECUTE` = "loose-only" → investigate carefully
- `live REJECT / paper_strict REJECT / paper_loose EXECUTE` = strongest warning
- `live EXECUTE / paper_strict EXECUTE` = high confidence signal

The Profile Comparison tab in Architect Chamber shows these divergences explicitly.
