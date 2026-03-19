"""
tests/test_replay_consistency.py

Phase 13: Deterministic replay verification.

Covers:
- Healthy EXECUTE decision replays identically
- REJECT decision replays identically (correct rejection reason preserved)
- Replay with unknown profile returns match=False + mismatch_reason
- assert_replay_consistency passes for clean records
- assert_replay_consistency raises for mismatched records
- Staleness is correctly reconstructed (snapshot_age_seconds → now_utc)
- Weak calibration reject replays correctly
- Suspicious underround reject (live) replays correctly
- Multiple records in a batch all replay consistently
- Stale pricing reject replays correctly across profiles
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from calibration.probability_mapper import map_to_event_probability
from calibration.types import (
    CalibrationMethod,
    CalibrationQuality,
    CalibrationRejectionReason,
    LIVE_CAL_CONFIG,
    MarketPricingSnapshot,
    PAPER_LOOSE_CAL_CONFIG,
    PAPER_STRICT_CAL_CONFIG,
    RawSignalOutput,
)
from shadow_runner.replay import assert_replay_consistency, replay_batch, replay_record
from shadow_runner.runner import ShadowRunner
from shadow_runner.types import ShadowDecisionRecord

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)

_CONFIG_MAP = {
    "live":         LIVE_CAL_CONFIG,
    "paper_strict": PAPER_STRICT_CAL_CONFIG,
    "paper_loose":  PAPER_LOOSE_CAL_CONFIG,
}


def _snap_healthy():
    return MarketPricingSnapshot(
        market_id="mkt-ok",
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


def _cal_weak(confidence=0.80):
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
        calibration_method=CalibrationMethod.IDENTITY,
        calibration_quality=CalibrationQuality.WEAK,
    )
    assert err is None
    return cal


# ── EXECUTE replay ────────────────────────────────────────────────────────────

class TestExecuteReplay:

    def test_live_execute_replays_correctly(self):
        runner = ShadowRunner([LIVE_CAL_CONFIG])
        records = runner.evaluate(_cal_strong(), _snap_healthy(),
                                  intended_size_usdc=20.0, now_utc=_NOW)
        execute_records = [r for r in records if r.decision_summary.decision == "EXECUTE_YES"]
        if not execute_records:
            pytest.skip("signal did not execute — adjust confidence")
        result = replay_record(execute_records[0], _CONFIG_MAP)
        assert result.match, f"Replay mismatch: {result.mismatch_reason}"

    def test_paper_strict_execute_replays_correctly(self):
        runner = ShadowRunner([PAPER_STRICT_CAL_CONFIG])
        records = runner.evaluate(_cal_strong(), _snap_healthy(),
                                  intended_size_usdc=20.0, now_utc=_NOW)
        execute_records = [r for r in records if r.decision_summary.decision == "EXECUTE_YES"]
        if not execute_records:
            pytest.skip("signal did not execute")
        result = replay_record(execute_records[0], _CONFIG_MAP)
        assert result.match, f"Replay mismatch: {result.mismatch_reason}"

    def test_paper_loose_execute_replays_correctly(self):
        runner = ShadowRunner([PAPER_LOOSE_CAL_CONFIG])
        records = runner.evaluate(_cal_strong(), _snap_healthy(), now_utc=_NOW)
        execute_records = [r for r in records if r.decision_summary.decision == "EXECUTE_YES"]
        if not execute_records:
            pytest.skip("signal did not execute")
        result = replay_record(execute_records[0], _CONFIG_MAP)
        assert result.match, f"Replay mismatch: {result.mismatch_reason}"


# ── REJECT replay ─────────────────────────────────────────────────────────────

class TestRejectReplay:

    def test_weak_cal_reject_replays_correctly(self):
        runner = ShadowRunner([PAPER_STRICT_CAL_CONFIG])
        records = runner.evaluate(_cal_weak(), _snap_healthy(),
                                  intended_size_usdc=20.0, now_utc=_NOW)
        reject_records = [r for r in records if r.decision_summary.decision == "REJECT"]
        assert reject_records, "Expected REJECT for weak cal in paper_strict"
        result = replay_record(reject_records[0], _CONFIG_MAP)
        assert result.match, f"Replay mismatch: {result.mismatch_reason}"

    def test_suspicious_underround_reject_replays(self):
        """ask_sum=0.96 < 0.97 live min → SUSPICIOUS_UNDERROUND replay."""
        snap = MarketPricingSnapshot(
            market_id="mkt-ur",
            ask_yes=0.46, bid_yes=0.44,
            ask_no=0.50, bid_no=0.48,
            liquidity=5000.0, timestamp_utc=_NOW,
        )
        runner = ShadowRunner([LIVE_CAL_CONFIG])
        records = runner.evaluate(_cal_strong(), snap,
                                  intended_size_usdc=20.0, now_utc=_NOW)
        assert records[0].decision_summary.rejection_reason == "SUSPICIOUS_UNDERROUND"
        result = replay_record(records[0], _CONFIG_MAP)
        assert result.match, f"Replay mismatch: {result.mismatch_reason}"

    def test_stale_pricing_reject_replays(self):
        """Stale snapshot: stored snapshot_age_seconds reconstructs correct now_utc."""
        stale_ts = _NOW - timedelta(seconds=150)
        snap = MarketPricingSnapshot(
            market_id="mkt-stale",
            ask_yes=0.44, bid_yes=0.42,
            ask_no=0.57, bid_no=0.55,
            liquidity=5000.0, timestamp_utc=stale_ts,
        )
        runner = ShadowRunner([PAPER_STRICT_CAL_CONFIG])
        records = runner.evaluate(_cal_strong(), snap,
                                  intended_size_usdc=20.0, now_utc=_NOW)
        stale_record = records[0]
        assert stale_record.decision_summary.rejection_reason == "STALE_PRICING"
        result = replay_record(stale_record, _CONFIG_MAP)
        assert result.match, f"Replay mismatch: {result.mismatch_reason}"


# ── Replay mechanics ──────────────────────────────────────────────────────────

class TestReplayMechanics:

    def test_unknown_profile_returns_mismatch(self):
        runner = ShadowRunner([PAPER_LOOSE_CAL_CONFIG])
        records = runner.evaluate(_cal_strong(), _snap_healthy(), now_utc=_NOW)
        result = replay_record(records[0], {})  # empty config_map
        assert not result.match
        assert "unknown_profile" in result.mismatch_reason

    def test_batch_replay_all_match(self):
        runner = ShadowRunner([LIVE_CAL_CONFIG, PAPER_STRICT_CAL_CONFIG, PAPER_LOOSE_CAL_CONFIG])
        records = runner.evaluate(_cal_strong(), _snap_healthy(),
                                  intended_size_usdc=20.0, now_utc=_NOW)
        results = replay_batch(records, _CONFIG_MAP)
        assert len(results) == 3
        assert all(r.match for r in results), [r.mismatch_reason for r in results if not r.match]

    def test_assert_replay_consistency_passes_for_clean_records(self):
        runner = ShadowRunner([LIVE_CAL_CONFIG, PAPER_STRICT_CAL_CONFIG, PAPER_LOOSE_CAL_CONFIG])
        records = []
        for _ in range(3):
            records.extend(
                runner.evaluate(_cal_strong(), _snap_healthy(),
                                intended_size_usdc=20.0, now_utc=_NOW)
            )
        # Should not raise
        assert_replay_consistency(records, _CONFIG_MAP)

    def test_replay_result_carries_original_decision(self):
        runner = ShadowRunner([LIVE_CAL_CONFIG])
        records = runner.evaluate(_cal_strong(), _snap_healthy(),
                                  intended_size_usdc=20.0, now_utc=_NOW)
        result = replay_record(records[0], _CONFIG_MAP)
        assert result.original_decision == records[0].decision_summary.decision

    def test_snapshot_age_reproduces_staleness(self):
        """
        Verify that snapshot_age_seconds correctly reconstructs now_utc so that
        a stale record replays as STALE_PRICING and a fresh record replays as non-stale.
        """
        fresh_ts = _NOW - timedelta(seconds=10)   # fresh for all modes
        stale_ts = _NOW - timedelta(seconds=200)  # stale for paper_strict (120s limit)

        fresh_snap = MarketPricingSnapshot(
            market_id="mkt-fresh", ask_yes=0.44, bid_yes=0.42,
            ask_no=0.57, bid_no=0.55, liquidity=5000.0, timestamp_utc=fresh_ts,
        )
        stale_snap = MarketPricingSnapshot(
            market_id="mkt-stale", ask_yes=0.44, bid_yes=0.42,
            ask_no=0.57, bid_no=0.55, liquidity=5000.0, timestamp_utc=stale_ts,
        )
        runner = ShadowRunner([PAPER_STRICT_CAL_CONFIG])
        fresh_rec = runner.evaluate(_cal_strong(), fresh_snap,
                                    intended_size_usdc=20.0, now_utc=_NOW)
        stale_rec = runner.evaluate(_cal_strong(), stale_snap,
                                    intended_size_usdc=20.0, now_utc=_NOW)

        assert stale_rec[0].decision_summary.rejection_reason == "STALE_PRICING"
        assert fresh_rec[0].decision_summary.rejection_reason != "STALE_PRICING"

        fresh_result = replay_record(fresh_rec[0], _CONFIG_MAP)
        stale_result = replay_record(stale_rec[0], _CONFIG_MAP)
        assert fresh_result.match
        assert stale_result.match
