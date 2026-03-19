# Package Manifest

**Snapshot date:** 2026-03-15
**Reference commit:** `22ffcc7` (v1 freeze, Phase 14)
**Test status:** 1143 passed, 2 skipped, 0 failures

---

## What Is Real vs What Is Sözde (Placeholder)

### REAL AND USED IN PRODUCTION

| Module | Status | Notes |
|---|---|---|
| `agents/orchestrator.py` | ✅ LIVE | 60s cycle, ArbitrageEngine, shadow recording |
| `core/polymarket_client.py` | ✅ LIVE | CLOB API wrapper |
| `core/position_manager.py` | ✅ LIVE | Capital, positions, closed trades, daily PnL |
| `core/web_server.py` | ✅ LIVE | HTTP server port 8080, now + chamber routes |
| `core/status_writer.py` | ✅ LIVE | status.json writer |
| `shadow_runner/journal.py` | ✅ LIVE | JSONL journal writer |
| `shadow_runner/types.py` | ✅ LIVE | Data models |
| `shadow_runner/validation.py` | ✅ LIVE | Evidence sufficiency gate |
| `shadow_runner/readiness.py` | ✅ LIVE | Readiness verdict |
| `operator_layer/` (all 7 files) | ✅ LIVE | Architect Chamber backend |
| `architect_chamber/index.html` | ✅ LIVE | Operator dashboard |
| `monitoring/daily_review.py` | ✅ LIVE (manual) | Daily review + verdict writer |
| `monitoring/readiness_checks.py` | ✅ LIVE | Check functions |

### RESEARCH / TEST PIPELINE (NOT in live trading loop)

| Module | Status | Notes |
|---|---|---|
| `signal_bridge/` | ✅ Research | Market ↔ signal mapping (not connected to live) |
| `calibration/` | ✅ Research | Full calibration pipeline (used in tests + ShadowRunner) |
| `execution_realism/` | ✅ Research | Fill simulation (used in tests + ShadowRunner) |
| `shadow_runner/runner.py` | ✅ Research | ShadowRunner class (not called by orchestrator) |
| `shadow_runner/reporting.py` | ✅ Research | Policy comparison reports |
| `monitoring/drift_monitor.py` | ✅ Research | Drift detection |
| `monitoring/regime_review.py` | ✅ Research | Regime stability |

### DEPRECATED / DISABLED

| Module | Status | Notes |
|---|---|---|
| `agents/signal_agent.py` | 🚫 DISABLED | Replaced by ArbitrageEngine |
| `agents/whale_tracker.py` | 🚫 DISABLED | Replaced by SmartTraderTracker |
| `backtesting/engine.py` | ⚠️ UNRELIABLE | Endpoint bias, neutral Bayesian prior — not a real backtest |

---

## Source Tree

```
├── CLAUDE.md                        ← Project instructions
├── requirements.txt                 ← Dependencies
├── .env.example                     ← Config template (no secrets)
├── main.py                          ← Entry point
├── agents/
│   ├── orchestrator.py              ← Main 60s trading loop
│   ├── signal_agent.py              ← DISABLED
│   └── whale_tracker.py             ← DISABLED
├── core/
│   ├── polymarket_client.py         ← CLOB API
│   ├── position_manager.py          ← Capital + positions
│   ├── web_server.py                ← HTTP server (+ chamber routes)
│   ├── status_writer.py             ← status.json
│   └── ...
├── strategies/
│   └── kelly_criterion.py           ← Position sizing
├── signal_bridge/                   ← Research: signal↔market bridge
├── calibration/                     ← Research: probability pipeline
├── execution_realism/               ← Research: fill simulation
├── shadow_runner/                   ← Audit journal + readiness
├── monitoring/                      ← Drift, regime, health checks
├── operator_layer/                  ← Architect Chamber backend
├── architect_chamber/
│   └── index.html                   ← Operator dashboard (port 8080/chamber)
├── config/
│   └── policies.py                  ← Policy profile documentation
├── data/                            ← Runtime state (gitignored)
│   ├── positions.json
│   ├── status.json
│   ├── control.json
│   ├── readiness_verdict.json
│   └── shadow_journal_*.jsonl
├── docs/                            ← Engineering specs
├── tests/                           ← 1143 tests
├── artifacts/                       ← Snapshot artifacts (SYNTHETIC labeled)
└── backtesting/                     ← UNRELIABLE, see notes
```

---

## Test Coverage Map

| Test File | What It Tests |
|---|---|
| `test_live_pilot_readiness.py` | Readiness gate, 1-FAIL→NO_GO, evidence gate |
| `test_shadow_runner_*.py` | ShadowRunner, journal, replay |
| `test_drift_monitor.py` | Drift detection |
| `test_operator_aggregator.py` | Aggregator, profile comparison, decision chain |
| `test_pnl_views.py` | Open positions, closed trades, equity |
| `test_health_views.py` | Alert aggregation, journal integrity |
| `test_readiness_views.py` | ReadinessState builder |
| `test_dashboard_api.py` | All 9 chamber API endpoints |
| `test_position_closed_trade_views.py` | Operator workflows: PnL, positions |
| `test_decision_policy_*.py` | Full calibration pipeline |
| `test_pricing_sanity_*.py` | Binary sanity checks |
| `test_execution_realism_*.py` | Fill simulation |
| `test_signal_bridge_*.py` | Market matching, bridge mapping |

---

## Known Gaps (Honest)

1. **opened_at / closed_at for closed trades**: Not stored in positions.json. These fields are always None.
2. **Decision → position link**: ClosedTrade.original_decision_id is always None. Requires shadow journal join by market_id/time.
3. **DriftMonitor in live cycle**: DriftMonitor.check() is not called automatically. Daily review must be run manually.
4. **Time-range filtering in dashboard**: Not implemented. Shows most-recent N records only.
5. **Regime review in readiness**: regime_review not provided to assess_readiness() → verdict caps at CONDITIONAL_REVIEW.
6. **$400K whale threshold**: WhaleTracker disabled. SmartTraderTracker uses position-based tracking, not dollar amounts.

---

## Artifacts (All Labeled SYNTHETIC)

All files in `artifacts/` except `pytest_output.txt`, `test_run_notes.txt`,
and `v1_snapshot_commit.txt` are **SYNTHETIC** — generated for review purposes.

Real shadow journals accumulate in `data/shadow_journal_YYYY-MM-DD.jsonl`
while the orchestrator runs. They are not included in this snapshot (gitignored).
