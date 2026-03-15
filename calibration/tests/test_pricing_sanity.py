"""
calibration/tests/test_pricing_sanity.py

Phase 12: Pricing sanity checks — hard structural limits and policy-specific
binary sanity band enforcement via _validate_pricing_snapshot and _check_binary_sanity.

Covers:
- Normal healthy binary quote structures (all modes pass)
- Suspiciously low ask_yes + ask_no (SUSPICIOUS_UNDERROUND per mode)
- Malformed quote structures (INVALID_PRICING regardless of mode)
- Hard structural limits (bid arb, ask < bid, price out of range)
- Low-liquidity interaction (sanity unaffected; liquidity gate separate)
- Explicit rejection reasons distinguish INVALID_PRICING from SUSPICIOUS_UNDERROUND
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from calibration.decision_policy import _check_binary_sanity, _validate_pricing_snapshot
from calibration.types import (
    BINARY_HARD_MAX_BID_SUM,
    BINARY_HARD_MIN_ASK_SUM,
    BINARY_SANITY_MIN_ASK_SUM_LIVE,
    BINARY_SANITY_MIN_ASK_SUM_PAPER_STRICT,
    BINARY_SANITY_MIN_ASK_SUM_PAPER_LOOSE,
    BINARY_SANITY_MAX_ASK_SUM_LIVE,
    BINARY_SANITY_MAX_ASK_SUM_PAPER_STRICT,
    BINARY_SANITY_MAX_ASK_SUM_PAPER_LOOSE,
    CalibrationRejectionReason,
    LIVE_CAL_CONFIG,
    MarketPricingSnapshot,
    PAPER_LOOSE_CAL_CONFIG,
    PAPER_STRICT_CAL_CONFIG,
)

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


def _snap(ask_yes=0.44, bid_yes=0.42, ask_no=0.57, bid_no=0.55,
          liquidity=5000.0, ts=None):
    return MarketPricingSnapshot(
        market_id="test",
        ask_yes=ask_yes, bid_yes=bid_yes,
        ask_no=ask_no, bid_no=bid_no,
        liquidity=liquidity,
        timestamp_utc=ts or _NOW,
    )


# ── Hard structural limits (INVALID_PRICING, all modes) ──────────────────────

class TestHardStructuralLimits:

    def test_ask_sum_below_hard_floor_is_invalid(self):
        """ask_yes + ask_no < 0.85 → INVALID_PRICING (hard floor, not underround)."""
        # Use valid spreads (ask > bid) so ask_sum check is what fires
        snap = _snap(ask_yes=0.40, bid_yes=0.38, ask_no=0.40, bid_no=0.38)  # sum=0.80 < 0.85
        err = _validate_pricing_snapshot(snap, _NOW)
        assert err is not None
        assert "hard floor" in err.lower() or "structural" in err.lower()

    def test_bid_sum_above_hard_ceiling_is_invalid(self):
        """bid_yes + bid_no > 1.01 → INVALID_PRICING (risk-free arb impossible)."""
        # ask > bid on all sides so the ask<bid check doesn't fire first
        snap = _snap(ask_yes=0.54, bid_yes=0.52, ask_no=0.54, bid_no=0.52)  # bid_sum=1.04 > 1.01
        err = _validate_pricing_snapshot(snap, _NOW)
        assert err is not None
        assert "hard ceiling" in err.lower() or "arb" in err.lower()

    def test_ask_below_bid_yes_is_invalid(self):
        snap = _snap(ask_yes=0.40, bid_yes=0.45)  # ask < bid
        err = _validate_pricing_snapshot(snap, _NOW)
        assert err is not None

    def test_ask_below_bid_no_is_invalid(self):
        snap = _snap(ask_no=0.50, bid_no=0.55)
        err = _validate_pricing_snapshot(snap, _NOW)
        assert err is not None

    def test_price_above_one_is_invalid(self):
        snap = _snap(ask_yes=1.05)
        err = _validate_pricing_snapshot(snap, _NOW)
        assert err is not None

    def test_negative_price_is_invalid(self):
        snap = _snap(bid_yes=-0.01)
        err = _validate_pricing_snapshot(snap, _NOW)
        assert err is not None

    def test_future_timestamp_is_invalid(self):
        from datetime import timedelta
        future = _NOW.replace(second=_NOW.second + 30) if False else \
            datetime(2026, 3, 15, 13, 0, 0, tzinfo=timezone.utc)
        snap = _snap(ts=future)
        err = _validate_pricing_snapshot(snap, _NOW)
        assert err is not None

    def test_hard_limits_are_independent_of_policy(self):
        """Hard structural errors are caught before policy-specific checks."""
        snap = _snap(ask_yes=0.40, ask_no=0.40)  # sum=0.80
        for config in (LIVE_CAL_CONFIG, PAPER_STRICT_CAL_CONFIG, PAPER_LOOSE_CAL_CONFIG):
            # structural check always fires
            err = _validate_pricing_snapshot(snap, _NOW)
            assert err is not None


# ── Healthy quotes pass all modes ─────────────────────────────────────────────

class TestHealthyQuotes:

    def test_normal_binary_market_passes_structural(self):
        """ask_yes=0.44 + ask_no=0.57 = 1.01 — healthy, all modes OK."""
        snap = _snap()
        assert _validate_pricing_snapshot(snap, _NOW) is None

    def test_normal_market_passes_live_sanity(self):
        snap = _snap()
        assert _check_binary_sanity(snap, LIVE_CAL_CONFIG) is None

    def test_normal_market_passes_paper_strict_sanity(self):
        snap = _snap()
        assert _check_binary_sanity(snap, PAPER_STRICT_CAL_CONFIG) is None

    def test_normal_market_passes_paper_loose_sanity(self):
        snap = _snap()
        assert _check_binary_sanity(snap, PAPER_LOOSE_CAL_CONFIG) is None

    def test_slight_overround_passes_live(self):
        """ask_sum=1.05 is normal market maker overround — passes live."""
        snap = _snap(ask_yes=0.47, ask_no=0.58)  # sum=1.05
        assert _check_binary_sanity(snap, LIVE_CAL_CONFIG) is None

    def test_bid_sum_just_below_1_passes_live(self):
        """bid_sum=0.97 — fine, no arb."""
        snap = _snap(bid_yes=0.43, bid_no=0.54)  # sum=0.97
        assert _check_binary_sanity(snap, LIVE_CAL_CONFIG) is None


# ── ask_sum band violations by mode ──────────────────────────────────────────

class TestAskSumBandByMode:

    def test_ask_sum_below_live_min_triggers_sanity(self):
        """ask_sum=0.96 < 0.97 (live min) → suspicious in live."""
        # 0.46 + 0.50 = 0.96
        snap = _snap(ask_yes=0.46, ask_no=0.50)
        result = _check_binary_sanity(snap, LIVE_CAL_CONFIG)
        assert result is not None
        assert "SUSPICIOUS_UNDERROUND" in result

    def test_ask_sum_0_96_passes_paper_strict(self):
        """ask_sum=0.96 ≥ 0.93 (paper_strict min) → OK in paper_strict."""
        snap = _snap(ask_yes=0.46, ask_no=0.50)  # sum=0.96
        result = _check_binary_sanity(snap, PAPER_STRICT_CAL_CONFIG)
        assert result is None

    def test_ask_sum_0_96_passes_paper_loose(self):
        """ask_sum=0.96 ≥ 0.88 (paper_loose min) → OK in paper_loose."""
        snap = _snap(ask_yes=0.46, ask_no=0.50)
        result = _check_binary_sanity(snap, PAPER_LOOSE_CAL_CONFIG)
        assert result is None

    def test_ask_sum_0_92_triggers_live_and_strict_not_loose(self):
        """ask_sum=0.92: fails live (0.97) and paper_strict (0.93) but passes paper_loose (0.88)."""
        snap = _snap(ask_yes=0.42, ask_no=0.50)  # sum=0.92
        assert _check_binary_sanity(snap, LIVE_CAL_CONFIG) is not None
        assert _check_binary_sanity(snap, PAPER_STRICT_CAL_CONFIG) is not None
        assert _check_binary_sanity(snap, PAPER_LOOSE_CAL_CONFIG) is None

    def test_ask_sum_0_87_triggers_all_modes(self):
        """ask_sum=0.87: fails all modes (< 0.88 paper_loose min)."""
        snap = _snap(ask_yes=0.39, ask_no=0.48)  # sum=0.87
        assert _check_binary_sanity(snap, LIVE_CAL_CONFIG) is not None
        assert _check_binary_sanity(snap, PAPER_STRICT_CAL_CONFIG) is not None
        assert _check_binary_sanity(snap, PAPER_LOOSE_CAL_CONFIG) is not None

    def test_ask_sum_above_live_max_triggers_sanity(self):
        """ask_sum=1.12 > 1.10 (live max) → suspicious (excessive vig)."""
        snap = _snap(ask_yes=0.55, ask_no=0.57)  # sum=1.12
        result = _check_binary_sanity(snap, LIVE_CAL_CONFIG)
        assert result is not None
        assert "SUSPICIOUS_UNDERROUND" in result

    def test_ask_sum_1_12_passes_paper_strict(self):
        """ask_sum=1.12 ≤ 1.15 (paper_strict max) → OK."""
        snap = _snap(ask_yes=0.55, ask_no=0.57)
        assert _check_binary_sanity(snap, PAPER_STRICT_CAL_CONFIG) is None

    def test_ask_sum_1_12_passes_paper_loose(self):
        """ask_sum=1.12 ≤ 1.25 (paper_loose max) → OK."""
        snap = _snap(ask_yes=0.55, ask_no=0.57)
        assert _check_binary_sanity(snap, PAPER_LOOSE_CAL_CONFIG) is None


# ── Threshold constants correctness ───────────────────────────────────────────

class TestThresholdConstants:

    def test_live_is_strictest_min_ask(self):
        assert BINARY_SANITY_MIN_ASK_SUM_LIVE > BINARY_SANITY_MIN_ASK_SUM_PAPER_STRICT
        assert BINARY_SANITY_MIN_ASK_SUM_PAPER_STRICT > BINARY_SANITY_MIN_ASK_SUM_PAPER_LOOSE

    def test_live_is_strictest_max_ask(self):
        assert BINARY_SANITY_MAX_ASK_SUM_LIVE < BINARY_SANITY_MAX_ASK_SUM_PAPER_STRICT
        assert BINARY_SANITY_MAX_ASK_SUM_PAPER_STRICT < BINARY_SANITY_MAX_ASK_SUM_PAPER_LOOSE

    def test_hard_floor_below_all_policy_mins(self):
        assert BINARY_HARD_MIN_ASK_SUM < BINARY_SANITY_MIN_ASK_SUM_PAPER_LOOSE
        assert BINARY_HARD_MIN_ASK_SUM < BINARY_SANITY_MIN_ASK_SUM_PAPER_STRICT
        assert BINARY_HARD_MIN_ASK_SUM < BINARY_SANITY_MIN_ASK_SUM_LIVE

    def test_hard_bid_ceiling_reasonable(self):
        assert 1.00 < BINARY_HARD_MAX_BID_SUM <= 1.02


# ── Low liquidity does not tighten sanity bands ───────────────────────────────

class TestLowLiquidityAndSanity:

    def test_low_liquidity_does_not_affect_sanity_threshold(self):
        """Low liquidity does not tighten binary sanity — liquidity is a separate gate."""
        healthy_snap = _snap(liquidity=50.0)  # below min_liquidity=1000
        # Sanity check itself passes — liquidity gate handled elsewhere
        assert _check_binary_sanity(healthy_snap, LIVE_CAL_CONFIG) is None

    def test_low_liquidity_high_ask_sum_triggers_sanity_independently(self):
        """ask_sum violation fires regardless of liquidity level."""
        snap = _snap(ask_yes=0.55, ask_no=0.57, liquidity=50.0)  # sum=1.12
        result = _check_binary_sanity(snap, LIVE_CAL_CONFIG)
        assert result is not None
