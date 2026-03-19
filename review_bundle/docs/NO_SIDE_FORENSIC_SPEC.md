# NO-Side Forensic Specification

## Problem Statement

The ArbitrageEngine's NO/DOWN side was practically dead in live trading. Root cause: `no_best_ask` from the Polymarket CLOB orderbook frequently returns 0.99, making `no_edge` (= `no_prob - no_best_ask`) strongly negative. This meant the engine never selected direction=NO regardless of market conditions.

Meanwhile, shadow/reporting used synthetic NO prices (`1 - yes_price`), creating a split-brain where shadow showed healthy NO edges that could never execute live.

## Solution

### 1. NoPriceSource Tracking

Every market evaluation now explicitly tracks where the NO ask price came from:

| Source | Meaning |
|--------|---------|
| `REAL_BOOK` | From actual NO token orderbook (`market["no_best_ask"]`) |
| `SYNTHETIC` | Derived as `1 - yes_price` (no real book data) |
| `MISSING` | Book was fetched but returned no usable data |

### 2. NO-Side Health Classification

When `no_price_source == REAL_BOOK`, the ask is classified:

| Health | Condition | Effect |
|--------|-----------|--------|
| `OK` | `ask < 0.90` | NO direction eligible |
| `SUSPICIOUS` | `0.90 <= ask < 0.95` | NO direction blocked, logged |
| `UNTRADABLE` | `ask >= 0.95` | NO direction blocked, logged |

### 3. NoSideStatus — Direction Decision Reasons

Every market evaluation produces an explicit reason for the direction decision:

| Status | Meaning |
|--------|---------|
| `YES_EDGE_DOMINATES` | `yes_edge >= no_edge` and `yes_edge > 0` |
| `NO_EDGE_DOMINATES` | `no_edge > yes_edge`, real book, healthy ask |
| `NO_REAL_BOOK_MISSING` | NO edge was better but no real orderbook data |
| `NO_SIDE_UNTRADABLE` | NO edge was better but ask >= 0.95 |
| `NO_SIDE_BOOK_SUSPICIOUS` | NO edge was better but ask >= 0.90 |
| `BOTH_EDGES_NEGATIVE` | Neither YES nor NO had positive edge |
| `NO_EDGE_NEGATIVE` | YES edge positive, NO edge <= 0 |

### 4. SideDiagnostics

Every market candidate (including rejected ones) gets a `SideDiagnostics` record with:
- YES/NO prices, edges, probabilities
- NO price source and token presence
- Selected direction and explicit reason
- Timestamp and market identity

Stored in `ArbitrageEngine._last_diagnostics` (keyed by `condition_id`), accessible via `get_last_diagnostics()`.

### 5. Direction Selection Gate

NO direction is ONLY selected when ALL conditions are met:
1. `no_edge > yes_edge`
2. `no_edge > 0`
3. `no_price_source == REAL_BOOK`
4. `no_side_health == "OK"` (ask < 0.90)

This prevents the engine from trading NO on synthetic or unhealthy book data.

## Files Changed

| File | Change |
|------|--------|
| `strategies/arbitrage_engine.py` | Added `NoSideStatus`, `NoPriceSource`, `SideDiagnostics`; rewrote `_evaluate_market()` |
| `agents/orchestrator.py` | Shadow journal uses real NO prices when available; includes diagnostics in records |

## Verification

- `tests/test_no_side_diagnostics.py` — diagnostics populated, reasons explicit
- `tests/test_no_side_execution_path.py` — direction=NO uses correct token, no YES hardcoding
- `tests/test_shadow_live_side_consistency.py` — shadow and live see same NO reality
