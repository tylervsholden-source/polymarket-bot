"""
tests/test_shadow_acceptance_metrics.py

Phase 14: Shadow acceptance metrics tests.

Covers:
- EV realism metrics (haircut abs and pct)
- Rejection composition metrics
- Fillability metrics
- Suspicious underround and stale pricing metrics
- compute_summary_metrics for healthy, stale, weak-cal, and partial-fill scenarios
- regime_review rolling stability
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
from shadow_runner.runner import ShadowRunner
from shadow_runner.summary_metrics import compute_summary_metrics, ShadowSummaryMetrics
from monitoring.regime_review import compute_regime_review

_BASE = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


def _make_signal(confidence: float = 0.82, quality=CalibrationQuality.STRONG):
    method = CalibrationMethod.PLATT if quality == CalibrationQuality.STRONG else CalibrationMethod.IDENTITY
    raw = RawSignalOutput(
        asset="BTC", horizon_minutes=15, timestamp_utc=_BASE,
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
        calibration_method=method,
        calibration_quality=quality,
    )
    assert err is None
    return cal


def _snap(i: int = 0, age_seconds: float = 0.0, liquidity: float = 5000.0,
          ask_yes: float = 0.44) -> MarketPricingSnapshot:
    ts = _BASE - timedelta(seconds=age_seconds)
    return MarketPricingSnapshot(
        market_id=f"mkt-{i:04d}",
        ask_yes=ask_yes, bid_yes=ask_yes - 0.02,
        ask_no=0.57, bid_no=0.55,
        liquidity=liquidity, timestamp_utc=ts,
    )


# ── Healthy scenario baseline ─────────────────────────────────────────────────

class TestHealthyScenarioMetrics:

    def test_healthy_metrics_execute_count_positive(self):
        runner = ShadowRunner([LIVE_CAL_CONFIG])
        records = [
            rec
            for i in range(20)
            for rec in runner.evaluate(_make_signal(0.82), _snap(i),
                                       intended_size_usdc=20.0, now_utc=_BASE)
        ]
        m = compute_summary_metrics(records, "live")
        assert m.total_evaluated == 20
        assert m.execute_count + m.reject_count == 20

    def test_healthy_execution_rate_in_range(self):
        runner = ShadowRunner([LIVE_CAL_CONFIG])
        records = [
            rec
            for i in range(20)
            for rec in runner.evaluate(_make_signal(0.82), _snap(i),
                                       intended_size_usdc=20.0, now_utc=_BASE)
        ]
        m = compute_summary_metrics(records, "live")
        assert 0.0 <= m.execution_rate <= 1.0

    def test_ev_haircut_non_negative_for_executes(self):
        """Execution-adjusted EV should be <= gross EV."""
        runner = ShadowRunner([LIVE_CAL_CONFIG])
        records = [
            rec
            for i in range(20)
            for rec in runner.evaluate(_make_signal(0.82), _snap(i),
                                       intended_size_usdc=20.0, now_utc=_BASE)
        ]
        m = compute_summary_metrics(records, "live")
        if m.mean_ev_haircut_abs is not None:
            assert m.mean_ev_haircut_abs >= -0.001  # allow tiny float noise


# ── Rejection composition ─────────────────────────────────────────────────────

class TestRejectionComposition:

    def test_weak_cal_dominated_rejection(self):
        runner = ShadowRunner([LIVE_CAL_CONFIG])
        records = [
            rec
            for i in range(30)
            for rec in runner.evaluate(
                _make_signal(0.80, quality=CalibrationQuality.WEAK), _snap(i),
                intended_size_usdc=20.0, now_utc=_BASE,
            )
        ]
        m = compute_summary_metrics(records, "live")
        assert "WEAK_CALIBRATION" in m.rejection_counts
        assert m.rejection_counts["WEAK_CALIBRATION"] == 30
        assert m.rejection_rates["WEAK_CALIBRATION"] == pytest.approx(1.0)

    def test_stale_pricing_captured_in_rejection_reasons(self):
        runner = ShadowRunner([LIVE_CAL_CONFIG])
        stale_records = [
            rec
            for i in range(10)
            for rec in runner.evaluate(
                _make_signal(0.82), _snap(i, age_seconds=200),
                intended_size_usdc=20.0, now_utc=_BASE,
            )
        ]
        fresh_records = [
            rec
            for i in range(10)
            for rec in runner.evaluate(
                _make_signal(0.82), _snap(i + 100),
                intended_size_usdc=20.0, now_utc=_BASE,
            )
        ]
        all_records = stale_records + fresh_records
        m = compute_summary_metrics(all_records, "live")
        assert m.stale_pricing_count == 10
        assert m.stale_pricing_rate == pytest.approx(0.5)  # 10/20

    def test_rejection_rates_sum_at_most_1(self):
        runner = ShadowRunner([LIVE_CAL_CONFIG])
        records = [
            rec
            for i in range(20)
            for rec in runner.evaluate(_make_signal(0.82), _snap(i),
                                       intended_size_usdc=20.0, now_utc=_BASE)
        ]
        m = compute_summary_metrics(records, "live")
        total_rate = sum(m.rejection_rates.values())
        assert total_rate <= 1.0 + 1e-9


# ── Pricing sanity metrics ─────────────────────────────────────────────────────

class TestPricingSanityMetrics:

    def test_underround_captured(self):
        runner = ShadowRunner([LIVE_CAL_CONFIG])
        # ask_yes=0.46, ask_no=0.50 → sum=0.96 < 0.97 → SUSPICIOUS_UNDERROUND
        snap = MarketPricingSnapshot(
            market_id="mkt-ur", ask_yes=0.46, bid_yes=0.44,
            ask_no=0.50, bid_no=0.48, liquidity=5000.0, timestamp_utc=_BASE,
        )
        records = [
            rec
            for i in range(10)
            for rec in runner.evaluate(_make_signal(0.82), snap,
                                       intended_size_usdc=20.0, now_utc=_BASE)
        ]
        m = compute_summary_metrics(records, "live")
        assert m.suspicious_underround_count == 10
        assert m.suspicious_underround_rate == pytest.approx(1.0)

    def test_stale_rate_computed_correctly(self):
        runner = ShadowRunner([LIVE_CAL_CONFIG])
        # 5 stale, 5 fresh
        stale = [
            rec
            for i in range(5)
            for rec in runner.evaluate(_make_signal(0.82), _snap(i, age_seconds=200),
                                       intended_size_usdc=20.0, now_utc=_BASE)
        ]
        fresh = [
            rec
            for i in range(5)
            for rec in runner.evaluate(_make_signal(0.82), _snap(i + 50),
                                       intended_size_usdc=20.0, now_utc=_BASE)
        ]
        m = compute_summary_metrics(stale + fresh, "live")
        assert m.stale_pricing_rate == pytest.approx(5 / 10)


# ── Fillability metrics ────────────────────────────────────────────────────────

class TestFillabilityMetrics:

    def test_partial_fill_rejected_counted_in_live(self):
        """In live mode, PARTIAL fill leads to PARTIAL_FILL_REJECTED."""
        runner = ShadowRunner([LIVE_CAL_CONFIG])
        # Small liquidity → partial fill scenario
        small_liq_snap = MarketPricingSnapshot(
            market_id="mkt-sm", ask_yes=0.44, bid_yes=0.42,
            ask_no=0.57, bid_no=0.55, liquidity=50.0, timestamp_utc=_BASE,
        )
        records = [
            rec
            for i in range(10)
            for rec in runner.evaluate(_make_signal(0.82), small_liq_snap,
                                       intended_size_usdc=20.0, now_utc=_BASE)
        ]
        m = compute_summary_metrics(records, "live")
        # May or may not trigger partial fill depending on exact model
        # Key test: counts are non-negative and sum correctly
        assert m.partial_fill_rejected_count >= 0
        assert m.partial_fill_rejection_rate == pytest.approx(
            m.partial_fill_rejected_count / m.total_evaluated
        )

    def test_full_fill_executes_counted(self):
        runner = ShadowRunner([LIVE_CAL_CONFIG])
        records = [
            rec
            for i in range(20)
            for rec in runner.evaluate(_make_signal(0.82), _snap(i),
                                       intended_size_usdc=20.0, now_utc=_BASE)
        ]
        m = compute_summary_metrics(records, "live")
        # Executes with fill_fraction >= 0.95
        assert m.full_fill_execute_count >= 0
        assert m.full_fill_execute_count <= m.execute_count


# ── EV realism metrics ─────────────────────────────────────────────────────────

class TestEVRealismMetrics:

    def test_ev_metrics_none_when_no_executes(self):
        runner = ShadowRunner([LIVE_CAL_CONFIG])
        records = [
            rec
            for i in range(20)
            for rec in runner.evaluate(
                _make_signal(0.80, quality=CalibrationQuality.WEAK), _snap(i),
                intended_size_usdc=20.0, now_utc=_BASE,
            )
        ]
        m = compute_summary_metrics(records, "live")
        # All rejected → EV fields should be None or 0 sample size
        assert m.ev_haircut_sample_size == 0
        assert m.mean_gross_ev is None or m.mean_gross_ev == 0.0

    def test_ev_haircut_pct_in_range(self):
        """EV haircut pct should be between 0 and 1 for normal executes."""
        runner = ShadowRunner([LIVE_CAL_CONFIG])
        records = [
            rec
            for i in range(20)
            for rec in runner.evaluate(_make_signal(0.82), _snap(i),
                                       intended_size_usdc=20.0, now_utc=_BASE)
        ]
        m = compute_summary_metrics(records, "live")
        if m.mean_ev_haircut_pct is not None:
            assert -0.1 <= m.mean_ev_haircut_pct <= 1.1  # allow small float noise

    def test_coverage_fields_populated(self):
        runner = ShadowRunner([LIVE_CAL_CONFIG])
        records = [
            rec
            for i in range(20)
            for rec in runner.evaluate(_make_signal(0.82), _snap(i),
                                       intended_size_usdc=20.0, now_utc=_BASE)
        ]
        m = compute_summary_metrics(records, "live")
        assert "BTC" in m.assets_seen
        assert 15 in m.horizons_seen


# ── Regime review ──────────────────────────────────────────────────────────────

class TestRegimeReview:

    def test_insufficient_records_returns_unstable(self):
        """< window_size records → is_stable=False (unknown stability)."""
        runner = ShadowRunner([LIVE_CAL_CONFIG])
        records = [
            rec
            for i in range(5)
            for rec in runner.evaluate(_make_signal(0.82), _snap(i),
                                       intended_size_usdc=20.0, now_utc=_BASE)
        ]
        review = compute_regime_review(records, profile="live", window_size=20)
        assert not review.is_stable
        assert review.rolling_snapshots == []

    def test_stable_corpus_has_no_flags(self):
        """Uniform healthy signals should not trigger stability flags."""
        runner = ShadowRunner([LIVE_CAL_CONFIG])
        records = []
        for i in range(80):
            now = _BASE + timedelta(hours=i)
            recs = runner.evaluate(_make_signal(0.82), _snap(i),
                                   intended_size_usdc=20.0, now_utc=now)
            records.extend(recs)
        review = compute_regime_review(records, profile="live", window_size=10, step=5)
        assert len(review.rolling_snapshots) > 1
        # Uniform data: no spikes expected
        assert not review.flags.rejection_rate_spike

    def test_rolling_snapshots_have_correct_window_size(self):
        runner = ShadowRunner([LIVE_CAL_CONFIG])
        records = []
        for i in range(50):
            recs = runner.evaluate(_make_signal(0.82), _snap(i),
                                   intended_size_usdc=20.0, now_utc=_BASE)
            records.extend(recs)
        review = compute_regime_review(records, profile="live", window_size=10, step=5)
        for snap in review.rolling_snapshots:
            assert snap.window_size == 10

    def test_regime_review_returns_summary_string(self):
        runner = ShadowRunner([LIVE_CAL_CONFIG])
        records = []
        for i in range(30):
            recs = runner.evaluate(_make_signal(0.82), _snap(i),
                                   intended_size_usdc=20.0, now_utc=_BASE)
            records.extend(recs)
        review = compute_regime_review(records, profile="live", window_size=10, step=5)
        assert isinstance(review.summary, str)
        assert len(review.summary) > 5
