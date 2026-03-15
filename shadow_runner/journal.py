"""
shadow_runner/journal.py

Phase 13: Structured decision journal — JSONL format.

Design decisions:
  1. Format: newline-delimited JSON (JSONL). One record per line.
     - Human-readable, grep-able, appendable without full file rewrite.
     - Parquet export deferred to Phase 14 (noted in SHADOW_RUNNER_SPEC.md).
  2. JournalWriter is append-only. Existing journal files are never modified.
     Each runner session appends; do not truncate.
  3. datetime fields are serialized as ISO-8601 UTC strings.
     On read, they are parsed back to timezone-aware datetime objects.
  4. None values are serialized as JSON null. All Optional fields are written.
  5. JournalReader yields records lazily (generator) — safe for large files.
  6. Schema version is stored in every record. Reader silently skips records
     with unknown schema versions (logs a warning instead of crashing).
  7. Journal files are named by run_id by default:
       journal_{run_id}.jsonl
     Custom paths are also supported.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from shadow_runner.types import (
    DecisionSummary,
    JOURNAL_SCHEMA_VERSION,
    PricingSnapshot,
    ShadowDecisionRecord,
    SignalSnapshot,
)


# ── Integrity stats ───────────────────────────────────────────────────────────

@dataclass
class JournalIntegrityStats:
    """
    Summary of parse results from a JournalReader pass.

    bad_line_fraction = (bad_json_count + version_skip_count) / max(total_lines, 1)
    """
    total_lines: int
    parsed_ok: int
    bad_json_count: int
    version_skip_count: int
    bad_line_fraction: float


# ── Serialization ─────────────────────────────────────────────────────────────

def _to_dict(record: ShadowDecisionRecord) -> dict:
    """Convert a ShadowDecisionRecord to a JSON-serializable dict."""
    sig = record.signal
    pri = record.pricing
    dec = record.decision_summary

    return {
        "schema_version":    record.schema_version,
        "record_id":         record.record_id,
        "run_id":            record.run_id,
        "ts_recorded_utc":   _dt_to_str(record.ts_recorded_utc),
        "policy_profile":    record.policy_profile,
        "intended_size_usdc": record.intended_size_usdc,
        "evidence_source":   record.evidence_source,

        "signal": {
            "asset":                    sig.asset,
            "horizon_minutes":          sig.horizon_minutes,
            "signal_timestamp_utc":     _dt_to_str(sig.signal_timestamp_utc),
            "predicted_class":          sig.predicted_class,
            "raw_confidence":           sig.raw_confidence,
            "class_probabilities":      sig.class_probabilities,
            "model_version":            sig.model_version,
            "calibrated_up_prob":       sig.calibrated_up_prob,
            "calibrated_down_prob":     sig.calibrated_down_prob,
            "calibrated_no_trade_prob": sig.calibrated_no_trade_prob,
            "calibration_method":       sig.calibration_method,
            "calibration_quality":      sig.calibration_quality,
            "effective_yes_prob":       sig.effective_yes_prob,
            "effective_no_prob":        sig.effective_no_prob,
            "mapping_context":          sig.mapping_context,
            "bridge_intent_side":       sig.bridge_intent_side,
            "brier_score":              sig.brier_score,
            "ece":                      sig.ece,
        },

        "pricing": {
            "market_id":              pri.market_id,
            "ask_yes":                pri.ask_yes,
            "bid_yes":                pri.bid_yes,
            "ask_no":                 pri.ask_no,
            "bid_no":                 pri.bid_no,
            "liquidity":              pri.liquidity,
            "pricing_timestamp_utc":  _dt_to_str(pri.pricing_timestamp_utc),
            "snapshot_age_seconds":   pri.snapshot_age_seconds,
        },

        "decision": {
            "decision":                 dec.decision,
            "rejection_reason":         dec.rejection_reason,
            "policy_mode":              dec.policy_mode,
            "passes_final_gate":        dec.passes_final_gate,
            "intended_size_usdc_used":  dec.intended_size_usdc_used,
            "pricing_sanity_notes":     dec.pricing_sanity_notes,
            "gross_ev":                 dec.gross_ev,
            "net_ev_after_fee":         dec.net_ev_after_fee,
            "execution_adjusted_ev":    dec.execution_adjusted_ev,
            "required_edge_threshold":  dec.required_edge_threshold,
            "fill_fraction":            dec.fill_fraction,
        },
    }


def _from_dict(d: dict) -> Optional[ShadowDecisionRecord]:
    """
    Parse a dict back into a ShadowDecisionRecord.
    Returns None if schema_version is unrecognized.
    """
    if d.get("schema_version") != JOURNAL_SCHEMA_VERSION:
        return None  # caller logs and skips

    sig_d = d["signal"]
    pri_d = d["pricing"]
    dec_d = d["decision"]

    signal = SignalSnapshot(
        asset=sig_d["asset"],
        horizon_minutes=sig_d["horizon_minutes"],
        signal_timestamp_utc=_str_to_dt(sig_d["signal_timestamp_utc"]),
        predicted_class=sig_d["predicted_class"],
        raw_confidence=sig_d["raw_confidence"],
        class_probabilities=sig_d.get("class_probabilities"),
        model_version=sig_d.get("model_version", "v0"),
        calibrated_up_prob=sig_d["calibrated_up_prob"],
        calibrated_down_prob=sig_d["calibrated_down_prob"],
        calibrated_no_trade_prob=sig_d["calibrated_no_trade_prob"],
        calibration_method=sig_d["calibration_method"],
        calibration_quality=sig_d["calibration_quality"],
        effective_yes_prob=sig_d["effective_yes_prob"],
        effective_no_prob=sig_d["effective_no_prob"],
        mapping_context=sig_d.get("mapping_context", ""),
        bridge_intent_side=sig_d["bridge_intent_side"],
        brier_score=sig_d.get("brier_score"),
        ece=sig_d.get("ece"),
    )

    pricing = PricingSnapshot(
        market_id=pri_d["market_id"],
        ask_yes=pri_d["ask_yes"],
        bid_yes=pri_d["bid_yes"],
        ask_no=pri_d["ask_no"],
        bid_no=pri_d["bid_no"],
        liquidity=pri_d["liquidity"],
        pricing_timestamp_utc=_str_to_dt(pri_d["pricing_timestamp_utc"]),
        snapshot_age_seconds=pri_d["snapshot_age_seconds"],
    )

    decision = DecisionSummary(
        decision=dec_d["decision"],
        rejection_reason=dec_d.get("rejection_reason"),
        policy_mode=dec_d["policy_mode"],
        passes_final_gate=dec_d["passes_final_gate"],
        intended_size_usdc_used=dec_d["intended_size_usdc_used"],
        pricing_sanity_notes=dec_d.get("pricing_sanity_notes"),
        gross_ev=dec_d.get("gross_ev"),
        net_ev_after_fee=dec_d.get("net_ev_after_fee"),
        execution_adjusted_ev=dec_d.get("execution_adjusted_ev"),
        required_edge_threshold=dec_d.get("required_edge_threshold"),
        fill_fraction=dec_d.get("fill_fraction"),
    )

    return ShadowDecisionRecord(
        record_id=d["record_id"],
        run_id=d["run_id"],
        ts_recorded_utc=_str_to_dt(d["ts_recorded_utc"]),
        schema_version=d["schema_version"],
        signal=signal,
        pricing=pricing,
        policy_profile=d["policy_profile"],
        intended_size_usdc=d.get("intended_size_usdc", 20.0),
        evidence_source=d.get("evidence_source", "live_shadow"),
        decision_summary=decision,
    )


# ── Datetime helpers ──────────────────────────────────────────────────────────

def _dt_to_str(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _str_to_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ── Writer ────────────────────────────────────────────────────────────────────

class JournalWriter:
    """
    Appends ShadowDecisionRecords to a JSONL journal file.

    Thread-safety: not thread-safe. Use one writer per thread/process.

    Parameters
    ----------
    path : str | Path
        Path to journal file. File is created if it does not exist.
        Existing content is preserved (append mode).
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._path, "a", encoding="utf-8")

    def write(self, record: ShadowDecisionRecord) -> None:
        """Write a single record as one JSONL line."""
        line = json.dumps(_to_dict(record), ensure_ascii=False)
        self._file.write(line + "\n")

    def write_batch(self, records: list[ShadowDecisionRecord]) -> None:
        """Write a batch of records."""
        for record in records:
            self.write(record)

    def flush(self) -> None:
        """Flush the underlying file buffer."""
        self._file.flush()

    def close(self) -> None:
        """Flush and close the file."""
        self._file.flush()
        self._file.close()

    def __enter__(self) -> "JournalWriter":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# ── Reader ────────────────────────────────────────────────────────────────────

class JournalReader:
    """
    Reads ShadowDecisionRecords from a JSONL journal file.

    Records with unrecognized schema versions are skipped.

    Usage
    -----
        reader = JournalReader(path)
        for record in reader.iter_records():
            ...

        # Or load all into memory:
        records = reader.read_all()
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def iter_records(self) -> Iterator[ShadowDecisionRecord]:
        """Yield records lazily. Skips blank lines and bad JSON."""
        with open(self._path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue  # skip malformed lines
                record = _from_dict(d)
                if record is None:
                    continue  # skip unknown schema version
                yield record

    def read_all(self) -> list[ShadowDecisionRecord]:
        """Load all valid records into memory."""
        return list(self.iter_records())

    def read_all_with_integrity(
        self,
    ) -> tuple[list[ShadowDecisionRecord], JournalIntegrityStats]:
        """
        Load all valid records and return integrity statistics.

        Unlike read_all(), this also reports how many lines were skipped
        due to bad JSON or unknown schema version.

        Returns
        -------
        (records, stats)
            records : list of successfully parsed ShadowDecisionRecords
            stats   : JournalIntegrityStats with counts of skipped lines
        """
        records: list[ShadowDecisionRecord] = []
        total_lines = 0
        bad_json_count = 0
        version_skip_count = 0

        with open(self._path, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                total_lines += 1
                try:
                    d = json.loads(stripped)
                except json.JSONDecodeError:
                    bad_json_count += 1
                    continue
                record = _from_dict(d)
                if record is None:
                    version_skip_count += 1
                    continue
                records.append(record)

        bad_total = bad_json_count + version_skip_count
        bad_fraction = bad_total / total_lines if total_lines > 0 else 0.0
        stats = JournalIntegrityStats(
            total_lines=total_lines,
            parsed_ok=len(records),
            bad_json_count=bad_json_count,
            version_skip_count=version_skip_count,
            bad_line_fraction=bad_fraction,
        )
        return records, stats

    def read_by_run_id(self, run_id: str) -> list[ShadowDecisionRecord]:
        """Load all records matching a specific run_id."""
        return [r for r in self.iter_records() if r.run_id == run_id]

    def read_by_profile(self, policy_profile: str) -> list[ShadowDecisionRecord]:
        """Load all records for a specific policy profile."""
        return [r for r in self.iter_records() if r.policy_profile == policy_profile]


# ── In-memory buffer (for testing without file I/O) ──────────────────────────

class InMemoryJournal:
    """
    JSONL journal backed by an in-memory list.

    Useful for tests that don't want file system side effects.
    Has the same write/read interface as JournalWriter + JournalReader.
    """

    def __init__(self) -> None:
        self._lines: list[str] = []

    def write(self, record: ShadowDecisionRecord) -> None:
        self._lines.append(json.dumps(_to_dict(record), ensure_ascii=False))

    def write_batch(self, records: list[ShadowDecisionRecord]) -> None:
        for r in records:
            self.write(r)

    def iter_records(self) -> Iterator[ShadowDecisionRecord]:
        for line in self._lines:
            d = json.loads(line)
            record = _from_dict(d)
            if record is not None:
                yield record

    def read_all(self) -> list[ShadowDecisionRecord]:
        return list(self.iter_records())

    def record_count(self) -> int:
        return len(self._lines)

    def to_jsonl(self) -> str:
        """Return the full journal as a JSONL string."""
        return "\n".join(self._lines) + ("\n" if self._lines else "")
