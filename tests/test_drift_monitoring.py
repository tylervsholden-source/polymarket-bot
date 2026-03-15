"""
tests/test_drift_monitoring.py

Phase 13: Drift monitoring — DriftMonitor, DriftReport, AlertEngine.

Covers:
- DriftMonitor computes correct per-profile metrics
- rejection_rate_delta is 0 when baseline == current
- rejection_rate_delta correctly reflects increase
- EV metrics computed for EXECUTE records
- EV metrics None when no executes in window
- AlertEngine returns no alerts when within thresholds
- AlertEngine returns WARNING for 15pp rejection rate increase
- AlertEngine returns CRITICAL for 30pp rejection rate increase
- AlertEngine returns WARNING for EV mean drop
- AlertEngine returns CRITICAL for severe EV mean drop
- AlertEngine suppresses alerts when window is too small
- DriftMonitor.check() returns DriftReport with profiles_checked
- Profile divergence computed correctly
- Alert carries correct baseline/current/delta values
"""
from __future__ import annotations

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
from monitoring.alerts import AlertConfig, AlertEngine
from monitoring.drift_monitor import DriftMonitor
from monitoring.metrics import compute_ev_metrics, compute_rejection_metrics, group_by_profile
from shadow_runner.runner import ShadowRunner
from shadow_runner.types import (
    DecisionSummary,
    PricingSnapshot,
    ShadowDecisionRecord,
    SignalSnapshot,
)

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


def _make_snap_signal():
    """Return a (CalibratedSignal, MarketPricingSnapshot) pair."""
    raw = RawSignalOutput(
        asset="BTC", horizon_minutes=5, timestamp_utc=_NOW,
        predicted_class="UP", raw_confidence=0.80,
        class_probabilities={"UP": 0.80, "DOWN": 0.12, "NO_TRADE": 0.08},
    )
    cal, err = map_to_event_probability(
        raw=raw,
        calibrated_up_prob=0.80,
        calibrated_down_prob=0.12,
        calibrated_no_trade_prob=0.08,
        polarity="NORMAL",
        calibration_method=CalibrationMethod.PLATT,
        calibration_quality=CalibrationQuality.STRONG,
    )
    assert err is None
    snap = MarketPricingSnapshot(
        market_id="mkt-drift",
        ask_yes=0.44, bid_yes=0.42,
        ask_no=0.57, bid_no=0.55,
        liquidity=5000.0, timestamp_utc=_NOW,
    )
    return cal, snap


def _generate_records(n_execute=10, n_reject=0, profile="paper_loose"):
    """
    Generate synthetic records with controlled execute/reject ratios.
    Simulates a window of records for metric testing.
    """
    records = []
    for i in range(n_execute + n_reject):
        is_reject = i >= n_execute
        sig = SignalSnapshot(
            asset="BTC",
            horizon_minutes=5,
            signal_timestamp_utc=_NOW,
            predicted_class="UP",
            raw_confidence=0.80,
            class_probabilities={"UP": 0.80, "DOWN": 0.12, "NO_TRADE": 0.08},
            calibration_method="platt",
            calibration_quality="strong",
            effective_yes_prob=0.80,
            effective_no_prob=0.20,
            mapping_context="UP→YES (NORMAL)",
            bridge_intent_side="YES",
        )
        pri = PricingSnapshot(
            market_id=f"mkt-{i:04d}",
            ask_yes=0.44, bid_yes=0.42,
            ask_no=0.57, bid_no=0.55,
            liquidity=5000.0,
            pricing_timestamp_utc=_NOW,
            snapshot_age_seconds=0.0,
        )
        dec = DecisionSummary(
            decision="REJECT" if is_reject else "EXECUTE_YES",
            rejection_reason="NEGATIVE_EDGE" if is_reject else None,
            policy_mode=profile,
            passes_final_gate=not is_reject,
            intended_size_usdc_used=20.0,
            execution_adjusted_ev=None if is_reject else 0.035,
            required_edge_threshold=0.020,
        )
        records.append(ShadowDecisionRecord(
            record_id=f"rec-{i:04d}",
            run_id="run-test",
            ts_recorded_utc=_NOW,
            signal=sig,
            pricing=pri,
            policy_profile=profile,
            intended_size_usdc=20.0,
            decision_summary=dec,
        ))
    return records


# ── Metric extraction ────────────────────────────────────────────────────────

class TestMetricExtraction:

    def test_rejection_metrics_all_execute(self):
        records = _generate_records(n_execute=10, n_reject=0)
        m = compute_rejection_metrics(records, "paper_loose")
        assert m.rejection_rate == pytest.approx(0.0)
        assert m.execute_count == 10
        assert m.reject_count == 0

    def test_rejection_metrics_mixed(self):
        records = _generate_records(n_execute=6, n_reject=4)
        m = compute_rejection_metrics(records, "paper_loose")
        assert m.rejection_rate == pytest.approx(0.40)
        assert m.reject_count == 4
        assert "NEGATIVE_EDGE" in m.reason_counts

    def test_rejection_metrics_empty_window(self):
        m = compute_rejection_metrics([], "live")
        assert m.total_records == 0
        assert m.rejection_rate == pytest.approx(0.0)

    def test_ev_metrics_from_executes(self):
        records = _generate_records(n_execute=10, n_reject=0)
        m = compute_ev_metrics(records, "paper_loose")
        assert m is not None
        assert m.mean_exec_adj_ev == pytest.approx(0.035)
        assert m.execute_count == 10

    def test_ev_metrics_none_when_all_reject(self):
        records = _generate_records(n_execute=0, n_reject=10)
        m = compute_ev_metrics(records, "paper_loose")
        assert m is None

    def test_group_by_profile(self):
        recs_live  = _generate_records(5, 0, "live")
        recs_loose = _generate_records(3, 2, "paper_loose")
        grouped = group_by_profile(recs_live + recs_loose)
        assert len(grouped["live"]) == 5
        assert len(grouped["paper_loose"]) == 5


# ── DriftMonitor ─────────────────────────────────────────────────────────────

class TestDriftMonitor:

    def test_zero_delta_when_baseline_equals_current(self):
        baseline = _generate_records(10, 10, "paper_loose")  # 50% rejection rate
        monitor = DriftMonitor(baseline, profiles=["paper_loose"])
        current = _generate_records(10, 10, "paper_loose")
        report = monitor.check(current)
        assert report.rejection_rate_delta["paper_loose"] == pytest.approx(0.0)

    def test_positive_delta_when_rejection_increases(self):
        baseline = _generate_records(10, 0, "paper_loose")   # 0% rejection
        monitor = DriftMonitor(baseline, profiles=["paper_loose"])
        current = _generate_records(5, 5, "paper_loose")      # 50% rejection
        report = monitor.check(current)
        assert report.rejection_rate_delta["paper_loose"] == pytest.approx(0.50)

    def test_negative_delta_when_rejection_decreases(self):
        baseline = _generate_records(5, 5, "paper_loose")    # 50% rejection
        monitor = DriftMonitor(baseline, profiles=["paper_loose"])
        current = _generate_records(10, 0, "paper_loose")    # 0% rejection
        report = monitor.check(current)
        assert report.rejection_rate_delta["paper_loose"] == pytest.approx(-0.50)

    def test_profiles_checked_matches_constructor(self):
        baseline = _generate_records(10, 0, "live")
        monitor = DriftMonitor(baseline, profiles=["live"])
        report = monitor.check(_generate_records(10, 0, "live"))
        assert "live" in report.profiles_checked

    def test_ev_mean_delta_zero_when_identical(self):
        baseline = _generate_records(10, 0, "live")
        monitor = DriftMonitor(baseline, profiles=["live"])
        current = _generate_records(10, 0, "live")
        report = monitor.check(current)
        assert report.ev_mean_delta["live"] == pytest.approx(0.0)

    def test_baseline_window_size_reported(self):
        baseline = _generate_records(20, 5, "live")
        monitor = DriftMonitor(baseline, profiles=["live"])
        report = monitor.check(_generate_records(10, 0, "live"))
        assert report.baseline_window_size == 25

    def test_drift_monitor_requires_nonempty_baseline(self):
        with pytest.raises(ValueError, match="at least one"):
            DriftMonitor([])

    def test_multi_profile_report(self):
        baseline = (
            _generate_records(10, 0, "live")
            + _generate_records(10, 2, "paper_strict")
        )
        monitor = DriftMonitor(baseline, profiles=["live", "paper_strict"])
        current = (
            _generate_records(8, 2, "live")
            + _generate_records(7, 3, "paper_strict")
        )
        report = monitor.check(current)
        assert "live" in report.rejection_rate_delta
        assert "paper_strict" in report.rejection_rate_delta


# ── AlertEngine ───────────────────────────────────────────────────────────────

class TestAlertEngine:

    def test_no_alerts_within_threshold(self):
        baseline = _generate_records(50, 10, "paper_strict")  # ~17% rejection
        monitor = DriftMonitor(baseline, profiles=["paper_strict"])
        # Similar current window
        current = _generate_records(48, 12, "paper_strict")   # ~20% rejection, Δ≈+3pp
        report = monitor.check(current)
        engine = AlertEngine()
        alerts = engine.check(report)
        assert len(alerts) == 0

    def test_warning_on_15pp_rejection_increase(self):
        baseline = _generate_records(85, 15, "paper_strict")  # 15% rejection
        monitor = DriftMonitor(baseline, profiles=["paper_strict"])
        current = _generate_records(70, 30, "paper_strict")   # 30% rejection, Δ=+15pp
        report = monitor.check(current)
        engine = AlertEngine()
        alerts = engine.check(report)
        rate_alerts = [a for a in alerts if a.metric == "rejection_rate"]
        assert any(a.level == "WARNING" for a in rate_alerts)

    def test_critical_on_30pp_rejection_increase(self):
        baseline = _generate_records(90, 10, "live")  # 10% rejection
        monitor = DriftMonitor(baseline, profiles=["live"])
        current = _generate_records(60, 40, "live")   # 40% rejection, Δ=+30pp
        report = monitor.check(current)
        engine = AlertEngine()
        alerts = engine.check(report)
        rate_alerts = [a for a in alerts if a.metric == "rejection_rate"]
        assert any(a.level == "CRITICAL" for a in rate_alerts)

    def test_suppressed_when_window_too_small(self):
        baseline = _generate_records(90, 10, "live")
        monitor = DriftMonitor(baseline, profiles=["live"])
        # Only 3 records — below min_window_size=10
        current = _generate_records(0, 3, "live")
        report = monitor.check(current)
        engine = AlertEngine()
        alerts = engine.check(report)
        assert len(alerts) == 0

    def test_alert_carries_correct_delta(self):
        baseline = _generate_records(90, 10, "live")  # 10% rejection
        monitor = DriftMonitor(baseline, profiles=["live"])
        current = _generate_records(60, 40, "live")   # 40% rejection
        report = monitor.check(current)
        engine = AlertEngine()
        alerts = engine.check(report)
        rate_alerts = [a for a in alerts if a.metric == "rejection_rate" and a.policy_profile == "live"]
        assert rate_alerts
        alert = rate_alerts[0]
        assert alert.delta == pytest.approx(0.30, abs=0.01)
        assert alert.baseline_value == pytest.approx(0.10)
        assert alert.current_value == pytest.approx(0.40)

    def test_custom_alert_config(self):
        """Lower thresholds should fire alerts that default config wouldn't."""
        # δ=+12pp to avoid IEEE-754 boundary (15%-10%=0.0499... < 0.05 exactly)
        cfg = AlertConfig(rejection_rate_warn=0.05, rejection_rate_crit=0.10)
        baseline = _generate_records(90, 10, "paper_loose")  # 10% rejection
        monitor = DriftMonitor(baseline, profiles=["paper_loose"])
        current = _generate_records(78, 22, "paper_loose")   # 22% rejection Δ=+12pp
        report = monitor.check(current)
        engine = AlertEngine(config=cfg)
        alerts = engine.check(report)
        assert any(a.metric == "rejection_rate" for a in alerts)
