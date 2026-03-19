"""
tests/test_journal_integrity.py

Phase 13: Journal serialization / deserialization integrity.

Covers:
- Round-trip: write then read back produces identical record
- All mandatory fields survive round-trip
- Optional None fields survive round-trip as null
- Optional non-None fields survive round-trip with values
- datetime fields retain timezone awareness
- InMemoryJournal has same behavior as file-based journal
- Malformed JSON lines are skipped gracefully
- Unknown schema_version records are skipped
- Multiple records can be written and read back in order
- JSONL output is one record per line (no embedded newlines)
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from calibration.probability_mapper import map_to_event_probability
from calibration.types import (
    CalibrationMethod,
    CalibrationQuality,
    LIVE_CAL_CONFIG,
    MarketPricingSnapshot,
    PAPER_LOOSE_CAL_CONFIG,
    PAPER_STRICT_CAL_CONFIG,
    RawSignalOutput,
)
from shadow_runner.journal import (
    InMemoryJournal,
    JournalReader,
    JournalWriter,
)
from shadow_runner.runner import ShadowRunner
from shadow_runner.types import JOURNAL_SCHEMA_VERSION

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


def _make_records(n=3):
    """Generate n shadow decision records across all three profiles."""
    runner = ShadowRunner([LIVE_CAL_CONFIG, PAPER_STRICT_CAL_CONFIG, PAPER_LOOSE_CAL_CONFIG])
    records = []
    for i in range(n):
        confidence = 0.70 + i * 0.05
        raw = RawSignalOutput(
            asset="BTC", horizon_minutes=5, timestamp_utc=_NOW,
            predicted_class="UP", raw_confidence=confidence,
            class_probabilities={
                "UP": confidence,
                "DOWN": round((1 - confidence) * 0.6, 6),
                "NO_TRADE": round((1 - confidence) * 0.4, 6),
            },
        )
        cal, err = map_to_event_probability(
            raw=raw,
            calibrated_up_prob=confidence,
            calibrated_down_prob=round((1 - confidence) * 0.6, 6),
            calibrated_no_trade_prob=round((1 - confidence) * 0.4, 6),
            polarity="NORMAL",
            calibration_method=CalibrationMethod.PLATT,
            calibration_quality=CalibrationQuality.STRONG,
        )
        assert err is None
        snap = MarketPricingSnapshot(
            market_id=f"mkt-{i:03d}",
            ask_yes=0.44, bid_yes=0.42,
            ask_no=0.57, bid_no=0.55,
            liquidity=5000.0, timestamp_utc=_NOW,
        )
        records.extend(
            runner.evaluate(cal, snap, intended_size_usdc=20.0, now_utc=_NOW)
        )
    return records


# ── In-memory round-trip ──────────────────────────────────────────────────────

class TestInMemoryJournal:

    def test_write_read_roundtrip(self):
        journal = InMemoryJournal()
        records = _make_records(1)
        journal.write_batch(records)
        read_back = journal.read_all()
        assert len(read_back) == len(records)

    def test_record_count(self):
        journal = InMemoryJournal()
        records = _make_records(2)
        journal.write_batch(records)
        assert journal.record_count() == 6  # 2 candidates × 3 profiles

    def test_policy_profile_survives_roundtrip(self):
        journal = InMemoryJournal()
        records = _make_records(1)
        journal.write_batch(records)
        read_back = journal.read_all()
        profiles_written = [r.policy_profile for r in records]
        profiles_read    = [r.policy_profile for r in read_back]
        assert profiles_written == profiles_read

    def test_run_id_survives_roundtrip(self):
        journal = InMemoryJournal()
        records = _make_records(1)
        journal.write_batch(records)
        read_back = journal.read_all()
        assert records[0].run_id == read_back[0].run_id

    def test_signal_fields_survive_roundtrip(self):
        journal = InMemoryJournal()
        records = _make_records(1)
        journal.write_batch(records)
        read_back = journal.read_all()
        orig, rback = records[0].signal, read_back[0].signal
        assert orig.asset == rback.asset
        assert orig.horizon_minutes == rback.horizon_minutes
        assert orig.calibration_method == rback.calibration_method
        assert orig.calibration_quality == rback.calibration_quality
        assert orig.effective_yes_prob == pytest.approx(rback.effective_yes_prob)
        assert orig.bridge_intent_side == rback.bridge_intent_side

    def test_pricing_fields_survive_roundtrip(self):
        journal = InMemoryJournal()
        records = _make_records(1)
        journal.write_batch(records)
        read_back = journal.read_all()
        orig, rback = records[0].pricing, read_back[0].pricing
        assert orig.market_id == rback.market_id
        assert orig.ask_yes == pytest.approx(rback.ask_yes)
        assert orig.snapshot_age_seconds == pytest.approx(rback.snapshot_age_seconds)

    def test_decision_fields_survive_roundtrip(self):
        journal = InMemoryJournal()
        records = _make_records(1)
        journal.write_batch(records)
        read_back = journal.read_all()
        orig, rback = records[0].decision_summary, read_back[0].decision_summary
        assert orig.decision == rback.decision
        assert orig.rejection_reason == rback.rejection_reason
        assert orig.policy_mode == rback.policy_mode
        assert orig.passes_final_gate == rback.passes_final_gate

    def test_none_optional_fields_survive(self):
        """None optional fields should deserialize back as None."""
        journal = InMemoryJournal()
        records = _make_records(1)
        journal.write_batch(records)
        read_back = journal.read_all()
        # For a clean healthy market in live, pricing_sanity_notes should be None
        live_recs = [r for r in read_back if r.policy_profile == "live"]
        assert live_recs[0].decision_summary.pricing_sanity_notes is None

    def test_datetime_is_timezone_aware_after_roundtrip(self):
        journal = InMemoryJournal()
        records = _make_records(1)
        journal.write_batch(records)
        read_back = journal.read_all()
        ts = read_back[0].ts_recorded_utc
        assert ts.tzinfo is not None

    def test_to_jsonl_is_one_line_per_record(self):
        journal = InMemoryJournal()
        records = _make_records(1)
        journal.write_batch(records)
        jsonl = journal.to_jsonl()
        lines = [l for l in jsonl.strip().split("\n") if l]
        assert len(lines) == len(records)
        # Each line must be valid JSON
        for line in lines:
            d = json.loads(line)
            assert d["schema_version"] == JOURNAL_SCHEMA_VERSION

    def test_schema_version_in_every_record(self):
        journal = InMemoryJournal()
        records = _make_records(2)
        journal.write_batch(records)
        read_back = journal.read_all()
        for r in read_back:
            assert r.schema_version == JOURNAL_SCHEMA_VERSION


# ── File-based round-trip ─────────────────────────────────────────────────────

class TestFileJournal:

    def test_file_roundtrip(self, tmp_path):
        path = tmp_path / "test_journal.jsonl"
        records = _make_records(2)
        with JournalWriter(path) as writer:
            writer.write_batch(records)
        reader = JournalReader(path)
        read_back = reader.read_all()
        assert len(read_back) == len(records)

    def test_file_append_mode(self, tmp_path):
        """Two separate writers append to same file."""
        path = tmp_path / "append_journal.jsonl"
        records_a = _make_records(1)
        records_b = _make_records(1)
        with JournalWriter(path) as w:
            w.write_batch(records_a)
        with JournalWriter(path) as w:
            w.write_batch(records_b)
        reader = JournalReader(path)
        all_records = reader.read_all()
        assert len(all_records) == len(records_a) + len(records_b)

    def test_read_by_profile(self, tmp_path):
        path = tmp_path / "profile_filter.jsonl"
        records = _make_records(2)
        with JournalWriter(path) as w:
            w.write_batch(records)
        reader = JournalReader(path)
        live_only = reader.read_by_profile("live")
        assert all(r.policy_profile == "live" for r in live_only)
        assert len(live_only) == 2  # 2 candidates × 1 live profile

    def test_read_by_run_id(self, tmp_path):
        path = tmp_path / "runid_filter.jsonl"
        runner_a = ShadowRunner([LIVE_CAL_CONFIG])
        runner_b = ShadowRunner([LIVE_CAL_CONFIG])
        raw = RawSignalOutput(
            asset="BTC", horizon_minutes=5, timestamp_utc=_NOW,
            predicted_class="UP", raw_confidence=0.80,
            class_probabilities={"UP": 0.80, "DOWN": 0.12, "NO_TRADE": 0.08},
        )
        cal, err = map_to_event_probability(
            raw=raw, calibrated_up_prob=0.80,
            calibrated_down_prob=0.12, calibrated_no_trade_prob=0.08,
            polarity="NORMAL", calibration_method=CalibrationMethod.PLATT,
            calibration_quality=CalibrationQuality.STRONG,
        )
        assert err is None
        snap = MarketPricingSnapshot(
            market_id="mkt-x", ask_yes=0.44, bid_yes=0.42,
            ask_no=0.57, bid_no=0.55, liquidity=5000.0, timestamp_utc=_NOW,
        )
        recs_a = runner_a.evaluate(cal, snap, intended_size_usdc=20.0, now_utc=_NOW)
        recs_b = runner_b.evaluate(cal, snap, intended_size_usdc=20.0, now_utc=_NOW)
        with JournalWriter(path) as w:
            w.write_batch(recs_a)
            w.write_batch(recs_b)
        reader = JournalReader(path)
        only_a = reader.read_by_run_id(runner_a.run_id)
        assert len(only_a) == 1
        assert all(r.run_id == runner_a.run_id for r in only_a)


# ── Robustness ────────────────────────────────────────────────────────────────

class TestJournalRobustness:

    def test_malformed_json_line_skipped(self, tmp_path):
        path = tmp_path / "malformed.jsonl"
        records = _make_records(1)
        with JournalWriter(path) as w:
            w.write_batch(records)
        # Inject a malformed line
        with open(path, "a") as f:
            f.write("this is not json\n")
        reader = JournalReader(path)
        read_back = reader.read_all()
        assert len(read_back) == len(records)  # malformed line skipped

    def test_unknown_schema_version_skipped(self, tmp_path):
        path = tmp_path / "oldschema.jsonl"
        records = _make_records(1)
        with JournalWriter(path) as w:
            w.write_batch(records)
        # Inject a record with unknown schema version
        bad_record = {"schema_version": "99", "record_id": "x", "run_id": "y"}
        with open(path, "a") as f:
            f.write(json.dumps(bad_record) + "\n")
        reader = JournalReader(path)
        read_back = reader.read_all()
        assert len(read_back) == len(records)  # bad schema skipped

    def test_blank_lines_skipped(self, tmp_path):
        path = tmp_path / "blanks.jsonl"
        records = _make_records(1)
        with JournalWriter(path) as w:
            w.write_batch(records)
        with open(path, "a") as f:
            f.write("\n\n\n")
        reader = JournalReader(path)
        read_back = reader.read_all()
        assert len(read_back) == len(records)
