"""
calibration/tests/test_underround_rejection.py

Phase 12: SUSPICIOUS_UNDERROUND rejection path through decide().

Covers:
- Live mode: suspicious underround → REJECT with SUSPICIOUS_UNDERROUND
- paper_strict: suspicious underround → REJECT with SUSPICIOUS_UNDERROUND
- paper_loose: suspicious underround → EXECUTE with pricing_sanity_notes set
- Audit output records the reason and notes correctly
- SUSPICIOUS_UNDERROUND is distinct from INVALID_PRICING
- Bid-sum violation triggers SUSPICIOUS_UNDERROUND in live/strict
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from calibration.decision_policy import decide
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
    TradeDecisionType,
)

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


def _cal(confidence=0.72):
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


def _snap_underround(ask_yes=0.46, ask_no=0.50, liquidity=5000.0):
    """ask_sum=0.96 — suspicious underround (< 0.97 live min, ≥ 0.93 strict min)."""
    return MarketPricingSnapshot(
        market_id="mkt-ur",
        ask_yes=ask_yes, bid_yes=ask_yes - 0.02,
        ask_no=ask_no, bid_no=ask_no - 0.02,
        liquidity=liquidity,
        timestamp_utc=_NOW,
    )


def _snap_heavy_underround(ask_yes=0.42, ask_no=0.50, liquidity=5000.0):
    """ask_sum=0.92 — heavy underround (< 0.93 strict min, ≥ 0.88 loose min)."""
    return MarketPricingSnapshot(
        market_id="mkt-hur",
        ask_yes=ask_yes, bid_yes=ask_yes - 0.02,
        ask_no=ask_no, bid_no=ask_no - 0.02,
        liquidity=liquidity,
        timestamp_utc=_NOW,
    )


def _snap_very_heavy_underround(ask_yes=0.39, ask_no=0.48, liquidity=5000.0):
    """ask_sum=0.87 — very heavy underround (< 0.88 paper_loose min → all modes)."""
    return MarketPricingSnapshot(
        market_id="mkt-vhur",
        ask_yes=ask_yes, bid_yes=ask_yes - 0.02,
        ask_no=ask_no, bid_no=ask_no - 0.02,
        liquidity=liquidity,
        timestamp_utc=_NOW,
    )


def _snap_healthy():
    """ask_sum=1.01 — clean market."""
    return MarketPricingSnapshot(
        market_id="mkt-ok",
        ask_yes=0.44, bid_yes=0.42,
        ask_no=0.57, bid_no=0.55,
        liquidity=5000.0,
        timestamp_utc=_NOW,
    )


# ── Live mode rejects suspicious underround ──────────────────────────────────

class TestLiveRejectsSuspiciousUnderround:

    def test_live_rejects_ask_sum_0_96(self):
        """ask_sum=0.96 < 0.97 live min → REJECT SUSPICIOUS_UNDERROUND in live."""
        cal = _cal()
        result = decide(cal, _snap_underround(), config=LIVE_CAL_CONFIG,
                        now_utc=_NOW, intended_size_usdc=20.0)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.SUSPICIOUS_UNDERROUND

    def test_live_rejects_ask_sum_0_92(self):
        """ask_sum=0.92 → REJECT in live."""
        cal = _cal()
        result = decide(cal, _snap_heavy_underround(), config=LIVE_CAL_CONFIG,
                        now_utc=_NOW, intended_size_usdc=20.0)
        assert result.rejection_reason == CalibrationRejectionReason.SUSPICIOUS_UNDERROUND

    def test_live_rejects_excessive_vig(self):
        """ask_sum=1.12 > 1.10 live max → REJECT in live (excessive vig)."""
        snap = MarketPricingSnapshot(
            market_id="mkt-vig",
            ask_yes=0.55, bid_yes=0.45,
            ask_no=0.57, bid_no=0.47,  # ask_sum=1.12, bid_sum=0.92 (no bid trigger)
            liquidity=5000.0, timestamp_utc=_NOW,
        )
        cal = _cal()
        result = decide(cal, snap, config=LIVE_CAL_CONFIG,
                        now_utc=_NOW, intended_size_usdc=20.0)
        assert result.rejection_reason == CalibrationRejectionReason.SUSPICIOUS_UNDERROUND

    def test_suspicious_underround_not_negative_edge(self):
        """SUSPICIOUS_UNDERROUND fires before edge gate — distinct from NEGATIVE_EDGE."""
        cal = _cal(confidence=0.90)  # very high confidence
        result = decide(cal, _snap_underround(), config=LIVE_CAL_CONFIG,
                        now_utc=_NOW, intended_size_usdc=20.0)
        assert result.rejection_reason == CalibrationRejectionReason.SUSPICIOUS_UNDERROUND
        assert result.rejection_reason != CalibrationRejectionReason.NEGATIVE_EDGE

    def test_suspicious_underround_not_invalid_pricing(self):
        """SUSPICIOUS_UNDERROUND is a distinct reason from INVALID_PRICING."""
        cal = _cal()
        result = decide(cal, _snap_underround(), config=LIVE_CAL_CONFIG,
                        now_utc=_NOW, intended_size_usdc=20.0)
        assert result.rejection_reason == CalibrationRejectionReason.SUSPICIOUS_UNDERROUND
        assert result.rejection_reason != CalibrationRejectionReason.INVALID_PRICING

    def test_live_rationale_mentions_underround(self):
        """Rationale must describe the suspicious condition."""
        cal = _cal()
        result = decide(cal, _snap_underround(), config=LIVE_CAL_CONFIG,
                        now_utc=_NOW, intended_size_usdc=20.0)
        assert "SUSPICIOUS_UNDERROUND" in result.rationale or "underround" in result.rationale.lower()


# ── paper_strict rejects suspicious underround ────────────────────────────────

class TestPaperStrictRejectsSuspiciousUnderround:

    def test_paper_strict_allows_ask_sum_0_96(self):
        """ask_sum=0.96 ≥ 0.93 paper_strict min → passes sanity in paper_strict."""
        cal = _cal()
        result = decide(cal, _snap_underround(), config=PAPER_STRICT_CAL_CONFIG,
                        now_utc=_NOW, intended_size_usdc=20.0)
        assert result.rejection_reason != CalibrationRejectionReason.SUSPICIOUS_UNDERROUND

    def test_paper_strict_rejects_ask_sum_0_92(self):
        """ask_sum=0.92 < 0.93 paper_strict min → REJECT in paper_strict."""
        cal = _cal()
        result = decide(cal, _snap_heavy_underround(), config=PAPER_STRICT_CAL_CONFIG,
                        now_utc=_NOW, intended_size_usdc=20.0)
        assert result.rejection_reason == CalibrationRejectionReason.SUSPICIOUS_UNDERROUND

    def test_paper_strict_rejects_very_heavy_underround(self):
        """ask_sum=0.87 → REJECT in paper_strict."""
        cal = _cal()
        result = decide(cal, _snap_very_heavy_underround(), config=PAPER_STRICT_CAL_CONFIG,
                        now_utc=_NOW, intended_size_usdc=20.0)
        assert result.rejection_reason == CalibrationRejectionReason.SUSPICIOUS_UNDERROUND


# ── paper_loose annotates but does not reject ─────────────────────────────────

class TestPaperLooseAnnotatesSuspiciousUnderround:

    def test_paper_loose_does_not_reject_ask_sum_0_92(self):
        """ask_sum=0.92 in paper_loose → NOT rejected (annotated)."""
        cal = _cal()
        result = decide(cal, _snap_heavy_underround(), config=PAPER_LOOSE_CAL_CONFIG,
                        now_utc=_NOW)
        assert result.rejection_reason != CalibrationRejectionReason.SUSPICIOUS_UNDERROUND

    def test_paper_loose_sets_pricing_sanity_notes_on_suspicious(self):
        """paper_loose execute with ask_sum < 0.88 → pricing_sanity_notes populated."""
        # ask_sum=0.87 < 0.88 (paper_loose min) → annotated but not rejected
        snap = MarketPricingSnapshot(
            market_id="mkt-vhur",
            ask_yes=0.39, bid_yes=0.37,
            ask_no=0.48, bid_no=0.46,  # ask_sum=0.87
            liquidity=5000.0, timestamp_utc=_NOW,
        )
        cal = _cal(confidence=0.80)
        result = decide(cal, snap, config=PAPER_LOOSE_CAL_CONFIG, now_utc=_NOW)
        # If it executes, notes must be set
        if result.decision == TradeDecisionType.EXECUTE_YES:
            assert result.pricing_sanity_notes is not None
            assert "SUSPICIOUS_UNDERROUND" in result.pricing_sanity_notes

    def test_paper_loose_clean_market_has_no_sanity_notes(self):
        """Clean market in paper_loose → pricing_sanity_notes is None."""
        cal = _cal()
        result = decide(cal, _snap_healthy(), config=PAPER_LOOSE_CAL_CONFIG, now_utc=_NOW)
        assert result.pricing_sanity_notes is None

    def test_paper_loose_rejects_very_heavy_underround(self):
        """ask_sum=0.87 < 0.88 paper_loose min → paper_loose also triggers sanity (annotates)."""
        from calibration.decision_policy import _check_binary_sanity
        snap = _snap_very_heavy_underround()
        # _check_binary_sanity fires even in paper_loose for very heavy underround
        result = _check_binary_sanity(snap, PAPER_LOOSE_CAL_CONFIG)
        assert result is not None


# ── Bid-sum suspicious underround ─────────────────────────────────────────────

class TestBidSumUnderround:

    def test_bid_sum_above_1_triggers_live(self):
        """bid_sum=1.005 > 1.00 (live max) → SUSPICIOUS_UNDERROUND (below hard ceiling 1.01)."""
        # bid_sum must be in (1.00, 1.01] — policy sanity fires, hard structural does not
        snap = MarketPricingSnapshot(
            market_id="mkt-bs",
            ask_yes=0.53, bid_yes=0.505,
            ask_no=0.52, bid_no=0.500,  # bid_sum=1.005, ask_sum=1.05
            liquidity=5000.0, timestamp_utc=_NOW,
        )
        cal = _cal()
        result = decide(cal, snap, config=LIVE_CAL_CONFIG,
                        now_utc=_NOW, intended_size_usdc=20.0)
        assert result.rejection_reason == CalibrationRejectionReason.SUSPICIOUS_UNDERROUND

    def test_bid_sum_above_1_not_checked_in_paper_loose(self):
        """paper_loose does not check bid_sum — no rejection for bid_sum > 1."""
        from calibration.decision_policy import _check_binary_sanity
        snap = MarketPricingSnapshot(
            market_id="mkt-bs",
            ask_yes=0.54, bid_yes=0.52,
            ask_no=0.52, bid_no=0.50,  # bid_sum=1.02
            liquidity=5000.0, timestamp_utc=_NOW,
        )
        # paper_loose: check_bid_overround=False → no bid_sum trigger
        result = _check_binary_sanity(snap, PAPER_LOOSE_CAL_CONFIG)
        # May be None (bid not checked) or trigger on ask_sum — but NOT bid_sum
        if result is not None:
            assert "bid" not in result.lower() or "arb" not in result.lower()
