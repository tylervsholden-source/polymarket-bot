"""
tests/test_rejection_analytics.py

Phase 13: Rejection pattern analytics across profiles and reporting.

Covers:
- generate_comparison_report produces correct per-profile summaries
- execution_rate correctly computed per profile
- loose-only execute count identified correctly
- strict-not-live count identified correctly
- format_report returns non-empty string
- CrossProfileComparison agreement_rate + divergence_rate sum to 1.0
- single-profile report has no cross-profile comparisons
- ProfileSummary rejection_reasons populated
- observation zone warning present in formatted report when expected
- All-reject window produces execution_rate=0.0
- All-execute window produces rejection_reasons empty dict
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
from shadow_runner.reporting import format_report, generate_comparison_report
from shadow_runner.runner import ShadowRunner
from shadow_runner.types import (
    DecisionSummary,
    PricingSnapshot,
    ShadowDecisionRecord,
    SignalSnapshot,
)

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


def _make_synthetic_record(
    profile: str,
    decision: str,
    rejection_reason: str | None = None,
    market_id: str = "mkt-000",
    asset: str = "BTC",
    exec_adj_ev: float | None = None,
    sanity_notes: str | None = None,
    run_id: str = "run-test",
) -> ShadowDecisionRecord:
    sig = SignalSnapshot(
        asset=asset,
        horizon_minutes=5,
        signal_timestamp_utc=_NOW,
        predicted_class="UP",
        raw_confidence=0.80,
        class_probabilities=None,
        calibration_method="platt",
        calibration_quality="strong",
        effective_yes_prob=0.80,
        effective_no_prob=0.20,
        mapping_context="UP→YES (NORMAL)",
        bridge_intent_side="YES",
    )
    pri = PricingSnapshot(
        market_id=market_id,
        ask_yes=0.44, bid_yes=0.42,
        ask_no=0.57, bid_no=0.55,
        liquidity=5000.0,
        pricing_timestamp_utc=_NOW,
        snapshot_age_seconds=0.0,
    )
    dec = DecisionSummary(
        decision=decision,
        rejection_reason=rejection_reason,
        policy_mode=profile,
        passes_final_gate=(decision != "REJECT"),
        intended_size_usdc_used=20.0,
        pricing_sanity_notes=sanity_notes,
        execution_adjusted_ev=exec_adj_ev,
        required_edge_threshold=0.025,
    )
    return ShadowDecisionRecord(
        record_id=f"rec-{profile}-{market_id}",
        run_id=run_id,
        ts_recorded_utc=_NOW,
        signal=sig,
        pricing=pri,
        policy_profile=profile,
        intended_size_usdc=20.0,
        decision_summary=dec,
    )


# ── Per-profile summary ───────────────────────────────────────────────────────

class TestPerProfileSummary:

    def test_execution_rate_all_execute(self):
        records = [
            _make_synthetic_record("paper_loose", "EXECUTE_YES", market_id=f"m{i}")
            for i in range(10)
        ]
        report = generate_comparison_report(records)
        ps = report.per_profile["paper_loose"]
        assert ps.execution_rate == pytest.approx(1.0)
        assert ps.execute_count == 10

    def test_execution_rate_all_reject(self):
        records = [
            _make_synthetic_record("live", "REJECT", "NEGATIVE_EDGE", market_id=f"m{i}")
            for i in range(5)
        ]
        report = generate_comparison_report(records)
        ps = report.per_profile["live"]
        assert ps.execution_rate == pytest.approx(0.0)
        assert ps.reject_count == 5

    def test_rejection_reasons_counted(self):
        records = [
            _make_synthetic_record("paper_strict", "REJECT", "NEGATIVE_EDGE"),
            _make_synthetic_record("paper_strict", "REJECT", "NEGATIVE_EDGE", market_id="m2"),
            _make_synthetic_record("paper_strict", "REJECT", "STALE_PRICING", market_id="m3"),
        ]
        report = generate_comparison_report(records)
        ps = report.per_profile["paper_strict"]
        assert ps.rejection_reasons["NEGATIVE_EDGE"] == 2
        assert ps.rejection_reasons["STALE_PRICING"] == 1

    def test_ev_stats_computed_for_executes(self):
        records = [
            _make_synthetic_record("paper_loose", "EXECUTE_YES", exec_adj_ev=0.030 + i * 0.001, market_id=f"m{i}")
            for i in range(10)
        ]
        report = generate_comparison_report(records)
        ps = report.per_profile["paper_loose"]
        assert ps.ev_mean is not None
        assert 0.030 <= ps.ev_mean <= 0.040

    def test_sanity_note_count(self):
        records = [
            _make_synthetic_record("paper_loose", "EXECUTE_YES", sanity_notes="SUSPICIOUS_UNDERROUND: ..."),
            _make_synthetic_record("paper_loose", "EXECUTE_YES", market_id="m2"),
        ]
        report = generate_comparison_report(records)
        ps = report.per_profile["paper_loose"]
        assert ps.sanity_note_count == 1


# ── Cross-profile divergence ──────────────────────────────────────────────────

class TestCrossProfileDivergence:

    def test_agreement_plus_divergence_equals_1(self):
        records = [
            _make_synthetic_record("live",  "EXECUTE_YES", market_id="m1"),
            _make_synthetic_record("paper_strict", "EXECUTE_YES", market_id="m1"),
            _make_synthetic_record("live",  "REJECT", "NEGATIVE_EDGE", market_id="m2"),
            _make_synthetic_record("paper_strict", "EXECUTE_YES", market_id="m2"),
        ]
        report = generate_comparison_report(records)
        for cmp in report.cross_profile:
            assert cmp.agreement_rate + cmp.divergence_rate == pytest.approx(1.0)

    def test_full_agreement_divergence_zero(self):
        records = [
            _make_synthetic_record("live", "EXECUTE_YES", market_id="m1"),
            _make_synthetic_record("paper_strict", "EXECUTE_YES", market_id="m1"),
            _make_synthetic_record("live", "REJECT", "NEGATIVE_EDGE", market_id="m2"),
            _make_synthetic_record("paper_strict", "REJECT", "NEGATIVE_EDGE", market_id="m2"),
        ]
        report = generate_comparison_report(records)
        # live vs paper_strict comparison
        cmp = next((c for c in report.cross_profile
                   if {c.profile_a, c.profile_b} == {"live", "paper_strict"}), None)
        assert cmp is not None
        assert cmp.divergence_rate == pytest.approx(0.0)
        assert cmp.agreement_rate == pytest.approx(1.0)

    def test_single_profile_no_cross_comparison(self):
        records = [
            _make_synthetic_record("paper_loose", "EXECUTE_YES", market_id=f"m{i}")
            for i in range(5)
        ]
        report = generate_comparison_report(records)
        assert report.cross_profile == []


# ── Divergence counts ─────────────────────────────────────────────────────────

class TestDivergenceCounts:

    def test_loose_only_execute_detected(self):
        """
        Market m1: loose executes, strict rejects, live rejects.
        → loose_only_execute_count should be 1.
        """
        records = [
            _make_synthetic_record("paper_loose", "EXECUTE_YES", market_id="m1"),
            _make_synthetic_record("paper_strict", "REJECT", "NEGATIVE_EDGE", market_id="m1"),
            _make_synthetic_record("live", "REJECT", "NEGATIVE_EDGE", market_id="m1"),
        ]
        report = generate_comparison_report(records)
        assert report.loose_only_execute_count == 1

    def test_strict_not_live_detected(self):
        """
        Market m2: strict executes, live rejects.
        → strict_not_live_count should be 1.
        """
        records = [
            _make_synthetic_record("paper_strict", "EXECUTE_YES", market_id="m2"),
            _make_synthetic_record("live", "REJECT", "NEGATIVE_EDGE", market_id="m2"),
        ]
        report = generate_comparison_report(records)
        assert report.strict_not_live_count == 1

    def test_both_execute_not_counted_as_divergent(self):
        """
        All three profiles execute → loose_only = 0, strict_not_live = 0.
        """
        records = [
            _make_synthetic_record("paper_loose", "EXECUTE_YES", market_id="m3"),
            _make_synthetic_record("paper_strict", "EXECUTE_YES", market_id="m3"),
            _make_synthetic_record("live", "EXECUTE_YES", market_id="m3"),
        ]
        report = generate_comparison_report(records)
        assert report.loose_only_execute_count == 0
        assert report.strict_not_live_count == 0


# ── Report formatting ─────────────────────────────────────────────────────────

class TestReportFormatting:

    def test_format_report_non_empty(self):
        records = [
            _make_synthetic_record("paper_loose", "EXECUTE_YES", market_id=f"m{i}")
            for i in range(5)
        ]
        report = generate_comparison_report(records)
        text = format_report(report)
        assert len(text) > 50

    def test_format_report_contains_profile_names(self):
        records = [
            _make_synthetic_record("paper_strict", "REJECT", "STALE_PRICING", market_id="m1"),
            _make_synthetic_record("paper_loose", "EXECUTE_YES", market_id="m1"),
        ]
        report = generate_comparison_report(records)
        text = format_report(report)
        assert "paper_strict" in text
        assert "paper_loose" in text

    def test_format_report_warns_loose_only(self):
        """When loose_only_execute > 0, formatted report should mention OBSERVATION ZONE."""
        records = [
            _make_synthetic_record("paper_loose", "EXECUTE_YES", market_id="m1"),
            _make_synthetic_record("paper_strict", "REJECT", "NEGATIVE_EDGE", market_id="m1"),
            _make_synthetic_record("live", "REJECT", "NEGATIVE_EDGE", market_id="m1"),
        ]
        report = generate_comparison_report(records)
        text = format_report(report)
        assert "OBSERVATION ZONE" in text

    def test_empty_records_produces_empty_report(self):
        report = generate_comparison_report([])
        assert report.total_records == 0
        assert report.profiles_found == []
        assert report.cross_profile == []
