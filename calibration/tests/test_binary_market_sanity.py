"""
calibration/tests/test_binary_market_sanity.py

Phase 12: Binary market sanity — ask-side, bid-side, asymmetry, liquidity.

Covers:
- Ask-side sanity band per policy mode
- Bid-side sanity enforcement (live/paper_strict only)
- One-sided quote pathology (individual ask too low)
- Asymmetry between YES and NO sides
- Interactions between sanity and liquidity
- _check_binary_sanity() return value format
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from calibration.decision_policy import _check_binary_sanity
from calibration.types import (
    BINARY_SANITY_MAX_BID_SUM_LIVE,
    BINARY_SANITY_MAX_BID_SUM_PAPER_STRICT,
    BINARY_SANITY_MIN_SINGLE_ASK_LIVE,
    BINARY_SANITY_MIN_SINGLE_ASK_PAPER_STRICT,
    CalibrationConfig,
    LIVE_CAL_CONFIG,
    MarketPricingSnapshot,
    PAPER_LOOSE_CAL_CONFIG,
    PAPER_STRICT_CAL_CONFIG,
)

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


def _snap(ask_yes=0.44, bid_yes=0.42, ask_no=0.57, bid_no=0.55, liquidity=5000.0):
    return MarketPricingSnapshot(
        market_id="test",
        ask_yes=ask_yes, bid_yes=bid_yes,
        ask_no=ask_no, bid_no=bid_no,
        liquidity=liquidity,
        timestamp_utc=_NOW,
    )


# ── Ask-side sanity ───────────────────────────────────────────────────────────

class TestAskSideSanity:

    def test_ask_sum_exact_live_min_passes(self):
        """ask_sum == live min (0.97) should just pass (boundary inclusive)."""
        # ask_yes=0.47, ask_no=0.50 → sum=0.97
        snap = _snap(ask_yes=0.47, ask_no=0.50, bid_yes=0.45, bid_no=0.48)
        result = _check_binary_sanity(snap, LIVE_CAL_CONFIG)
        assert result is None

    def test_ask_sum_just_below_live_min_fails(self):
        """ask_sum=0.969 < 0.97 → triggers live sanity."""
        # We need ask_yes + ask_no < 0.97 but > 0.85 (hard floor)
        snap = _snap(ask_yes=0.469, ask_no=0.50, bid_yes=0.449, bid_no=0.48)
        result = _check_binary_sanity(snap, LIVE_CAL_CONFIG)
        assert result is not None
        assert "SUSPICIOUS_UNDERROUND" in result

    def test_ask_sum_above_paper_strict_min_passes(self):
        """ask_sum=0.94 > 0.93 → passes paper_strict (avoid float boundary)."""
        snap = _snap(ask_yes=0.44, ask_no=0.50, bid_yes=0.42, bid_no=0.48)
        result = _check_binary_sanity(snap, PAPER_STRICT_CAL_CONFIG)
        assert result is None

    def test_ask_sum_exact_paper_loose_min_passes(self):
        """ask_sum=0.88 → just passes paper_loose."""
        snap = _snap(ask_yes=0.40, ask_no=0.48, bid_yes=0.38, bid_no=0.46)
        result = _check_binary_sanity(snap, PAPER_LOOSE_CAL_CONFIG)
        assert result is None

    def test_ask_sum_just_below_paper_loose_min_triggers_loose(self):
        """ask_sum=0.879 < 0.88 → triggers even in paper_loose."""
        snap = _snap(ask_yes=0.399, ask_no=0.48, bid_yes=0.379, bid_no=0.46)
        result = _check_binary_sanity(snap, PAPER_LOOSE_CAL_CONFIG)
        assert result is not None

    def test_max_ask_sum_at_live_max_passes(self):
        """ask_sum=1.10 at live max — should pass (bid_sum kept low to avoid bid trigger)."""
        snap = _snap(ask_yes=0.50, ask_no=0.60, bid_yes=0.40, bid_no=0.50)
        # ask_sum=1.10, bid_sum=0.90 — no bid trigger
        result = _check_binary_sanity(snap, LIVE_CAL_CONFIG)
        assert result is None

    def test_ask_sum_above_live_max_triggers(self):
        """ask_sum=1.11 > 1.10 → triggers live sanity (excessive vig)."""
        snap = _snap(ask_yes=0.54, ask_no=0.57, bid_yes=0.44, bid_no=0.47)
        # ask_sum=1.11, bid_sum=0.91 — only ask_sum triggers
        result = _check_binary_sanity(snap, LIVE_CAL_CONFIG)
        assert result is not None
        assert "SUSPICIOUS_UNDERROUND" in result

    def test_ask_sum_above_live_max_passes_paper_strict(self):
        """ask_sum=1.11 ≤ 1.15 paper_strict max → OK in paper_strict."""
        snap = _snap(ask_yes=0.54, ask_no=0.57, bid_yes=0.44, bid_no=0.47)
        # ask_sum=1.11, bid_sum=0.91
        result = _check_binary_sanity(snap, PAPER_STRICT_CAL_CONFIG)
        assert result is None

    def test_ask_sum_above_paper_strict_max_triggers_strict(self):
        """ask_sum=1.16 > 1.15 → triggers paper_strict sanity."""
        snap = _snap(ask_yes=0.59, ask_no=0.57, bid_yes=0.57, bid_no=0.55)
        result = _check_binary_sanity(snap, PAPER_STRICT_CAL_CONFIG)
        assert result is not None

    def test_return_value_format_for_violation(self):
        """Sanity violation message must contain SUSPICIOUS_UNDERROUND and mode."""
        snap = _snap(ask_yes=0.469, ask_no=0.50, bid_yes=0.449, bid_no=0.48)
        result = _check_binary_sanity(snap, LIVE_CAL_CONFIG)
        assert result is not None
        assert "SUSPICIOUS_UNDERROUND" in result
        assert "live" in result


# ── Bid-side sanity ───────────────────────────────────────────────────────────

class TestBidSideSanity:

    def test_bid_sum_exactly_live_max_passes(self):
        """bid_sum=1.00 ≤ BINARY_SANITY_MAX_BID_SUM_LIVE → passes."""
        snap = _snap(bid_yes=0.50, bid_no=0.50)  # bid_sum=1.00
        result = _check_binary_sanity(snap, LIVE_CAL_CONFIG)
        assert result is None

    def test_bid_sum_just_above_live_max_triggers(self):
        """bid_sum=1.005 > 1.00 live max → SUSPICIOUS_UNDERROUND (below hard ceiling 1.01)."""
        # bid_sum must be in (1.00, 1.01] — above policy sanity but below hard structural limit
        snap = MarketPricingSnapshot(
            market_id="test",
            ask_yes=0.53, bid_yes=0.505,
            ask_no=0.52, bid_no=0.500,  # bid_sum=1.005, ask_sum=1.05
            liquidity=5000.0, timestamp_utc=_NOW,
        )
        result = _check_binary_sanity(snap, LIVE_CAL_CONFIG)
        assert result is not None
        assert "SUSPICIOUS_UNDERROUND" in result
        assert "bid" in result.lower()

    def test_bid_sum_above_1_passes_paper_loose_because_not_checked(self):
        """paper_loose does not check bid_sum."""
        snap = MarketPricingSnapshot(
            market_id="test",
            ask_yes=0.53, bid_yes=0.505,
            ask_no=0.52, bid_no=0.500,  # bid_sum=1.005; ask_sum=1.05
            liquidity=5000.0, timestamp_utc=_NOW,
        )
        result = _check_binary_sanity(snap, PAPER_LOOSE_CAL_CONFIG)
        # paper_loose check_bid_overround=False → bid_sum not checked
        # ask_sum=1.05 within paper_loose max (1.25) → None
        assert result is None

    def test_bid_sum_constants_live_and_strict_are_same(self):
        """Live and paper_strict share the same max_bid_sum=1.00."""
        assert BINARY_SANITY_MAX_BID_SUM_LIVE == BINARY_SANITY_MAX_BID_SUM_PAPER_STRICT


# ── One-sided quote pathology (individual ask floor) ─────────────────────────

class TestOneSidedQuotePath:

    def test_very_low_ask_yes_triggers_live(self):
        """ask_yes=0.04 < 0.05 (live min_single_ask) → triggers pathology guard."""
        snap = _snap(ask_yes=0.04, ask_no=0.97, bid_yes=0.02, bid_no=0.95)
        # ask_sum=1.01 is fine, but individual ask_yes is pathologically low
        result = _check_binary_sanity(snap, LIVE_CAL_CONFIG)
        assert result is not None
        assert "SUSPICIOUS_UNDERROUND" in result
        assert "ask_yes" in result

    def test_very_low_ask_no_triggers_live(self):
        """ask_no=0.04 < 0.05 (live min_single_ask) → triggers in live."""
        snap = _snap(ask_yes=0.97, ask_no=0.04, bid_yes=0.95, bid_no=0.02)
        result = _check_binary_sanity(snap, LIVE_CAL_CONFIG)
        assert result is not None
        assert "ask_no" in result

    def test_low_ask_0_04_triggers_paper_strict(self):
        """ask_yes=0.04 < 0.03 is false (0.04 > 0.03) — check boundary."""
        # paper_strict min_single_ask=0.03. ask_yes=0.02 < 0.03 → triggers
        snap = _snap(ask_yes=0.02, ask_no=0.97, bid_yes=0.01, bid_no=0.95)
        result = _check_binary_sanity(snap, PAPER_STRICT_CAL_CONFIG)
        assert result is not None

    def test_low_ask_0_04_passes_paper_strict(self):
        """ask_yes=0.04 ≥ 0.03 (paper_strict floor) → passes individual check."""
        snap = _snap(ask_yes=0.04, ask_no=0.97, bid_yes=0.02, bid_no=0.95)
        # ask_sum=1.01 OK; individual 0.04 ≥ 0.03 OK in strict
        result = _check_binary_sanity(snap, PAPER_STRICT_CAL_CONFIG)
        assert result is None

    def test_individual_ask_not_checked_in_paper_loose(self):
        """paper_loose has no individual ask floor — pathological quote passes."""
        snap = _snap(ask_yes=0.02, ask_no=0.97, bid_yes=0.01, bid_no=0.95)
        # ask_sum=0.99 is within paper_loose band
        result = _check_binary_sanity(snap, PAPER_LOOSE_CAL_CONFIG)
        # ask_sum=0.99 is ≥ 0.88 (paper_loose min) and ≤ 1.25 (paper_loose max)
        assert result is None

    def test_single_ask_constants_live_stricter_than_strict(self):
        assert BINARY_SANITY_MIN_SINGLE_ASK_LIVE >= BINARY_SANITY_MIN_SINGLE_ASK_PAPER_STRICT


# ── Explicit config override ──────────────────────────────────────────────────

class TestExplicitConfigOverride:

    def test_explicit_min_ask_sum_overrides_mode_default(self):
        """CalibrationConfig with min_ask_sum_binary=0.99 rejects ask_sum=0.98 in live."""
        from calibration.types import CalibrationConfig
        strict_config = CalibrationConfig(
            mode="live",
            min_ask_sum_binary=0.99,
            reject_on_weak_calibration=True,
            reject_on_unknown_calibration=True,
            allow_suspicious_underround=False,
        )
        snap = _snap(ask_yes=0.48, ask_no=0.50, bid_yes=0.46, bid_no=0.48)
        # ask_sum=0.98 < 0.99 explicit override
        result = _check_binary_sanity(snap, strict_config)
        assert result is not None

    def test_explicit_max_ask_sum_overrides_mode_default(self):
        from calibration.types import CalibrationConfig
        loose_config = CalibrationConfig(
            mode="paper_loose",
            max_ask_sum_binary=1.05,  # tighter than default 1.25
            allow_suspicious_underround=True,
        )
        snap = _snap(ask_yes=0.54, ask_no=0.57, bid_yes=0.52, bid_no=0.55)
        # ask_sum=1.11 > 1.05 explicit override
        result = _check_binary_sanity(snap, loose_config)
        assert result is not None
