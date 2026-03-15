# Decision Journal Schema
## Phase 13 — Effective 2026-03-15

---

## Format

Newline-delimited JSON (JSONL). One record per line. UTF-8 encoded.

Files are named `journal_{run_id}.jsonl` by convention.
Files are append-only — never truncated or modified after writing.

---

## Schema Version

Every record carries `"schema_version": "1"`.

Records with unknown schema versions are silently skipped by `JournalReader`.
This allows forward-compatible schema evolution: old readers ignore new fields.

---

## Full Record Structure

```json
{
  "schema_version": "1",
  "record_id":      "<uuid4>",
  "run_id":         "<uuid4>",
  "ts_recorded_utc": "2026-03-15T12:00:00+00:00",
  "policy_profile": "live",
  "intended_size_usdc": 20.0,

  "signal": {
    "asset":                    "BTC",
    "horizon_minutes":          5,
    "signal_timestamp_utc":     "2026-03-15T11:55:00+00:00",
    "predicted_class":          "UP",
    "raw_confidence":           0.72,
    "class_probabilities":      {"UP": 0.72, "DOWN": 0.168, "NO_TRADE": 0.112},
    "model_version":            "v0",
    "calibrated_up_prob":       0.72,
    "calibrated_down_prob":     0.168,
    "calibrated_no_trade_prob": 0.112,
    "calibration_method":       "platt",
    "calibration_quality":      "strong",
    "effective_yes_prob":       0.72,
    "effective_no_prob":        0.28,
    "mapping_context":          "UP→YES (NORMAL)",
    "bridge_intent_side":       "YES",
    "brier_score":              null,
    "ece":                      null
  },

  "pricing": {
    "market_id":              "mkt-001",
    "ask_yes":                0.44,
    "bid_yes":                0.42,
    "ask_no":                 0.57,
    "bid_no":                 0.55,
    "liquidity":              5000.0,
    "pricing_timestamp_utc":  "2026-03-15T11:59:30+00:00",
    "snapshot_age_seconds":   30.0
  },

  "decision": {
    "decision":                 "EXECUTE_YES",
    "rejection_reason":         null,
    "policy_mode":              "live",
    "passes_final_gate":        true,
    "intended_size_usdc_used":  20.0,
    "pricing_sanity_notes":     null,
    "gross_ev":                 0.28,
    "net_ev_after_fee":         0.27,
    "execution_adjusted_ev":    0.265,
    "required_edge_threshold":  0.030,
    "fill_fraction":            1.0
  }
}
```

---

## Mandatory Fields

These fields MUST be present in every record. A record missing any of these
is considered malformed and will fail to deserialize.

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | string | Must be `"1"` |
| `record_id` | string | UUID4, unique per record |
| `run_id` | string | UUID4, shared across one runner session |
| `ts_recorded_utc` | ISO-8601 string | Wall clock at write time |
| `policy_profile` | string | The `config.mode` used |
| `signal.asset` | string | Asset identifier |
| `signal.horizon_minutes` | int | Signal horizon |
| `signal.signal_timestamp_utc` | ISO-8601 string | Signal generation time |
| `signal.predicted_class` | string | "UP" / "DOWN" / "NO_TRADE" |
| `signal.raw_confidence` | float | |
| `signal.calibration_method` | string | |
| `signal.calibration_quality` | string | |
| `signal.bridge_intent_side` | string | "YES" or "NO" |
| `signal.effective_yes_prob` | float | |
| `signal.effective_no_prob` | float | |
| `pricing.market_id` | string | |
| `pricing.ask_yes` | float | |
| `pricing.bid_yes` | float | |
| `pricing.ask_no` | float | |
| `pricing.bid_no` | float | |
| `pricing.liquidity` | float | |
| `pricing.pricing_timestamp_utc` | ISO-8601 string | |
| `pricing.snapshot_age_seconds` | float | Age at decide() call time |
| `decision.decision` | string | "EXECUTE_YES" / "EXECUTE_NO" / "REJECT" |
| `decision.rejection_reason` | string or null | |
| `decision.policy_mode` | string | From TradeDecision |
| `decision.passes_final_gate` | bool | |
| `decision.intended_size_usdc_used` | float | |

---

## Optional Fields

| Field | Present when |
|-------|-------------|
| `signal.class_probabilities` | Model outputs class probs (not always present) |
| `signal.brier_score` | Calibration metrics available |
| `signal.ece` | Calibration metrics available |
| `decision.pricing_sanity_notes` | paper_loose EXECUTE with suspicious pricing |
| `decision.gross_ev` | Decision reached edge estimation stage |
| `decision.net_ev_after_fee` | Decision reached edge estimation stage |
| `decision.execution_adjusted_ev` | Decision reached edge estimation stage |
| `decision.required_edge_threshold` | Decision reached edge estimation stage |
| `decision.fill_fraction` | Partial fill simulation ran |

---

## Datetime Conventions

- All datetimes are UTC.
- Serialized as ISO-8601 with timezone offset: `"2026-03-15T12:00:00+00:00"`.
- Naive datetimes (no tzinfo) are assumed UTC on read.

---

## Reconstructing now_utc for Replay

The `pricing.pricing_timestamp_utc` + `pricing.snapshot_age_seconds` fields
together fully determine the `now_utc` that was passed to `decide()`:

```python
now_utc = pricing_timestamp_utc + timedelta(seconds=snapshot_age_seconds)
```

This is the only way to reconstruct staleness-sensitive decisions correctly.
Do not use `ts_recorded_utc` for replay — that is wall-clock write time, not
the decision reference clock.

---

## Schema Evolution Policy

- New optional fields may be added to any section without incrementing `schema_version`.
- Breaking changes (field rename, type change, remove mandatory field) MUST increment `schema_version`.
- Old readers encountering schema_version > known version should skip the record.
