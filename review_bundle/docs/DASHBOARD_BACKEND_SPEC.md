# Dashboard Backend Specification

**Module:** `operator_layer/`

---

## Module Map

```
operator_layer/
├── __init__.py           — package marker
├── types.py              — all data contracts (DecisionEvent, OpenPosition, ...)
├── ledgers.py            — raw file I/O only (no business logic)
├── pnl.py                — position/trade/equity builders
├── health.py             — health state and alert aggregation
├── readiness_view.py     — readiness state builder (reads verdict file)
├── aggregator.py         — build_chamber_summary() entry point
└── api.py                — handler functions per route (JSON serialization)
```

---

## Data Flow

```
ledgers.py
  read_positions_ledger()       → positions.json
  read_status_snapshot()        → status.json
  read_control_state()          → control.json
  read_readiness_verdict()      → data/readiness_verdict.json
  find_journal_files()          → data/shadow_journal_*.jsonl
  read_journal_records()        → list[dict] from JSONL (most recent first)
  read_journal_integrity_stats()→ bad_line_fraction, total_lines, etc.
        ↓
aggregator.py: build_chamber_summary()
  pnl.build_open_positions()    → list[OpenPosition]
  pnl.build_closed_trades()     → list[ClosedTrade]
  pnl.build_equity_state()      → EquityState
  health.build_health_state()   → HealthState
  readiness_view.build_readiness_state() → ReadinessState
  aggregator.record_to_decision_event()  → list[DecisionEvent]
  aggregator.build_profile_comparison()  → ProfileComparisonSummary
        ↓
api.py: handle_summary() → json.dumps(ChamberSummary)
        ↓
web_server.py: _send_chamber() → HTTP 200 response
        ↓
architect_chamber/index.html: fetch('/api/chamber/summary') → render
```

---

## Key Design Decisions

### 1. No in-process caching
`build_chamber_summary()` reads from disk on every request.
Rationale: dashboard polls every 5s. The files are small. Stale cache would be
worse than the minor I/O cost. Operator trust requires fresh data.

### 2. Aggregator is the only assembly point
The dashboard never calls `ledgers.py` directly. All data flows through
`aggregator.build_chamber_summary()`. This makes it easy to add new data sources
without touching the dashboard or API handlers.

### 3. Serialization is explicit
`api._default()` handles `datetime → ISO string` and
`dataclass → dict via dataclasses.asdict()`. No surprise serialization.

### 4. Journal records capped at 1000 for aggregation
`read_journal_records(max_records=1000)` prevents unbounded reads on large corpora.
`max_decisions=50` caps the decision feed. Profile comparison uses the full 1000.

### 5. JSONL reading is sorted most-recent-first
After reading all lines across all journal files, records are sorted by
`ts_recorded_utc` descending. This ensures the decision feed always shows
the most recent activity regardless of file rotation.

---

## Alert Thresholds (health.py)

Explicit constants — not magic numbers:

| Constant | Value | Meaning |
|---|---|---|
| `SUSPICIOUS_UNDERROUND_WARN` | 0.20 | >20% of records have pricing_sanity_notes |
| `STALE_PRICING_WARN` | 0.15 | >15% of records have snapshot_age > 60s |
| `PARTIAL_FILL_WARN` | 0.30 | >30% of executes had fill_fraction < 1.0 |
| `JOURNAL_BAD_LINE_WARN` | 0.02 | >2% bad JSON lines in journal |

---

## Safe Defaults on Missing Data

Every `read_*` function in `ledgers.py` returns a safe default (empty dict or list)
when the file is not found. The dashboard renders gracefully with no data.

| File | Missing default |
|---|---|
| positions.json | `{capital: 0, positions: {}, closed: [], daily: {pnl: 0}}` |
| status.json | `{}` |
| control.json | `{live_trading: False, simulation_running: False}` |
| readiness_verdict.json | `{verdict: "NOT_REVIEWED"}` |
| shadow journals | `[]` (no records) |
