# Pricing Sanity Specification

**Module:** `calibration/decision_policy.py` (internal `_check_binary_sanity()`)
**Policy table:** `config/policies.py`

## Binary Market Sanity Checks

For YES/NO binary markets, ask prices must satisfy:
`ask_yes + ask_no ≈ 1.0` (the "overround")

### Hard Structural Limits (ALL modes — INVALID_PRICING rejection)

| Condition | Threshold | Action |
|---|---|---|
| ask_sum < 0.85 | ask_yes + ask_no < 0.85 | REJECT → INVALID_PRICING |
| bid_sum > 1.01 | bid_yes + bid_no > 1.01 | REJECT → INVALID_PRICING |
| ask < bid | ask_yes < bid_yes | REJECT → INVALID_PRICING |
| price outside [0,1] | any price < 0 or > 1 | REJECT → INVALID_PRICING |

### Profile-Dependent Limits

| Parameter | LIVE | PAPER_STRICT | PAPER_LOOSE |
|---|---|---|---|
| min_ask_sum | 0.97 | 0.93 | 0.88 (annotate) |
| max_ask_sum | 1.10 | 1.15 | 1.25 (annotate) |
| max_bid_sum | 1.00 | 1.00 | N/A |
| min_single_ask | 0.05 | 0.03 | N/A |
| suspicious_underround | REJECT | REJECT | annotate |

### Suspicious Underround in paper_loose

When `ask_sum < min_ask_sum` in paper_loose:
- Decision is NOT rejected
- `decision_summary.pricing_sanity_notes` is set
- Operator is warned via dashboard

This means: if a trade only passes because of suspicious pricing,
the `pricing_sanity_notes` field will not be null.

## Snapshot Staleness

`snapshot_age_seconds = now_utc - pricing_timestamp_utc`

| Profile | Threshold | Action |
|---|---|---|
| live | 60s | REJECT → STALE_PRICING |
| paper_strict | 120s | REJECT → STALE_PRICING |
| paper_loose | 300s | REJECT → STALE_PRICING |

## Health Thresholds (operator_layer/health.py)

| Alert | Threshold |
|---|---|
| Suspicious underround warning | > 20% of recent decisions |
| Stale pricing warning | > 15% of recent decisions |
| Journal bad lines warning | > 2% of lines |
