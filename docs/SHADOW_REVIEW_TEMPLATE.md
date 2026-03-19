# Shadow Review Template

Daily review checklist for the operator. Run after each day of shadow collection.

## How to Run

```bash
python -c "
from monitoring.daily_review import generate_daily_review, write_readiness_verdict
from shadow_runner.journal import JournalReader
import glob

files = sorted(glob.glob('data/shadow_journal_*.jsonl'))
records = []
for f in files:
    reader = JournalReader(f)
    records.extend(list(reader.read_all()))

report = generate_daily_review(records)
write_readiness_verdict(report, 'data/readiness_verdict.json')
print(report.readiness.verdict_reason)
"
```

## Review Checklist

### 1. Evidence Sufficiency

- [ ] live_like_evaluated ≥ 100?
- [ ] observation_days ≥ 5?
- [ ] evidence_source = "live_shadow" for all records? (not synthetic/demo)

### 2. Execution Rate (live profile)

- [ ] execution_rate between 10%–40%? (too high = calibration suspect; too low = too conservative)
- [ ] Is LOW_EDGE the dominant rejection? (expected)
- [ ] Is any single rejection reason > 80% of total? (concentration risk)

### 3. EV Quality

- [ ] mean execution_adjusted_ev > 0.030?
- [ ] ev_haircut_pct (gross→exec drop) < 40%?
- [ ] Is there a meaningful EV distribution (p25/p75 spread reasonable)?

### 4. Pricing Sanity

- [ ] suspicious_underround_rate < 10%?
- [ ] stale_pricing_rate < 15%?
- [ ] Any INVALID_PRICING rejections? (should be rare)

### 5. Profile Comparison

- [ ] How many "loose-only" decisions today?
- [ ] Does live and paper_strict agree on most candidates?
- [ ] If live rejects but loose executes: what was the reason?

### 6. Drift (after Day 2+)

- [ ] rejection_rate_delta stable (< ±10pp from baseline)?
- [ ] ev_mean_delta stable (< ±1pp)?
- [ ] No CRITICAL drift alerts?

### 7. Journal Integrity

- [ ] bad_line_fraction = 0.0?
- [ ] All files parseable?

### 8. Readiness Verdict

- [ ] What is today's verdict?
- [ ] If NO_GO: which blockers/fails?
- [ ] If CONDITIONAL: which warnings?
- [ ] If INSUFFICIENT_EVIDENCE: how many more days needed?

## After Review

If verdict = TINY_PILOT_CANDIDATE:
→ Review pilot_constraints before proceeding
→ Max $10 per trade, BTC/15m only, 1 open position, daily review mandatory

If verdict = CONDITIONAL_REVIEW:
→ Identify specific warnings
→ Do NOT proceed to pilot until warnings are resolved or accepted

If verdict = NO_GO:
→ Identify blockers/fails
→ Fix underlying issue before re-evaluating
