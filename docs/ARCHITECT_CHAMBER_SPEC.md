# Architect Chamber — System Specification

**Sprint:** Architect Chamber / Operator Dashboard
**Status:** Implemented (Phase 14+)
**URL:** `http://localhost:8080/chamber`

---

## What Architect Chamber Is

Architect Chamber is the operator control surface for the Polymarket trading bot.
It is NOT a pretty view layer. It is the operator's truth surface.

It answers six questions at any moment:
1. What did the bot decide recently?
2. What positions are currently open?
3. What trades have already closed?
4. What is the system health right now?
5. How does behavior differ by profile (live vs strict vs loose)?
6. Is the system ready for a tiny pilot or not?

---

## What Powers It

Every value shown in Architect Chamber traces to a real data source.
No fabricated values. No interpolated fields without documentation.

| Data Source | Contents | Real vs Shadow |
|---|---|---|
| `data/positions.json` | Capital, open positions, closed trades, daily PnL | **REAL** (actual or simulated orders) |
| `data/status.json` | Cycle count, scanned markets, last update, mode | **REAL** (live orchestrator state) |
| `data/control.json` | Live trading on/off, simulation on/off, min bet | **REAL** (operator control) |
| `data/shadow_journal_*.jsonl` | Every decision under every profile | **SHADOW** (no real money) |
| `data/readiness_verdict.json` | Readiness verdict from daily_review.py | **DERIVED** (from shadow corpus) |

---

## What Is Shadow-Only vs Real

### REAL (capital at risk or simulated orders):
- `open_positions` — from `positions.json`
- `closed_trades` — from `positions.json`
- `equity_state` — from `positions.json` + `status.json`
- `realized_pnl_day`, `realized_pnl_total` — from positions ledger
- `cycle_running`, `cycles_completed` — from `status.json`

### SHADOW (describe what WOULD happen, no money moved):
- `DecisionEvent.execution_adjusted_ev` — simulated executable EV
- `DecisionEvent.fill_fraction` — simulated fill
- `DecisionEvent.gross_ev`, `net_ev_after_fee` — simulated
- All readiness checks — based on shadow corpus
- All profile comparison rows — from shadow journal

This distinction is explicit in `operator_layer/types.py` field comments.

---

## Architecture

```
architect_chamber/index.html    (UI — polls /api/chamber/summary every 5s)
        ↓ fetch
core/web_server.py              (_send_chamber routes → operator_layer/api.py)
        ↓
operator_layer/api.py           (handler functions per route)
        ↓
operator_layer/aggregator.py    (build_chamber_summary — single assembly point)
        ↓
operator_layer/pnl.py           (build_equity_state, positions, trades)
operator_layer/health.py        (build_health_state, alerts)
operator_layer/readiness_view.py(build_readiness_state, profile summaries)
operator_layer/ledgers.py       (read_*: raw file I/O only)
```

---

## API Routes

| Route | Description |
|---|---|
| `GET /chamber` | Architect Chamber HTML dashboard |
| `GET /api/chamber/summary` | Full ChamberSummary (primary payload) |
| `GET /api/chamber/decisions` | Recent decision feed |
| `GET /api/chamber/positions` | Open positions view |
| `GET /api/chamber/trades` | Closed trades blotter |
| `GET /api/chamber/health` | Health and alert state |
| `GET /api/chamber/readiness` | Readiness verdict and evidence |
| `GET /api/chamber/profile-comparison` | Cross-profile comparison |
| `GET /api/chamber/equity` | Equity / capital state |
| `GET /api/chamber/decision/<id>` | Single decision chain drilldown |

---

## Dashboard Sections

### 1. Top Status Bar
- Environment badge (LIVE / SIMULATION / STOPPED)
- Readiness verdict pill
- Journal health pill
- Blocker/warn count pill
- Total shadow records count
- Last updated time

### 2. Overview Tab
- Equity cards: total equity, cash, unrealized PnL, day PnL
- Shadow stats: total records, journal files, observation days, cycles
- Recent 10 decisions preview (clickable → chain drilldown)
- Readiness verdict box + evidence progress bar
- Active alerts summary

### 3. Decisions Tab
- Full recent decision feed (up to 50 records)
- Filterable by: profile, decision type, asset, market ID
- Columns: time, asset, horizon, decision badge, profile, cal quality, EV, rejection reason, snapshot age
- Click any row → Decision Chain drilldown modal

### 4. Positions Tab
- Open position count and total unrealized PnL stats
- Position table: market, outcome, size, entry, current mark, unrealized PnL, status, risk flags

### 5. Closed Trades Tab
- Win/loss/neutral counts and total realized PnL stats
- Trade blotter: market, outcome, size, entry, close, PnL, PnL%, result

### 6. Profile Comparison Tab
- Per-profile stat cards (execution rate, totals)
- Divergent decision table: rows where profiles disagree
- Cross-profile summary: loose-only count, strict-not-live count

### 7. Health Tab
- Journal health, suspicious underround rate, stale pricing rate, partial fill rate
- All active alerts with level, category, value, threshold
- Cycle status: running, last cycle, cycles completed, blocker reasons

### 8. Readiness Tab
- Verdict box with color coding
- Evidence sufficiency numbers with progress bars
- All readiness checks table (level, value, message)
- Pilot constraints panel (visible even on NO_GO)
- Per-profile summary panels
- Evidence gaps list

### 9. Decision Chain Modal (drilldown)
Shows 5 explicit audit steps for any selected decision:
- Step 1: Bridge Mapping (intent side, effective probs, context)
- Step 2: Calibration (method, quality, calibrated probs, Brier/ECE)
- Step 3: Pricing Sanity (ask/bid spreads, snapshot age, sanity notes)
- Step 4: EV/Edge Computation (gross EV, net EV, execution-adjusted EV, threshold, fill)
- Step 5: Policy Gate (profile, passes gate, final decision, rejection reason)

---

## What Remains Deferred

1. **Timestamp enrichment for closed trades**: `opened_at` / `closed_at` are not stored in `positions.json`. A future shadow-journal join could provide these.
2. **Decision → position link**: `original_decision_id` on ClosedTrade is None. Requires cross-referencing shadow journal by market_id/time.
3. **Real live order execution**: Chamber shows a kill-switch status but does not yet control live execution (by design for this sprint).
4. **DriftMonitor integration**: drift alerts require DriftMonitor.check() output. Currently computed from rates on recent records only.
5. **Time-range filtering**: UI currently shows most-recent N records. Date range picker deferred.
6. **Export**: CSV export of decisions/trades deferred.
