# Decision Integration Specification

**Question answered:** How does a candidate market go from "scanned" to "journal record"?

## Two Parallel Decision Paths

### Path A: Live Orchestrator (ArbitrageEngine)

```
orchestrator._cycle()
  ↓
ArbitrageEngine.analyze(candidates)
  ↓ for each candidate
  BayesianEstimator → P(UP|indicators)
  EdgeModel → edge = P(UP) - ask_price
  StoikovModel → fill_fraction, executable_notional
  KellyCriterion → size = kelly_fraction * capital
  SmartTraderTracker → ±0.05 boost if smart traders agree
  ↓
  produces: TradeSignal or nothing (too low edge / liquidity)
  ↓
orchestrator._record_shadow_decisions(candidates, signals)
  ↓ for each candidate
  if signal exists: ShadowDecisionRecord(decision=EXECUTE_YES/EXECUTE_NO)
  else:             ShadowDecisionRecord(decision=REJECT, reason=NO_SIGNAL_PRODUCED)
  ↓
JournalWriter.write(record) → data/shadow_journal_YYYY-MM-DD.jsonl
```

### Path B: ShadowRunner (Calibration Pipeline)

```
ShadowRunner.evaluate(cal_signal, pricing)
  ↓ for each CalibrationConfig
  calibration.decision_policy.decide(cal_signal, pricing, config=config)
    ↓
    _check_binary_sanity() → pricing sanity
    _compute_edge() → gross_ev, fill_fraction
    _apply_policy() → apply calibration quality gates
    _check_final_gate() → execution_adjusted_ev >= min_edge?
  ↓
  ShadowDecisionRecord wrapping TradeDecision
  ↓
JournalWriter (optional)
```

Path B is used in tests and research. Path A is used in production.

## Shadow Recording Design Decision

The orchestrator uses Path A (simplified record construction) rather than
calling ShadowRunner.evaluate() directly, because:

1. ArbitrageEngine's internals don't produce CalibratedSignal objects
2. Feeding ArbitrageEngine output through the calibration pipeline would
   require a round-trip through signal_bridge that doesn't exist yet
3. The journal records from both paths use the same ShadowDecisionRecord type
   so they're compatible with the same monitoring infrastructure

This is not a gap — it's an explicit architectural choice. The journal remains
the unified audit layer regardless of which decision path produced the record.

## Signal Fields in Journal (live orchestrator path)

When orchestrator writes records, some calibration-specific fields are populated
with reasonable defaults or left as defaults from the orchestrator's Bayesian output:
- `calibration_method = "identity"` (no separate calibration step)
- `calibration_quality = "unknown"` initially
- `effective_yes_prob` = bayesian_prob (for YES trades)
- `effective_no_prob` = 1 - bayesian_prob

These defaults are honest about what the live path does.
The calibration pipeline test records have richer calibration data.
