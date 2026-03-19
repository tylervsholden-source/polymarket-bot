# Journal Schema

**File:** `data/shadow_journal_YYYY-MM-DD.jsonl`
**Schema version:** 1
**Format:** newline-delimited JSON (JSONL)

## Record Structure

```json
{
  "record_id": "uuid4",
  "run_id": "uuid4",
  "ts_recorded_utc": "2026-03-15T20:00:00+00:00",
  "schema_version": "1",
  "policy_profile": "live",
  "intended_size_usdc": 20.0,
  "evidence_source": "live_shadow",

  "signal": {
    "asset": "BTC",
    "horizon_minutes": 15,
    "signal_timestamp_utc": "...",
    "predicted_class": "UP",
    "raw_confidence": 0.72,
    "class_probabilities": {"UP": 0.72, "DOWN": 0.18, "NO_TRADE": 0.10},
    "model_version": "v0",
    "calibrated_up_prob": 0.67,
    "calibrated_down_prob": 0.28,
    "calibrated_no_trade_prob": 0.05,
    "calibration_method": "platt",
    "calibration_quality": "good",
    "effective_yes_prob": 0.67,
    "effective_no_prob": 0.33,
    "mapping_context": "UP→YES",
    "bridge_intent_side": "YES",
    "brier_score": 0.18,
    "ece": 0.04
  },

  "pricing": {
    "market_id": "0xabc...",
    "ask_yes": 0.55,
    "bid_yes": 0.54,
    "ask_no": 0.46,
    "bid_no": 0.45,
    "liquidity": 8000.0,
    "pricing_timestamp_utc": "...",
    "snapshot_age_seconds": 5.0
  },

  "decision_summary": {
    "decision": "EXECUTE_YES",
    "rejection_reason": null,
    "policy_mode": "live",
    "passes_final_gate": true,
    "intended_size_usdc_used": 20.0,
    "pricing_sanity_notes": null,
    "gross_ev": 0.052,
    "net_ev_after_fee": 0.042,
    "execution_adjusted_ev": 0.038,
    "required_edge_threshold": 0.030,
    "fill_fraction": 1.0
  }
}
```

## Known Decision Values

`decision_summary.decision`:
- `"EXECUTE_YES"` — buy YES token
- `"EXECUTE_NO"` — buy NO token
- `"REJECT"` — decision rejected

`decision_summary.rejection_reason` (when decision = REJECT):
- `"LOW_EDGE"` — executable EV below threshold
- `"STALE_PRICING"` — snapshot too old
- `"LOW_CALIBRATION_CONFIDENCE"` — calibration quality insufficient
- `"LOW_PROB_SUM"` — YES+NO probability sum too low
- `"LOW_LIQUIDITY"` — market liquidity below minimum
- `"PARTIAL_FILL_REJECT"` — expected fill fraction too low (live mode)
- `"INVALID_PRICING"` — hard structural pricing failure
- `"NO_SIGNAL_PRODUCED"` — orchestrator found no arb signal (live path only)

## Null Fields

- `gross_ev`, `net_ev_after_fee`, `execution_adjusted_ev`, `fill_fraction`: null when decision = REJECT before EV computation
- `pricing_sanity_notes`: null when pricing is clean; set only in paper_loose when suspicious
- `brier_score`, `ece`: null if not computed for this model version

## Integrity

JournalWriter writes one JSON object per line.
Bad lines (truncated writes, encoding errors) are counted by
`ledgers.read_journal_integrity_stats()` as `bad_json_count`.
Health gate triggers warning when `bad_line_fraction > 0.02`.
