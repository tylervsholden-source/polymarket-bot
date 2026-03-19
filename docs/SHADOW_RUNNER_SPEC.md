# Shadow Runner Specification

**Module:** `shadow_runner/`
**Phase:** 13 (runner), 14 (hardening)

## Purpose

The Shadow Runner records every market decision — EXECUTE and REJECT — to an
append-only JSONL journal. It enables:
- Drift monitoring across policy profiles
- Evidence collection for readiness gate
- Replay and audit

## Architecture

```
ShadowRunner (shadow_runner/runner.py)
  - Accepts list[CalibrationConfig] (one per policy profile)
  - For each (CalibratedSignal, MarketPricingSnapshot) candidate:
      → calls decide() for each config
      → wraps result in ShadowDecisionRecord
      → writes to JournalWriter
```

## Integration with Orchestrator

The live orchestrator does NOT call ShadowRunner.evaluate() directly.
It builds ShadowDecisionRecord objects from ArbitrageEngine output:
- Signals → EXECUTE_YES / EXECUTE_NO
- Non-signals → REJECT with "NO_SIGNAL_PRODUCED"

This is by design: ArbitrageEngine and the calibration pipeline are parallel
decision paths. The journal is the unifying audit layer.

## Journal File Format

- Location: `data/shadow_journal_YYYY-MM-DD.jsonl`
- Format: newline-delimited JSON (JSONL)
- Rotation: daily (new file per UTC date)
- Schema: see `JOURNAL_SCHEMA.md`
- Immutable: append-only, never modified after write

## Evidence Source Field

Every record has `evidence_source`:
- `"live_shadow"` — real market data, valid evidence for readiness gate
- `"demo"` — demo/testnet run, excluded from readiness gate
- `"synthetic"` — test fixtures, excluded from readiness gate

The readiness gate in `shadow_runner/validation.py` filters to
`evidence_source == "live_shadow"` only before counting evidence.

## Policy Profiles

Three profiles run against the same candidate:

| Profile | Mode | Notes |
|---|---|---|
| live | `LIVE_CAL_CONFIG` | Maximum strictness |
| paper_strict | `PAPER_STRICT_CAL_CONFIG` | Close to live |
| paper_loose | `PAPER_LOOSE_CAL_CONFIG` | Exploratory |

paper_loose results CANNOT justify going live.

## Readiness Gate (Phase 14)

`shadow_runner/validation.py`:
- min_live_like_evaluated: 100
- min_live_like_executes: 20
- min_live_like_rejects: 20
- min_observation_days: 5.0
- Rationale documented in module docstring

`shadow_runner/readiness.py`:
- 1 FAIL → NO_GO (Phase 14 change from CONDITIONAL)
- BLOCKER → always NO_GO
- Missing strict_metrics or regime_review → caps at CONDITIONAL_REVIEW
