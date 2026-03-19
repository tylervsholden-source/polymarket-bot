# Incident Bundle — INC-2026-03-15-001

**Date:** 2026-03-15
**Impact:** 7 unauthorized real orders, $72.73 USDC committed
**Status:** Live trading disabled, remediation in progress

## Contents

```
incident_bundle/
├── README.md                          ← This file
├── main.py                            ← Entry point
├── CLAUDE.md                          ← Project instructions
├── requirements.txt
├── .env.example
│
├── incident/                          ← INCIDENT ARTIFACTS
│   ├── timeline.txt                   ← Full chronological timeline (UTC)
│   ├── order_event.json               ← All 7 orders with IDs, amounts, timestamps
│   ├── runtime_env_sanitized.txt      ← Active config (secrets removed)
│   ├── run_command.txt                ← How bot was started, 3 instances
│   └── root_cause_analysis.txt        ← 5 root causes + remediation
│
├── data/                              ← STATE FILES (as-is, not cleaned)
│   ├── control.json                   ← live_trading flag (NOW false)
│   ├── status.json                    ← Dashboard status snapshot
│   ├── positions.json                 ← Current positions + 8 closed trades
│   ├── positions.json.bak             ← Backup before changes
│   ├── positions.json.bak2
│   ├── readiness_verdict.json         ← TINY_PILOT_CANDIDATE (manual override)
│   ├── position_meta.json
│   └── shadow_journal_2026-03-15.jsonl ← 1199 shadow decision records
│
├── logs/                              ← BOT LOGS
│   ├── bot.log                        ← Main log (full day)
│   ├── runtime_evidence_20260315.log
│   └── test_results_20260315.log
│
├── core/                              ← CORE MODULE
│   ├── polymarket_client.py           ← CLOB API (place_order lives here)
│   ├── position_manager.py            ← Position tracking + PnL
│   └── web_server.py                  ← Dashboard (READ-ONLY)
│
├── agents/                            ← AGENTS
│   └── orchestrator.py                ← Main cycle loop (orders placed here)
│
├── strategies/                        ← TRADING STRATEGIES
│   ├── arbitrage_engine.py            ← Signal generation + expired market bug
│   └── kelly_criterion.py             ← Position sizing
│
├── operator_layer/                    ← ARCHITECT CHAMBER
│   ├── aggregator.py
│   ├── health.py
│   ├── ledgers.py
│   ├── pnl.py
│   ├── readiness_view.py
│   └── types.py
│
├── signal_bridge/                     ← Signal processing
├── calibration/                       ← Probability calibration
├── execution_realism/                 ← Fill fraction / execution modeling
├── monitoring/                        ← Daily review / readiness
├── shadow_runner/                     ← Shadow decision runner
├── tests/                             ← Test files
├── artifacts/                         ← Test outputs
└── web/                               ← Frontend files
```

## Quick Investigation Guide

1. Start with `incident/root_cause_analysis.txt`
2. Check `incident/timeline.txt` for exact sequence
3. Verify orders in `incident/order_event.json`
4. Key code path: `agents/orchestrator.py:177` → `core/polymarket_client.py:place_order()`
5. The missing approval gate: search for "approval" in all .py files → 0 results
