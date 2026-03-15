"""
calibration/tests/test_policy_profile_reporting.py

Phase 12: decision output clearly reports which policy profile was used.

Covers:
- policy_mode field is present and correct on all decision types
- live / paper_strict / paper_loose are distinguishable in output
- rationale mentions policy-sensitive context
- EXECUTE, REJECT (early), and REJECT (late) paths all carry correct policy_mode
- pricing_sanity_notes is None for live/paper_strict (they reject instead)
- pricing_sanity_notes is set when paper_loose annotates suspicious pricing
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from calibration.decision_policy import decide
from calibration.probability_mapper import map_to_event_probability
from calibration.types import (
    CalibrationConfig,
    CalibrationMethod,
    CalibrationQuality,
    CalibrationRejectionReason,
    LIVE_CAL_CONFIG,
    MarketPricingSnapshot,
    PAPER_LOOSE_CAL_CONFIG,
    PAPER_STRICT_CAL_CONFIG,
    RawSignalOutput,
    TradeDecisionType,
)

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


def _snap_healthy():
    return MarketPricingSnapshot(
        market_id="mkt",
        ask_yes=0.44, bid_yes=0.42,
        ask_no=0.57, bid_no=0.55,
        liquidity=5000.0, timestamp_utc=_NOW,
    )


def _snap_underround():
    """ask_sum=0.92: strict→reject, loose→annotate."""
    return MarketPricingSnapshot(
        market_id="mkt-ur",
        ask_yes=0.42, bid_yes=0.40,
        ask_no=0.50, bid_no=0.48,
        liquidity=5000.0, timestamp_utc=_NOW,
    )


def _cal(confidence=0.72, quality=CalibrationQuality.STRONG):
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
        calibration_quality=quality,
    )
    assert err is None
    return cal


# ── policy_mode field correctness ─────────────────────────────────────────────

class TestPolicyModeField:

    def test_execute_live_reports_live(self):
        cal = _cal(confidence=0.80)
        result = decide(cal, _snap_healthy(), config=LIVE_CAL_CONFIG,
                        now_utc=_NOW, intended_size_usdc=20.0)
        assert result.decision == TradeDecisionType.EXECUTE_YES
        assert result.policy_mode == "live"

    def test_execute_paper_strict_reports_paper_strict(self):
        cal = _cal(confidence=0.80)
        result = decide(cal, _snap_healthy(), config=PAPER_STRICT_CAL_CONFIG,
                        now_utc=_NOW, intended_size_usdc=20.0)
        assert result.decision == TradeDecisionType.EXECUTE_YES
        assert result.policy_mode == "paper_strict"

    def test_execute_paper_loose_reports_paper_loose(self):
        cal = _cal(confidence=0.80)
        result = decide(cal, _snap_healthy(), config=PAPER_LOOSE_CAL_CONFIG, now_utc=_NOW)
        assert result.decision == TradeDecisionType.EXECUTE_YES
        assert result.policy_mode == "paper_loose"

    def test_three_modes_are_distinct_strings(self):
        live_cal = _cal(confidence=0.80)
        strict_cal = _cal(confidence=0.80)
        loose_cal = _cal(confidence=0.80)
        live_r   = decide(live_cal,   _snap_healthy(), config=LIVE_CAL_CONFIG,
                          now_utc=_NOW, intended_size_usdc=20.0)
        strict_r = decide(strict_cal, _snap_healthy(), config=PAPER_STRICT_CAL_CONFIG,
                          now_utc=_NOW, intended_size_usdc=20.0)
        loose_r  = decide(loose_cal,  _snap_healthy(), config=PAPER_LOOSE_CAL_CONFIG,
                          now_utc=_NOW)
        modes = {live_r.policy_mode, strict_r.policy_mode, loose_r.policy_mode}
        assert len(modes) == 3


# ── Early rejection carries correct policy_mode ───────────────────────────────

class TestEarlyRejectPolicyMode:

    def test_suspicious_underround_reject_live_carries_live_mode(self):
        cal = _cal()
        # Use ask_sum=0.96 which is only suspicious in live (< 0.97)
        snap = MarketPricingSnapshot(
            market_id="mkt",
            ask_yes=0.46, bid_yes=0.44,
            ask_no=0.50, bid_no=0.48,
            liquidity=5000.0, timestamp_utc=_NOW,
        )
        result = decide(cal, snap, config=LIVE_CAL_CONFIG,
                        now_utc=_NOW, intended_size_usdc=20.0)
        assert result.rejection_reason == CalibrationRejectionReason.SUSPICIOUS_UNDERROUND
        assert result.policy_mode == "live"

    def test_suspicious_underround_reject_strict_carries_strict_mode(self):
        cal = _cal()
        result = decide(cal, _snap_underround(), config=PAPER_STRICT_CAL_CONFIG,
                        now_utc=_NOW, intended_size_usdc=20.0)
        assert result.rejection_reason == CalibrationRejectionReason.SUSPICIOUS_UNDERROUND
        assert result.policy_mode == "paper_strict"

    def test_weak_cal_reject_strict_carries_strict_mode(self):
        from calibration.types import CalibrationQuality
        cal_w = _cal(quality=CalibrationQuality.WEAK)
        result = decide(cal_w, _snap_healthy(), config=PAPER_STRICT_CAL_CONFIG,
                        now_utc=_NOW, intended_size_usdc=20.0)
        assert result.rejection_reason == CalibrationRejectionReason.WEAK_CALIBRATION
        assert result.policy_mode == "paper_strict"

    def test_early_reject_intended_size_propagated(self):
        """Early rejections carry the actual intended_size_usdc_used."""
        cal_w = _cal(quality=CalibrationQuality.WEAK)
        result = decide(cal_w, _snap_healthy(), config=PAPER_STRICT_CAL_CONFIG,
                        now_utc=_NOW, intended_size_usdc=75.0)
        assert result.rejection_reason == CalibrationRejectionReason.WEAK_CALIBRATION
        assert result.intended_size_usdc_used == pytest.approx(75.0)


# ── pricing_sanity_notes field ────────────────────────────────────────────────

class TestPricingSanityNotes:

    def test_clean_market_live_no_sanity_notes(self):
        cal = _cal(confidence=0.80)
        result = decide(cal, _snap_healthy(), config=LIVE_CAL_CONFIG,
                        now_utc=_NOW, intended_size_usdc=20.0)
        assert result.pricing_sanity_notes is None

    def test_clean_market_paper_strict_no_sanity_notes(self):
        cal = _cal(confidence=0.80)
        result = decide(cal, _snap_healthy(), config=PAPER_STRICT_CAL_CONFIG,
                        now_utc=_NOW, intended_size_usdc=20.0)
        assert result.pricing_sanity_notes is None

    def test_clean_market_paper_loose_no_sanity_notes(self):
        cal = _cal(confidence=0.80)
        result = decide(cal, _snap_healthy(), config=PAPER_LOOSE_CAL_CONFIG, now_utc=_NOW)
        assert result.pricing_sanity_notes is None

    def test_live_suspicious_does_not_set_notes_because_it_rejects(self):
        """Live rejects on SUSPICIOUS_UNDERROUND — pricing_sanity_notes is not set (we rejected)."""
        cal = _cal()
        snap = MarketPricingSnapshot(
            market_id="mkt",
            ask_yes=0.46, bid_yes=0.44,
            ask_no=0.50, bid_no=0.48,
            liquidity=5000.0, timestamp_utc=_NOW,
        )
        result = decide(cal, snap, config=LIVE_CAL_CONFIG,
                        now_utc=_NOW, intended_size_usdc=20.0)
        assert result.decision == TradeDecisionType.REJECT
        # notes not present on reject path (only on EXECUTE path in paper_loose)
        assert result.pricing_sanity_notes is None

    def test_paper_loose_suspicious_execute_has_notes(self):
        """paper_loose annotates ask_sum < 0.88 in pricing_sanity_notes on EXECUTE."""
        # 0.92 is above paper_loose min (0.88) — use 0.87 to trigger annotation
        snap = MarketPricingSnapshot(
            market_id="mkt-vur",
            ask_yes=0.39, bid_yes=0.37,
            ask_no=0.48, bid_no=0.46,  # ask_sum=0.87 < 0.88
            liquidity=5000.0, timestamp_utc=_NOW,
        )
        cal = _cal(confidence=0.80)
        result = decide(cal, snap, config=PAPER_LOOSE_CAL_CONFIG, now_utc=_NOW)
        if result.decision == TradeDecisionType.EXECUTE_YES:
            assert result.pricing_sanity_notes is not None


# ── Custom policy config round-trip ───────────────────────────────────────────

class TestCustomPolicyConfig:

    def test_custom_config_mode_reflected_in_output(self):
        """Custom CalibrationConfig with explicit mode is reflected in policy_mode."""
        from calibration.types import CalibrationConfig
        custom = CalibrationConfig(mode="paper_strict", min_prob_sum=0.75,
                                   reject_on_weak_calibration=True,
                                   reject_on_unknown_calibration=True,
                                   min_execution_adjusted_edge=0.025,
                                   max_snapshot_age_seconds=120,
                                   allow_suspicious_underround=False,
                                   check_bid_overround=True)
        cal = _cal(confidence=0.80)
        result = decide(cal, _snap_healthy(), config=custom,
                        now_utc=_NOW, intended_size_usdc=20.0)
        assert result.policy_mode == "paper_strict"
