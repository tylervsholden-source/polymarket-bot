# Shadow Runner Specification
## Phase 13 — Effective 2026-03-15

---

## Purpose

The Shadow Runner runs the full real decision pipeline against live market data
without placing any real orders. It produces a structured decision journal that
enables:

1. **Drift monitoring** — detect when rejection patterns or EV distributions change
2. **Policy comparison** — run live / paper_strict / paper_loose in parallel on the same inputs
3. **Replay verification** — prove the pipeline is deterministic
4. **Strategy audit** — full record of every candidate evaluated, whether EXECUTE or REJECT

---

## Architecture

```
Live market feed
    │
    ▼
CalibratedSignal + MarketPricingSnapshot
    │
    ▼
ShadowRunner.evaluate(cal_signal, pricing, ...)
    │
    ├──► config=LIVE_CAL_CONFIG      ──► decide() ──► ShadowDecisionRecord(profile="live")
    ├──► config=PAPER_STRICT_CAL_CONFIG ──► decide() ──► ShadowDecisionRecord(profile="paper_strict")
    └──► config=PAPER_LOOSE_CAL_CONFIG  ──► decide() ──► ShadowDecisionRecord(profile="paper_loose")
    │
    ▼
JournalWriter.write_batch(records)  ──► journal_{run_id}.jsonl
    │
    ▼
DriftMonitor.check(current_window)  ──► DriftReport
    │
    ▼
AlertEngine.check(report)           ──► list[DriftAlert]
```

---

## Core Invariant

**The shadow runner uses the SAME `decide()` function as live execution.**

No stubs. No mocks. If `decide()` is changed, shadow runner behavior changes in lockstep.
This guarantees that shadow results are valid predictions of live behavior.

---

## What Is Journaled

Every candidate that enters `ShadowRunner.evaluate()` produces one journal record
per policy profile — regardless of whether the decision is EXECUTE or REJECT.

**Recording every reject is mandatory**, not optional. Rejects carry:
- Rejection reason distribution (needed for drift monitoring)
- Early-stage reject rates (calibration quality, staleness) vs late-stage (edge)
- Information about what the pipeline is filtering out

---

## Three-Profile Parallel Run

Standard deployment runs three profiles simultaneously:

| Profile        | Purpose                                         |
|----------------|-------------------------------------------------|
| `live`         | Shadow of live deployment — strictest           |
| `paper_strict` | Honest research — informs go-live decisions     |
| `paper_loose`  | Exploratory — observational only                |

All three profiles receive identical inputs (same `cal_signal`, `pricing`, `now_utc`).
The three decisions for a single candidate are linked by `(run_id, record_id prefix)`.
They are matched for cross-profile comparison using `(asset, market_id, signal_timestamp_utc)`.

---

## Determinism

The pipeline is **deterministic**: given the same inputs, `decide()` always
returns the same output. Shadow runner stores all inputs needed to prove this:

- `signal.*` — all fields of `CalibratedSignal` (and its embedded `RawSignalOutput`)
- `pricing.*` — all fields of `MarketPricingSnapshot`
- `pricing.snapshot_age_seconds` — age at the moment `decide()` was called
- `policy_profile` — which `CalibrationConfig` was used
- `intended_size_usdc` — trade size parameter

Replay reconstructs `now_utc` as:
```
now_utc = pricing_timestamp_utc + snapshot_age_seconds
```

This reproduces the exact staleness the original decision saw.

---

## Files

| File | Role |
|------|------|
| `shadow_runner/types.py` | Data models: ShadowDecisionRecord, ShadowRunConfig, etc. |
| `shadow_runner/runner.py` | ShadowRunner — multi-profile execution |
| `shadow_runner/journal.py` | JournalWriter / JournalReader / InMemoryJournal |
| `shadow_runner/replay.py` | Deterministic replay verification |
| `shadow_runner/reporting.py` | Policy profile comparison reports |
| `monitoring/metrics.py` | Metric extractors (rejection, EV, divergence) |
| `monitoring/drift_monitor.py` | Baseline vs current window comparison |
| `monitoring/alerts.py` | Alert rules and triggering |

---

## Explicit Policy Choices

### 1. Journal every candidate (EXECUTE and REJECT alike)
**Rationale:** Rejects contain the most drift-sensitive information. A sudden increase
in WEAK_CALIBRATION rejects indicates the upstream model is degrading. Only journaling
executes would miss this signal entirely.

### 2. Parallel multi-profile runs on same input
**Rationale:** Cross-profile divergence is the primary tool for understanding whether
an apparent edge is real (passes paper_strict) or observational only (only passes
paper_loose). Running on the same input ensures any divergence is due to policy
differences, not market timing differences.

### 3. Replay re-computes (does not compare raw output)
**Rationale:** Re-computing from stored inputs proves both (a) determinism and (b)
journal completeness. If replay just compared stored JSON strings, it would catch
serialization bugs but not prove the decision chain is reproducible.

### 4. Mandatory journal fields
See JOURNAL_SCHEMA.md for the full field list and rationale.

### 5. Drift alerts use relative (absolute pp delta) thresholds, not Z-scores
**Rationale:** Statistical tests (Z-score, chi-squared) require distributional assumptions
about the underlying signal. The rejection rate is not normally distributed in this regime.
Absolute percentage-point thresholds are simpler, interpretable, and less prone to false
alarms from distributional misspecification.

---

## Deferred to Phase 14

- **Parquet export**: JSONL works for logging; columnar format needed for analytical queries.
- **Streaming EV drift (EWMA)**: Current window-based comparison requires explicit baseline.
  EWMA would enable continuous monitoring without explicit baseline management.
- **Per-reason drift alerts**: Current rejection_rate alert fires on total rate.
  Per-reason drift (e.g., STALE_PRICING specifically rising) would be more actionable.
- **Live vs paper_strict EV gap metric**: Useful for detecting execution risk not captured in model.
- **Automatic baseline rotation**: Currently baseline is fixed at construction time.
  Auto-rotating every N records to prevent stale baselines.
