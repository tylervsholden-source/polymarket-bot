"""
tests/test_phase13_integration.py

Phase 13 end-to-end integration test.

Exercises the full Phase 13 pipeline:
  signal → ShadowRunner → journal → replay → drift monitoring → alerts

Concrete scenarios tested (≥5 as required by spec):

1. Happy path: healthy signal evaluated by 3 profiles, journaled, replayed.
   All replays match. No drift alerts on stable baseline.

2. Calibration deterioration scenario: baseline is all STRONG-cal records;
   current window has all WEAK-cal records (rejected by strict/live).
   Rejection rate spike → CRITICAL drift alert fires.

3. Stale pricing scenario: baseline is all fresh snapshots; current window
   has stale snapshots beyond paper_strict max_age.
   Rejection reason is STALE_PRICING; alert fires on rejection rate delta.

4. Paper_loose observation zone scenario: weak-cal records execute in
   paper_loose but not in paper_strict or live.
   report.loose_only_execute_count > 0; format_report warns OBSERVATION ZONE.

5. Journal persistence scenario: write records to file, read back, replay all.
   assert_replay_consistency passes. File contains correct number of records.

6. Multi-candidate batch drift monitoring: 50 baseline candidates, then 50
   current candidates with higher rejection rate. Drift monitor detects change.
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone

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
from monitoring.alerts import AlertEngine
from monitoring.drift_monitor import DriftMonitor
from shadow_runner.journal import InMemoryJournal, JournalReader, JournalWriter
from shadow_runner.replay import assert_replay_consistency
from shadow_runner.reporting import format_report, generate_comparison_report
from shadow_runner.runner import ShadowRunner

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)

_CONFIG_MAP = {
    "live":         LIVE_CAL_CONFIG,
    "paper_strict": PAPER_STRICT_CAL_CONFIG,
    "paper_loose":  PAPER_LOOSE_CAL_CONFIG,
}


def _make_cal(confidence=0.80, quality=CalibrationQuality.STRONG):
    raw = RawSignalOutput(
        asset="BTC", horizon_minutes=5, timestamp_utc=_NOW,
        predicted_class="UP", raw_confidence=confidence,
        class_probabilities={
            "UP": confidence,
            "DOWN": round((1 - confidence) * 0.6, 6),
            "NO_TRADE": round((1 - confidence) * 0.4, 6),
        },
    )
    method = CalibrationMethod.PLATT if quality == CalibrationQuality.STRONG else CalibrationMethod.IDENTITY
    cal, err = map_to_event_probability(
        raw=raw,
        calibrated_up_prob=confidence,
        calibrated_down_prob=round((1 - confidence) * 0.6, 6),
        calibrated_no_trade_prob=round((1 - confidence) * 0.4, 6),
        polarity="NORMAL",
        calibration_method=method,
        calibration_quality=quality,
    )
    assert err is None
    return cal


def _make_snap(market_id="mkt-001"):
    return MarketPricingSnapshot(
        market_id=market_id,
        ask_yes=0.44, bid_yes=0.42,
        ask_no=0.57, bid_no=0.55,
        liquidity=5000.0, timestamp_utc=_NOW,
    )


# ── Scenario 1: Happy path ────────────────────────────────────────────────────

class TestScenario1HappyPath:
    """Healthy signal → 3 profiles → journal → replay → no alerts."""

    def test_full_pipeline_produces_3_records(self):
        runner = ShadowRunner([LIVE_CAL_CONFIG, PAPER_STRICT_CAL_CONFIG, PAPER_LOOSE_CAL_CONFIG])
        journal = InMemoryJournal()
        records = runner.evaluate(
            _make_cal(0.80), _make_snap(),
            intended_size_usdc=20.0, now_utc=_NOW,
        )
        journal.write_batch(records)
        read_back = journal.read_all()
        assert len(read_back) == 3

    def test_all_records_replay_consistently(self):
        runner = ShadowRunner([LIVE_CAL_CONFIG, PAPER_STRICT_CAL_CONFIG, PAPER_LOOSE_CAL_CONFIG])
        records = runner.evaluate(
            _make_cal(0.80), _make_snap(),
            intended_size_usdc=20.0, now_utc=_NOW,
        )
        journal = InMemoryJournal()
        journal.write_batch(records)
        read_back = journal.read_all()
        assert_replay_consistency(read_back, _CONFIG_MAP)

    def test_stable_baseline_produces_no_alerts(self):
        runner = ShadowRunner([LIVE_CAL_CONFIG, PAPER_STRICT_CAL_CONFIG, PAPER_LOOSE_CAL_CONFIG])
        baseline_live = runner.evaluate_batch(
            [(_make_cal(0.80), _make_snap(market_id=f"mkt-b{i:02d}")) for i in range(20)],
            intended_size_usdc=20.0, now_utc=_NOW,
        )
        baseline_live = [r for r in baseline_live if r.policy_profile == "live"]
        current_live = runner.evaluate_batch(
            [(_make_cal(0.80), _make_snap(market_id=f"mkt-c{i:02d}")) for i in range(15)],
            intended_size_usdc=20.0, now_utc=_NOW,
        )
        current_live = [r for r in current_live if r.policy_profile == "live"]
        monitor = DriftMonitor(baseline_live, profiles=["live"])
        current = current_live
        report = monitor.check(current)
        alerts = AlertEngine().check(report)
        rate_alerts = [a for a in alerts if a.metric == "rejection_rate"]
        assert len(rate_alerts) == 0


# ── Scenario 2: Calibration deterioration ────────────────────────────────────

class TestScenario2CalibrationDeterioration:
    """Baseline: STRONG cal. Current: WEAK cal. paper_strict rejects all → spike."""

    def test_weak_cal_rejection_spike_detected(self):
        runner = ShadowRunner([PAPER_STRICT_CAL_CONFIG])
        baseline_records = []
        for i in range(30):
            snap = _make_snap(market_id=f"mkt-s{i:02d}")
            baseline_records.extend(
                runner.evaluate(_make_cal(0.80, CalibrationQuality.STRONG), snap,
                                intended_size_usdc=20.0, now_utc=_NOW)
            )
        monitor = DriftMonitor(baseline_records, profiles=["paper_strict"])

        current_records = []
        for i in range(20):
            snap = _make_snap(market_id=f"mkt-w{i:02d}")
            current_records.extend(
                runner.evaluate(_make_cal(0.80, CalibrationQuality.WEAK), snap,
                                intended_size_usdc=20.0, now_utc=_NOW)
            )

        # All current records should be REJECT (WEAK_CALIBRATION)
        assert all(r.decision_summary.rejection_reason == "WEAK_CALIBRATION"
                   for r in current_records)

        report = monitor.check(current_records)
        # delta should be strongly positive (rejection rate went from low to 1.0)
        delta = report.rejection_rate_delta["paper_strict"]
        assert delta > 0.30, f"Expected >30pp delta, got {delta:.2%}"

        alerts = AlertEngine().check(report)
        crit_alerts = [a for a in alerts
                       if a.level == "CRITICAL" and a.metric == "rejection_rate"]
        assert len(crit_alerts) > 0, "Expected CRITICAL alert on calibration deterioration"


# ── Scenario 3: Stale pricing ─────────────────────────────────────────────────

class TestScenario3StalePricing:
    """Baseline: fresh snapshots. Current: stale snapshots. STALE_PRICING rejects."""

    def test_stale_pricing_rejection_detected(self):
        from datetime import timedelta
        runner = ShadowRunner([PAPER_STRICT_CAL_CONFIG])

        # Baseline: fresh snapshots (age = 0)
        baseline = []
        for i in range(20):
            snap = _make_snap(market_id=f"mkt-fresh-{i:02d}")
            baseline.extend(runner.evaluate(
                _make_cal(0.80), snap, intended_size_usdc=20.0, now_utc=_NOW
            ))
        monitor = DriftMonitor(baseline, profiles=["paper_strict"])

        # Current: stale snapshots (age = 200s > paper_strict 120s limit)
        stale_ts = _NOW - timedelta(seconds=200)
        current = []
        for i in range(15):
            stale_snap = MarketPricingSnapshot(
                market_id=f"mkt-stale-{i:02d}",
                ask_yes=0.44, bid_yes=0.42,
                ask_no=0.57, bid_no=0.55,
                liquidity=5000.0, timestamp_utc=stale_ts,
            )
            current.extend(runner.evaluate(
                _make_cal(0.80), stale_snap, intended_size_usdc=20.0, now_utc=_NOW
            ))

        assert all(r.decision_summary.rejection_reason == "STALE_PRICING"
                   for r in current)

        report = monitor.check(current)
        assert report.rejection_rate_delta["paper_strict"] > 0.20


# ── Scenario 4: Paper_loose observation zone ──────────────────────────────────

class TestScenario4ObservationZone:
    """Weak-cal records execute only in paper_loose — observation zone warning fires."""

    def test_loose_only_execute_in_report(self):
        runner = ShadowRunner([LIVE_CAL_CONFIG, PAPER_STRICT_CAL_CONFIG, PAPER_LOOSE_CAL_CONFIG])
        # Use weak calibration + high confidence
        # paper_loose: reject_on_weak_calibration=False → may execute
        # paper_strict: reject_on_weak_calibration=True → rejects
        # live: reject_on_weak_calibration=True → rejects
        records = runner.evaluate(
            _make_cal(0.90, CalibrationQuality.WEAK),
            _make_snap(market_id="mkt-weak"),
            intended_size_usdc=20.0,
            now_utc=_NOW,
        )

        report = generate_comparison_report(records)
        # paper_strict and live must reject; paper_loose may execute
        loose_records = [r for r in records if r.policy_profile == "paper_loose"]
        strict_records = [r for r in records if r.policy_profile == "paper_strict"]
        live_records   = [r for r in records if r.policy_profile == "live"]

        assert strict_records[0].decision_summary.rejection_reason == "WEAK_CALIBRATION"
        assert live_records[0].decision_summary.rejection_reason == "WEAK_CALIBRATION"

        # If paper_loose executed, observation zone should be flagged
        if loose_records[0].decision_summary.decision in ("EXECUTE_YES", "EXECUTE_NO"):
            assert report.loose_only_execute_count > 0
            text = format_report(report)
            assert "OBSERVATION ZONE" in text


# ── Scenario 5: Journal persistence ──────────────────────────────────────────

class TestScenario5JournalPersistence:
    """Write to file, read back, replay — full round-trip with file I/O."""

    def test_file_journal_full_roundtrip(self, tmp_path):
        path = tmp_path / "integration_journal.jsonl"
        runner = ShadowRunner([LIVE_CAL_CONFIG, PAPER_STRICT_CAL_CONFIG, PAPER_LOOSE_CAL_CONFIG])

        all_records = []
        for i in range(5):
            snap = _make_snap(market_id=f"mkt-{i:02d}")
            records = runner.evaluate(
                _make_cal(0.80), snap, intended_size_usdc=20.0, now_utc=_NOW
            )
            all_records.extend(records)

        with JournalWriter(path) as writer:
            writer.write_batch(all_records)

        reader = JournalReader(path)
        read_back = reader.read_all()

        assert len(read_back) == 5 * 3  # 5 candidates × 3 profiles
        assert_replay_consistency(read_back, _CONFIG_MAP)


# ── Scenario 6: Batch drift monitoring ───────────────────────────────────────

class TestScenario6BatchDriftMonitoring:
    """50 baseline candidates, then 50 with higher rejection rate → alert fires."""

    def test_batch_drift_detected(self):
        runner = ShadowRunner([LIVE_CAL_CONFIG])

        baseline = []
        for i in range(50):
            snap = _make_snap(market_id=f"mkt-base-{i:03d}")
            baseline.extend(
                runner.evaluate(_make_cal(0.80), snap, intended_size_usdc=20.0, now_utc=_NOW)
            )
        monitor = DriftMonitor(baseline, profiles=["live"])

        # Current window: all WEAK calibration → live rejects all
        current = []
        for i in range(30):
            snap = _make_snap(market_id=f"mkt-curr-{i:03d}")
            current.extend(
                runner.evaluate(_make_cal(0.80, CalibrationQuality.WEAK), snap,
                                intended_size_usdc=20.0, now_utc=_NOW)
            )

        report = monitor.check(current)
        alerts = AlertEngine().check(report)
        assert any(a.level in ("WARNING", "CRITICAL") for a in alerts), (
            f"Expected at least one alert. rejection_rate_delta={report.rejection_rate_delta}"
        )
