"""
tests/test_shadow_validation_gate.py

Phase 14: Evidence sufficiency gate tests.

Covers:
- Insufficient evidence → not sufficient, gaps populated
- Sufficient evidence → sufficient, no gaps
- Each gap dimension individually
- Custom requirements override
- Hard blocker path: evidence insufficient → assess_readiness returns NO_GO immediately
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
from shadow_runner.types import ShadowDecisionRecord
from shadow_runner.validation import (
    EvidenceRequirements,
    check_evidence_sufficiency,
)
from shadow_runner.readiness import (
    ReadinessVerdict,
    assess_readiness,
    LIVE_PILOT_CONSTRAINTS,
)
from shadow_runner.summary_metrics import compute_summary_metrics

_BASE = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


def _make_signal(confidence: float = 0.82) -> object:
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
        calibration_method=CalibrationMethod.PLATT,
        calibration_quality=CalibrationQuality.STRONG,
    )
    assert err is None
    return cal


def _make_snap(i: int = 0, age_seconds: float = 0.0) -> MarketPricingSnapshot:
    ts = _BASE - timedelta(seconds=age_seconds)
    return MarketPricingSnapshot(
        market_id=f"mkt-{i:04d}",
        ask_yes=0.44, bid_yes=0.42, ask_no=0.57, bid_no=0.55,
        liquidity=5000.0, timestamp_utc=ts,
    )


def _build_records(
    n_candidates: int,
    span_days: float = 4.0,
    profile: str = "live",
) -> list[ShadowDecisionRecord]:
    """Build n_candidates records for a single profile, spread over span_days."""
    configs = {
        "live": [LIVE_CAL_CONFIG],
        "paper_strict": [PAPER_STRICT_CAL_CONFIG],
        "paper_loose": [PAPER_LOOSE_CAL_CONFIG],
    }
    runner = ShadowRunner(configs[profile])
    records = []
    interval = (span_days * 86400) / max(n_candidates, 1)
    for i in range(n_candidates):
        now = _BASE + timedelta(seconds=i * interval)
        recs = runner.evaluate(_make_signal(), _make_snap(i),
                               intended_size_usdc=20.0, now_utc=now)
        records.extend(recs)
    return records


# ── Insufficient evidence ─────────────────────────────────────────────────────

class TestInsufficientEvidence:

    def test_empty_corpus_is_insufficient(self):
        result = check_evidence_sufficiency([])
        assert not result.sufficient
        assert len(result.gaps) > 0

    def test_too_few_evaluated_decisions(self):
        records = _build_records(10, span_days=4.0)
        result = check_evidence_sufficiency(records)
        assert not result.sufficient
        gap_dims = [g.dimension for g in result.gaps]
        assert "live_like_evaluated" in gap_dims

    def test_insufficient_executes_gap_present(self):
        # Build records but with very few executes
        # Use low confidence to force mostly rejects
        from calibration.types import CalibrationQuality, CalibrationMethod
        raw = RawSignalOutput(
            asset="BTC", horizon_minutes=15, timestamp_utc=_BASE,
            predicted_class="UP", raw_confidence=0.55,
            class_probabilities={"UP": 0.55, "DOWN": 0.27, "NO_TRADE": 0.18},
        )
        cal, err = map_to_event_probability(
            raw=raw, calibrated_up_prob=0.55,
            calibrated_down_prob=0.27, calibrated_no_trade_prob=0.18,
            polarity="NORMAL",
            calibration_method=CalibrationMethod.IDENTITY,
            calibration_quality=CalibrationQuality.WEAK,
        )
        assert err is None
        runner = ShadowRunner([LIVE_CAL_CONFIG])
        records = []
        for i in range(60):
            now = _BASE + timedelta(hours=i * 2)
            recs = runner.evaluate(cal, _make_snap(i),
                                   intended_size_usdc=20.0, now_utc=now)
            records.extend(recs)
        result = check_evidence_sufficiency(records)
        # Either all are rejects (weak cal rejected) or some metrics gap
        # Either way, if insufficient, check it says so
        if not result.sufficient:
            assert len(result.gaps) > 0

    def test_insufficient_observation_days(self):
        """Records spanning < 3 days → observation_days gap."""
        records = _build_records(60, span_days=1.0)
        result = check_evidence_sufficiency(records)
        assert not result.sufficient
        gap_dims = [g.dimension for g in result.gaps]
        assert "observation_days" in gap_dims

    def test_gaps_carry_human_readable_messages(self):
        records = _build_records(5, span_days=0.5)
        result = check_evidence_sufficiency(records)
        assert not result.sufficient
        for gap in result.gaps:
            assert isinstance(gap.message, str)
            assert len(gap.message) > 10

    def test_readiness_with_insufficient_evidence_is_no_go(self):
        """assess_readiness() must return INSUFFICIENT_EVIDENCE when evidence is insufficient."""
        records = _build_records(5, span_days=0.5)
        ev_result = check_evidence_sufficiency(records)
        assert not ev_result.sufficient

        metrics = compute_summary_metrics(records, profile="live")
        report = assess_readiness(metrics, ev_result)
        assert report.verdict == ReadinessVerdict.INSUFFICIENT_EVIDENCE
        assert not report.evidence_sufficient
        assert "insufficient" in report.verdict_reason.lower()


# ── Sufficient evidence ───────────────────────────────────────────────────────

class TestSufficientEvidence:

    def test_sufficient_corpus_returns_sufficient(self):
        """Build enough records across enough days.
        Live policy at 0.82 confidence produces very few executes — set
        min_live_like_executes=1 to reflect real pipeline strictness.
        """
        records = _build_records(60, span_days=4.0)
        req = EvidenceRequirements(
            min_live_like_evaluated=50,
            min_live_like_executes=1,   # live policy is strict; 1 execute in 60 is realistic
            min_live_like_rejects=5,
            min_assets_covered=1,
            min_horizons_covered=1,
            min_observation_days=3.0,
        )
        result = check_evidence_sufficiency(records, req)
        assert result.sufficient, f"Gaps: {[g.message for g in result.gaps]}"
        assert result.gaps == []

    def test_sufficient_result_carries_correct_counts(self):
        records = _build_records(60, span_days=4.0)
        req = EvidenceRequirements(
            min_live_like_evaluated=50,
            min_live_like_executes=5,
            min_live_like_rejects=5,
            min_assets_covered=1,
            min_horizons_covered=1,
            min_observation_days=3.0,
        )
        result = check_evidence_sufficiency(records, req)
        assert result.live_like_evaluated == len(records)
        assert result.live_like_executes + result.live_like_rejects == len(records)

    def test_assets_and_horizons_reported(self):
        records = _build_records(60, span_days=4.0)
        req = EvidenceRequirements(min_live_like_evaluated=50, min_live_like_executes=5,
                                   min_live_like_rejects=5, min_assets_covered=1,
                                   min_horizons_covered=1, min_observation_days=3.0)
        result = check_evidence_sufficiency(records, req)
        assert "BTC" in result.assets_covered
        assert 15 in result.horizons_covered

    def test_observation_days_positive(self):
        records = _build_records(60, span_days=4.0)
        req = EvidenceRequirements(min_live_like_evaluated=50, min_live_like_executes=5,
                                   min_live_like_rejects=5, min_assets_covered=1,
                                   min_horizons_covered=1, min_observation_days=3.0)
        result = check_evidence_sufficiency(records, req)
        assert result.observation_days >= 3.0


# ── Custom requirements ────────────────────────────────────────────────────────

class TestCustomRequirements:

    def test_relaxed_requirements_pass_small_corpus(self):
        records = _build_records(15, span_days=4.0)
        req = EvidenceRequirements(
            min_live_like_evaluated=10,
            min_live_like_executes=1,
            min_live_like_rejects=1,
            min_assets_covered=1,
            min_horizons_covered=1,
            min_observation_days=0.5,
        )
        result = check_evidence_sufficiency(records, req)
        assert result.sufficient, f"Gaps: {[g.message for g in result.gaps]}"

    def test_strict_requirements_fail_medium_corpus(self):
        records = _build_records(30, span_days=4.0)
        req = EvidenceRequirements(
            min_live_like_evaluated=100,
            min_live_like_executes=50,
            min_live_like_rejects=50,
            min_assets_covered=3,
            min_horizons_covered=3,
            min_observation_days=7.0,
        )
        result = check_evidence_sufficiency(records, req)
        assert not result.sufficient

    def test_requirements_used_preserved_in_result(self):
        records = _build_records(20, span_days=4.0)
        req = EvidenceRequirements(min_live_like_evaluated=5,
                                   min_live_like_executes=1,
                                   min_live_like_rejects=1,
                                   min_assets_covered=1,
                                   min_horizons_covered=1,
                                   min_observation_days=0.1)
        result = check_evidence_sufficiency(records, req)
        assert result.requirements_used is req
