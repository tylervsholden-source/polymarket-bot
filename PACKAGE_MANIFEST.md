# Package Manifest
## Phase 10 Snapshot — 2026-03-15

This file lists every module required for the calibration + execution realism
decision path. The package is self-contained: unpacking and running
`python -m pytest calibration/tests/ execution_realism/tests/` requires no
prior-phase artifacts.

---

## Core Decision Path

| File | Purpose |
|------|---------|
| `calibration/types.py` | All data models: `RawSignalOutput`, `CalibratedSignal`, `MarketPricingSnapshot`, `EdgeEstimate`, `TradeDecision`, `CalibrationConfig`. Type contracts enforced in `__post_init__`. |
| `calibration/probability_mapper.py` | Maps directional signal → Polymarket side probability. Returns `(CalibratedSignal, None)` or `(None, RejectionReason)`. |
| `calibration/decision_policy.py` | 12-step `decide()` function. Calls `estimate_*_edge_realistic()`. Returns `TradeDecision`. |
| `calibration/edge_estimator.py` | `estimate_yes/no_edge_realistic()` — primary path. `estimate_yes/no_edge()` — deprecated constant-slippage (backward compat). |

## Execution Realism Layer

| File | Purpose |
|------|---------|
| `execution_realism/types.py` | `ExecutableCostBreakdown`, `StalenessZone`, `FillDecision`, `LiquidityBucket`, etc. |
| `execution_realism/core.py` | `compute_executable_ev()` — assembles full cost breakdown. Phase 11: PARTIAL fill penalty applied. |
| `execution_realism/slippage_model.py` | Liquidity-bucket + size-bucket slippage. |
| `execution_realism/staleness_penalty.py` | Horizon-aware staleness zones (FRESH/AGING/STALE/EXPIRED). |
| `execution_realism/liquidity_model.py` | Liquidity quality assessment. |
| `execution_realism/fill_simulator.py` | FILLABLE / PARTIAL / UNFILLABLE fill simulation. |

## Signal Bridge (type definitions only)

| File | Purpose |
|------|---------|
| `signal_bridge/types.py` | `DirectionalSignal` (horizon enforced in `__post_init__`), `TradeSide`, `Polarity`, `TradeIntent`. |

## Config

| File | Purpose |
|------|---------|
| `config/loader.py` | Documents Python-as-source-of-truth policy. No runtime behavior. |

## Documentation

| File | Purpose |
|------|---------|
| `calibration/CONTRACT_HARDENING_SPEC.md` | Live vs paper contract differences, all type contracts, probability boundary policy. |
| `calibration/DECISION_INTEGRATION_SPEC.md` | Why split-brain is unacceptable, integration policies. |
| `calibration/DECISION_FLOW.md` | 12-step decision flow with I/O contract. |
| `PACKAGE_MANIFEST.md` | This file. |

## Tests

| Test File | Coverage |
|-----------|---------|
| `calibration/tests/test_live_contract_enforcement.py` | Live size requirement, bridge side contract, horizon at DirectionalSignal, calibration quality guards |
| `calibration/tests/test_probability_boundary.py` | Negative probs, over-sum, under-sum live (0.90), under-sum paper (0.50), missing class_probs |
| `calibration/tests/test_package_integrity.py` | Import graph, config source of truth, cold-import end-to-end |
| `calibration/tests/test_supported_horizons.py` | All unsupported horizons reject; Layer 1 + Layer 2 enforcement |
| `calibration/tests/test_type_contracts.py` | RawSignalOutput, CalibratedSignal, CalibrationConfig, DirectionalSignal, MarketPricingSnapshot contracts |
| `calibration/tests/test_phase10_contract_hardening.py` | min_prob_sum, intended_size, CalibrationConfig __post_init__ |
| `execution_realism/tests/test_phase11_partial_fill_economics.py` | PARTIAL fill gives lower EV than FILLABLE, penalty economics |
| `calibration/tests/test_phase9_integration.py` | Split-brain, spread attack, probability boundary |
| `calibration/tests/test_audit_breakdown.py` | TradeDecision audit trail fields |
| `calibration/tests/test_realistic_gate_priority.py` | Realistic gate supersedes simple gate |
| `calibration/tests/test_intended_size_contract.py` | Size→slippage monotonicity, UNFILLABLE |
| `calibration/tests/test_guardrails.py` | All 12 guards individually |
| `calibration/tests/test_live_vs_paper_policy.py` | Live vs paper config divergence |
| `calibration/tests/test_decision_policy_p6.py` | Horizon, bridge side, class probs |
| `calibration/tests/test_decision_path_integration.py` | End-to-end audit trail |
| `calibration/tests/test_probability_contracts.py` | Probability validation at mapper + decide |
| `calibration/tests/test_calibration.py` | Mapper unit tests |
| `calibration/tests/test_edge_estimation.py` | EdgeEstimate calculations |
| `calibration/tests/test_integration.py` | Full chain integration |
| `execution_realism/tests/test_*.py` | Slippage, staleness, fill, liquidity models |

---

## What Is NOT in This Package

- `agents/` — Orchestrator, signal agent (live trading layer)
- `core/` — Polymarket client, position manager
- `strategies/` — Kelly criterion (risk sizing)
- `backtesting/` — Historical simulation engine
- `web/` — Dashboard server
- `crypto_directional/` — ML signal generation

These modules are not required for the calibration decision path tests.

---

## Test Suite Status (Phase 10)

Run: `python -m pytest calibration/tests/ execution_realism/tests/ -q`

Expected: All tests pass. No prior-phase file dependencies required.
