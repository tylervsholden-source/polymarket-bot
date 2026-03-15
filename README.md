# Polymarket AI Trading Bot — Phase 13 Core Snapshot

## Scope of This Package

**This snapshot contains the decision-engine core only:**

| Included | Not included |
|----------|-------------|
| `calibration/` | `agents/` (orchestrator, feeds) |
| `execution_realism/` | `core/` (position manager, API client) |
| `signal_bridge/` | `strategies/` (Kelly, Stoikov, etc.) |
| `shadow_runner/` | `main.py` (entry point) |
| `monitoring/` | `backtesting/` |
| `tests/` (862 tests for the above) | Full integration tests with live API |

Running `pytest` scoped to the above passes **862/862**. Running bare `pytest` from the repo root will collect errors on `agents/`, `core/`, `strategies/` — those modules are part of the full repo and are not included here.

**Current phase:** Phase 13 — Shadow Runner + Journaling + Drift Monitoring
**Readiness:** Shadow evidence collection ready. Live pilot requires 3–7 days of shadow run data before activation.

## Overview

Decision engine for automated Polymarket trading. Handles signal calibration, edge estimation, execution realism, and shadow execution across three policy profiles (live / paper_strict / paper_loose).

This is **not** a "run and profit" package. It is an evidence-gathering and decision-validation layer. Live trading requires the full repo including orchestrator, position manager, and API client.

## Running the Core Tests

```bash
pip install -r requirements.txt
python -m pytest tests/ calibration/tests/ execution_realism/tests/ signal_bridge/tests/ -v
# 862 passed
```

## Architecture

```
ORCHESTRATOR (60s loop)
  ├── BinanceFeed          → spot price, RSI, volume, order book
  ├── ArbitrageEngine      → BayesianEstimator + EdgeModel + Stoikov + Kelly
  │     signal_bridge/     → RawSignal → CalibratedSignal → TradeIntent
  │     calibration/       → CalibrationConfig × (live|paper_strict|paper_loose)
  │     execution_realism/ → fill_fraction, slippage, staleness penalty
  ├── SmartTraderTracker   → ±0.05 boost from on-chain whale signals
  ├── PositionManager      → capital accounting, YES/NO PnL
  └── ShadowRunner         → parallel dry-run across all policy profiles
        shadow_runner/     → journal (JSONL), replay, reporting
        monitoring/        → DriftMonitor, AlertEngine
```

## Key Modules

| Module | Purpose |
|--------|---------|
| `calibration/` | Signal calibration, edge estimation, decision policy |
| `execution_realism/` | Fill simulation, slippage, liquidity, staleness |
| `signal_bridge/` | Raw signal → calibrated signal → trade intent bridge |
| `shadow_runner/` | Shadow execution, JSONL journal, deterministic replay |
| `monitoring/` | Drift detection, alert engine |
| `config/` | Policy profiles (live / paper_strict / paper_loose) |

## Policy Profiles

- **live** — production thresholds, strictest pricing sanity, PLATT/ISOTONIC calibration only
- **paper_strict** — live-equivalent thresholds but no real orders
- **paper_loose** — relaxed thresholds for signal discovery (OBSERVATION ONLY)

## Running Tests

```bash
python -m pytest tests/ calibration/tests/ execution_realism/tests/ signal_bridge/tests/ -v
# 862 passed
```

## Artifacts

See `artifacts/` for:
- `pytest_output.txt` — full test run output
- `shadow_journal_sample.jsonl` — example journal records
- `daily_report_sample.json` — per-profile decision summary
- `drift_report_sample.json` — drift monitoring output with alerts
- `replay_examples/` — deterministic replay verification
- `decision_examples/` — EXECUTE/REJECT scenarios across profiles

## Spec Documents

All design decisions documented in `docs/`:
- `DECISION_FLOW.md` — end-to-end decision pipeline
- `SHADOW_RUNNER_SPEC.md` — shadow execution architecture
- `JOURNAL_SCHEMA.md` — JSONL record schema
- `DRIFT_MONITORING_SPEC.md` — drift detection design
- `PRICING_SANITY_SPEC.md` — pricing sanity gate
- `PAPER_MODE_POLICY.md` — paper vs live policy differences
- `PARTIAL_FILL_POLICY.md` — partial fill economics
- `EXECUTABLE_NOTIONAL_SPEC.md` — notional size computation
- `DECISION_INTEGRATION_SPEC.md` — integration contract
- `CONTRACT_HARDENING_SPEC.md` — live contract enforcement
