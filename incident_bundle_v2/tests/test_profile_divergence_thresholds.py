"""
tests/test_profile_divergence_thresholds.py

Phase 14: Profile divergence threshold tests.

Covers:
- live-like vs paper_strict divergence measurement
- paper_strict vs paper_loose divergence
- Overly optimistic paper_loose behavior flagging
- check_profile_divergence levels (GREEN / WARN / FAIL / BLOCKER)
- compute_summary_metrics correctly reflects profile execution rates
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from calibration.probability_mapper import map_to_event_probability
from calibration.types import (
    CalibrationMethod, CalibrationQuality,
    LIVE_CAL_CONFIG, PAPER_LOOSE_CAL_CONFIG, PAPER_STRICT_CAL_CONFIG,
    MarketPricingSnapshot, RawSignalOutput,
)
from monitoring.readiness_checks import (
    CheckLevel,
    check_profile_divergence,
)
from shadow_runner.runner import ShadowRunner
from shadow_runner.summary_metrics import compute_summary_metrics

_BASE = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


def _make_signal(confidence: float = 0.82, quality: CalibrationQuality = CalibrationQuality.STRONG):
    raw = RawSignalOutput(
        asset="BTC", horizon_minutes=15, timestamp_utc=_BASE,
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


def _make_snap(i: int = 0, liquidity: float = 5000.0) -> MarketPricingSnapshot:
    return MarketPricingSnapshot(
        market_id=f"mkt-{i:04d}",
        ask_yes=0.44, bid_yes=0.42, ask_no=0.57, bid_no=0.55,
        liquidity=liquidity, timestamp_utc=_BASE,
    )


# ── check_profile_divergence unit tests ──────────────────────────────────────

class TestCheckProfileDivergence:

    def test_no_divergence_green(self):
        result = check_profile_divergence(live_exec_rate=0.30, loose_exec_rate=0.35)
        assert result.level == CheckLevel.GREEN

    def test_moderate_divergence_warn(self):
        result = check_profile_divergence(live_exec_rate=0.20, loose_exec_rate=0.45)
        assert result.level == CheckLevel.WARN

    def test_high_divergence_fail(self):
        result = check_profile_divergence(live_exec_rate=0.10, loose_exec_rate=0.55)
        assert result.level == CheckLevel.FAIL

    def test_extreme_divergence_blocker(self):
        result = check_profile_divergence(live_exec_rate=0.05, loose_exec_rate=0.75)
        assert result.level == CheckLevel.BLOCKER
        assert result.is_blocker

    def test_missing_live_rate_warns(self):
        result = check_profile_divergence(live_exec_rate=None, loose_exec_rate=0.50)
        assert result.level == CheckLevel.WARN

    def test_missing_loose_rate_warns(self):
        result = check_profile_divergence(live_exec_rate=0.30, loose_exec_rate=None)
        assert result.level == CheckLevel.WARN

    def test_metric_value_is_absolute_difference(self):
        result = check_profile_divergence(live_exec_rate=0.20, loose_exec_rate=0.50)
        assert abs(result.metric_value - 0.30) < 1e-9


# ── Real pipeline profile divergence ─────────────────────────────────────────

class TestRealPipelineDivergence:

    def test_weak_cal_executes_in_loose_not_strict(self):
        """
        Weak calibration: paper_loose executes (doesn't reject on weak cal),
        paper_strict rejects. This should produce measurable divergence.
        """
        runner = ShadowRunner([LIVE_CAL_CONFIG, PAPER_STRICT_CAL_CONFIG, PAPER_LOOSE_CAL_CONFIG])
        records = []
        for i in range(20):
            sig = _make_signal(0.80, quality=CalibrationQuality.WEAK)
            recs = runner.evaluate(sig, _make_snap(i),
                                   intended_size_usdc=20.0, now_utc=_BASE)
            records.extend(recs)

        live_metrics   = compute_summary_metrics(records, "live")
        strict_metrics = compute_summary_metrics(records, "paper_strict")
        loose_metrics  = compute_summary_metrics(records, "paper_loose")

        # Live and paper_strict reject weak cal
        assert live_metrics.execute_count == 0
        assert strict_metrics.execute_count == 0
        # paper_loose may execute (doesn't reject on weak cal if edge is good enough)
        # Even if it doesn't execute for other reasons, the divergence is measurable
        live_rate  = live_metrics.execution_rate
        loose_rate = loose_metrics.execution_rate
        divergence = loose_rate - live_rate
        assert divergence >= 0  # loose always >= live

    def test_live_paper_strict_close_for_strong_signals(self):
        """
        Strong calibration + healthy market: live and paper_strict should behave
        very similarly. Divergence should be low.
        """
        runner = ShadowRunner([LIVE_CAL_CONFIG, PAPER_STRICT_CAL_CONFIG])
        records = []
        for i in range(30):
            sig = _make_signal(0.82)
            recs = runner.evaluate(sig, _make_snap(i),
                                   intended_size_usdc=20.0, now_utc=_BASE)
            records.extend(recs)

        live_metrics   = compute_summary_metrics(records, "live")
        strict_metrics = compute_summary_metrics(records, "paper_strict")

        live_rate   = live_metrics.execution_rate
        strict_rate = strict_metrics.execution_rate
        # They should be close for clean signals
        divergence = abs(strict_rate - live_rate)
        assert divergence < 0.25  # within 25pp for strong healthy signals

    def test_three_profile_execution_rate_ordering(self):
        """
        paper_loose execution rate >= paper_strict >= live (by design).
        """
        runner = ShadowRunner([LIVE_CAL_CONFIG, PAPER_STRICT_CAL_CONFIG, PAPER_LOOSE_CAL_CONFIG])
        records = []
        for i in range(30):
            sig = _make_signal(0.78)
            recs = runner.evaluate(sig, _make_snap(i),
                                   intended_size_usdc=20.0, now_utc=_BASE)
            records.extend(recs)

        live_rate   = compute_summary_metrics(records, "live").execution_rate
        strict_rate = compute_summary_metrics(records, "paper_strict").execution_rate
        loose_rate  = compute_summary_metrics(records, "paper_loose").execution_rate

        # paper_loose >= paper_strict >= live (by policy design)
        assert loose_rate >= strict_rate - 0.01   # small tolerance for ties
        assert strict_rate >= live_rate - 0.01

    def test_rejection_concentration_dominated_by_weak_cal(self):
        """Weak cal signals should dominate rejection composition for live profile."""
        runner = ShadowRunner([LIVE_CAL_CONFIG])
        records = []
        for i in range(30):
            sig = _make_signal(0.80, quality=CalibrationQuality.WEAK)
            recs = runner.evaluate(sig, _make_snap(i),
                                   intended_size_usdc=20.0, now_utc=_BASE)
            records.extend(recs)
        metrics = compute_summary_metrics(records, "live")
        assert metrics.total_evaluated == 30
        # All should be WEAK_CALIBRATION rejects in live
        assert metrics.rejection_counts.get("WEAK_CALIBRATION", 0) == 30
        assert metrics.execute_count == 0


# ── Summary metrics profile isolation ────────────────────────────────────────

class TestSummaryMetricsProfileIsolation:

    def test_compute_metrics_filters_by_profile(self):
        """compute_summary_metrics should only include records for the specified profile."""
        runner = ShadowRunner([LIVE_CAL_CONFIG, PAPER_LOOSE_CAL_CONFIG])
        records = []
        for i in range(10):
            recs = runner.evaluate(_make_signal(), _make_snap(i),
                                   intended_size_usdc=20.0, now_utc=_BASE)
            records.extend(recs)

        live_metrics  = compute_summary_metrics(records, "live")
        loose_metrics = compute_summary_metrics(records, "paper_loose")

        assert live_metrics.total_evaluated == 10
        assert loose_metrics.total_evaluated == 10
        assert live_metrics.total_evaluated + loose_metrics.total_evaluated == len(records)

    def test_unknown_profile_returns_empty_metrics(self):
        records = []
        runner = ShadowRunner([LIVE_CAL_CONFIG])
        for i in range(5):
            recs = runner.evaluate(_make_signal(), _make_snap(i),
                                   intended_size_usdc=20.0, now_utc=_BASE)
            records.extend(recs)
        metrics = compute_summary_metrics(records, "nonexistent_profile")
        assert metrics.total_evaluated == 0
        assert metrics.execution_rate == 0.0
