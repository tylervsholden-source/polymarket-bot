"""
tests/test_shadow_runner_flow.py

Phase 13: Shadow Runner end-to-end flow tests.

Covers:
- ShadowRunner executes all profiles for a single candidate
- Returns one record per profile, in order
- Records carry correct policy_profile field
- Multi-profile run: live and paper_strict can differ on same input
- evaluate_batch returns flat list (candidates × profiles)
- ShadowRunner never raises for REJECT decisions
- run_id is consistent across records from same runner
- intended_size_usdc is stored in record
- now_utc is shared across profiles for a single candidate
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
    TradeDecisionType,
)
from shadow_runner.runner import ShadowRunner
from shadow_runner.types import ShadowDecisionRecord

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


def _snap_healthy():
    return MarketPricingSnapshot(
        market_id="mkt-001",
        ask_yes=0.44, bid_yes=0.42,
        ask_no=0.57, bid_no=0.55,
        liquidity=5000.0, timestamp_utc=_NOW,
    )


def _cal_strong(confidence=0.80):
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
    return cal


# ── Basic flow ────────────────────────────────────────────────────────────────

class TestShadowRunnerBasicFlow:

    def test_three_profiles_three_records(self):
        """One candidate → three records (one per profile)."""
        runner = ShadowRunner([LIVE_CAL_CONFIG, PAPER_STRICT_CAL_CONFIG, PAPER_LOOSE_CAL_CONFIG])
        records = runner.evaluate(_cal_strong(), _snap_healthy(),
                                  intended_size_usdc=20.0, now_utc=_NOW)
        assert len(records) == 3

    def test_records_carry_correct_profile(self):
        runner = ShadowRunner([LIVE_CAL_CONFIG, PAPER_STRICT_CAL_CONFIG, PAPER_LOOSE_CAL_CONFIG])
        records = runner.evaluate(_cal_strong(), _snap_healthy(),
                                  intended_size_usdc=20.0, now_utc=_NOW)
        profiles = [r.policy_profile for r in records]
        assert profiles == ["live", "paper_strict", "paper_loose"]

    def test_record_order_matches_config_order(self):
        """Records are returned in the same order as configs passed to ShadowRunner."""
        runner = ShadowRunner([PAPER_LOOSE_CAL_CONFIG, LIVE_CAL_CONFIG])
        records = runner.evaluate(_cal_strong(), _snap_healthy(),
                                  intended_size_usdc=20.0, now_utc=_NOW)
        assert records[0].policy_profile == "paper_loose"
        assert records[1].policy_profile == "live"

    def test_run_id_consistent_across_records(self):
        runner = ShadowRunner([LIVE_CAL_CONFIG, PAPER_STRICT_CAL_CONFIG])
        records = runner.evaluate(_cal_strong(), _snap_healthy(),
                                  intended_size_usdc=20.0, now_utc=_NOW)
        assert records[0].run_id == records[1].run_id
        assert records[0].run_id == runner.run_id

    def test_intended_size_stored_in_record(self):
        runner = ShadowRunner([LIVE_CAL_CONFIG])
        records = runner.evaluate(_cal_strong(), _snap_healthy(),
                                  intended_size_usdc=75.0, now_utc=_NOW)
        assert records[0].intended_size_usdc == pytest.approx(75.0)

    def test_now_utc_stored_as_ts_recorded(self):
        runner = ShadowRunner([LIVE_CAL_CONFIG])
        records = runner.evaluate(_cal_strong(), _snap_healthy(),
                                  intended_size_usdc=20.0, now_utc=_NOW)
        assert records[0].ts_recorded_utc == _NOW


# ── Decision correctness ──────────────────────────────────────────────────────

class TestShadowRunnerDecisionCorrectness:

    def test_live_execute_on_healthy_signal(self):
        runner = ShadowRunner([LIVE_CAL_CONFIG])
        records = runner.evaluate(_cal_strong(0.80), _snap_healthy(),
                                  intended_size_usdc=20.0, now_utc=_NOW)
        assert records[0].decision_summary.decision == "EXECUTE_YES"
        assert records[0].decision_summary.rejection_reason is None

    def test_reject_carries_rejection_reason(self):
        """Weak cal is rejected by paper_strict — reason stored in record."""
        from calibration.types import CalibrationQuality
        raw = RawSignalOutput(
            asset="BTC", horizon_minutes=5, timestamp_utc=_NOW,
            predicted_class="UP", raw_confidence=0.72,
            class_probabilities={"UP": 0.72, "DOWN": 0.17, "NO_TRADE": 0.11},
        )
        cal, err = map_to_event_probability(
            raw=raw,
            calibrated_up_prob=0.72,
            calibrated_down_prob=0.17,
            calibrated_no_trade_prob=0.11,
            polarity="NORMAL",
            calibration_method=CalibrationMethod.IDENTITY,
            calibration_quality=CalibrationQuality.WEAK,
        )
        assert err is None
        runner = ShadowRunner([PAPER_STRICT_CAL_CONFIG])
        records = runner.evaluate(cal, _snap_healthy(),
                                  intended_size_usdc=20.0, now_utc=_NOW)
        assert records[0].decision_summary.decision == "REJECT"
        assert records[0].decision_summary.rejection_reason == "WEAK_CALIBRATION"

    def test_profile_divergence_on_borderline_signal(self):
        """paper_loose allows weak cal; paper_strict rejects it."""
        from calibration.types import CalibrationQuality
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
            calibration_method=CalibrationMethod.IDENTITY,
            calibration_quality=CalibrationQuality.WEAK,
        )
        assert err is None
        runner = ShadowRunner([PAPER_STRICT_CAL_CONFIG, PAPER_LOOSE_CAL_CONFIG])
        records = runner.evaluate(cal, _snap_healthy(), now_utc=_NOW)
        strict_r, loose_r = records
        assert strict_r.decision_summary.decision == "REJECT"
        assert loose_r.decision_summary.rejection_reason != "WEAK_CALIBRATION"


# ── Batch evaluation ──────────────────────────────────────────────────────────

class TestBatchEvaluation:

    def test_batch_returns_candidates_times_profiles(self):
        """3 candidates × 2 profiles = 6 records."""
        runner = ShadowRunner([LIVE_CAL_CONFIG, PAPER_STRICT_CAL_CONFIG])
        candidates = [
            (_cal_strong(0.75), _snap_healthy()),
            (_cal_strong(0.80), _snap_healthy()),
            (_cal_strong(0.85), _snap_healthy()),
        ]
        records = runner.evaluate_batch(candidates, intended_size_usdc=20.0, now_utc=_NOW)
        assert len(records) == 6

    def test_batch_profiles_interleaved_correctly(self):
        """Records are: [cand0_live, cand0_strict, cand1_live, cand1_strict, ...]"""
        runner = ShadowRunner([LIVE_CAL_CONFIG, PAPER_STRICT_CAL_CONFIG])
        candidates = [
            (_cal_strong(0.75), _snap_healthy()),
            (_cal_strong(0.80), _snap_healthy()),
        ]
        records = runner.evaluate_batch(candidates, intended_size_usdc=20.0, now_utc=_NOW)
        assert records[0].policy_profile == "live"
        assert records[1].policy_profile == "paper_strict"
        assert records[2].policy_profile == "live"
        assert records[3].policy_profile == "paper_strict"


# ── Signal and pricing extraction ────────────────────────────────────────────

class TestSnapshotExtraction:

    def test_signal_fields_preserved(self):
        cal = _cal_strong(0.72)
        runner = ShadowRunner([PAPER_LOOSE_CAL_CONFIG])
        records = runner.evaluate(cal, _snap_healthy(), now_utc=_NOW)
        sig = records[0].signal
        assert sig.asset == "BTC"
        assert sig.horizon_minutes == 5
        assert sig.calibration_quality == "strong"
        assert sig.bridge_intent_side == "YES"
        assert sig.effective_yes_prob == pytest.approx(0.72)

    def test_pricing_fields_preserved(self):
        snap = _snap_healthy()
        runner = ShadowRunner([PAPER_LOOSE_CAL_CONFIG])
        records = runner.evaluate(_cal_strong(), snap, now_utc=_NOW)
        pri = records[0].pricing
        assert pri.market_id == "mkt-001"
        assert pri.ask_yes == pytest.approx(0.44)
        assert pri.liquidity == pytest.approx(5000.0)
        assert pri.snapshot_age_seconds == pytest.approx(0.0)  # NOW == pricing ts

    def test_ev_fields_in_decision_summary(self):
        runner = ShadowRunner([LIVE_CAL_CONFIG])
        records = runner.evaluate(_cal_strong(0.80), _snap_healthy(),
                                  intended_size_usdc=20.0, now_utc=_NOW)
        summary = records[0].decision_summary
        if summary.decision == "EXECUTE_YES":
            assert summary.execution_adjusted_ev is not None
            assert summary.gross_ev is not None
            assert summary.required_edge_threshold == pytest.approx(0.030)


# ── Constructor validation ────────────────────────────────────────────────────

class TestConstructorValidation:

    def test_empty_configs_raises(self):
        with pytest.raises(ValueError, match="at least one"):
            ShadowRunner([])

    def test_single_config_works(self):
        runner = ShadowRunner([PAPER_LOOSE_CAL_CONFIG])
        records = runner.evaluate(_cal_strong(), _snap_healthy(), now_utc=_NOW)
        assert len(records) == 1
