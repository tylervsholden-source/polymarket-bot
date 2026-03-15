"""
calibration/tests/test_supported_horizons.py

Phase 10: Strict horizon enforcement at every enforcement point.

Supported horizons: {5, 15} minutes. All others rejected.

Enforcement layers (defense in depth):
  Layer 1: DirectionalSignal.__post_init__       — at signal entry point
  Layer 2: decide() step 5                       — at decision boundary
  Layer 3: SUPPORTED_HORIZONS constant            — single source of truth

Policy:
  - 5m and 15m always pass horizon check
  - 1, 10, 30, 60, 240 always reject
  - Paper mode: still rejects unsupported horizons (not observation-only)
  - Live mode: still rejects unsupported horizons
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from calibration.decision_policy import decide
from calibration.probability_mapper import map_to_event_probability
from calibration.types import (
    LIVE_CAL_CONFIG,
    PAPER_CAL_CONFIG,
    SUPPORTED_HORIZONS,
    CalibrationMethod,
    CalibrationQuality,
    CalibrationRejectionReason,
    MarketPricingSnapshot,
    RawSignalOutput,
    TradeDecisionType,
)
from signal_bridge.types import Direction, DirectionalSignal

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)

_UNSUPPORTED = [1, 10, 30, 60, 120, 240]
_SUPPORTED   = [5, 15]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _raw(horizon: int, confidence: float = 0.72) -> RawSignalOutput:
    return RawSignalOutput(
        asset="BTC",
        horizon_minutes=horizon,
        timestamp_utc=_NOW,
        predicted_class="UP",
        raw_confidence=confidence,
        class_probabilities={"UP": confidence, "DOWN": 0.18, "NO_TRADE": 0.10},
    )


def _cal(horizon: int):
    raw = _raw(horizon)
    cal, err = map_to_event_probability(
        raw=raw,
        calibrated_up_prob=0.72,
        calibrated_down_prob=0.18,
        calibrated_no_trade_prob=0.10,
        polarity="NORMAL",
        calibration_method=CalibrationMethod.PLATT,
        calibration_quality=CalibrationQuality.STRONG,
    )
    assert err is None
    return cal


def _pricing() -> MarketPricingSnapshot:
    return MarketPricingSnapshot(
        market_id="mkt-test",
        ask_yes=0.44, bid_yes=0.42,
        ask_no=0.57,  bid_no=0.55,
        liquidity=5000.0,
        timestamp_utc=_NOW,
    )


# ── 1. SUPPORTED_HORIZONS constant ─────────────────────────────────────────────

class TestSupportedHorizonsConstant:

    def test_supported_horizons_is_5_and_15(self):
        """SUPPORTED_HORIZONS must be exactly {5, 15}."""
        assert SUPPORTED_HORIZONS == frozenset({5, 15})

    def test_supported_horizons_is_frozenset(self):
        """SUPPORTED_HORIZONS must be frozenset (immutable)."""
        assert isinstance(SUPPORTED_HORIZONS, frozenset)

    @pytest.mark.parametrize("h", _UNSUPPORTED)
    def test_unsupported_not_in_constant(self, h):
        assert h not in SUPPORTED_HORIZONS

    @pytest.mark.parametrize("h", _SUPPORTED)
    def test_supported_in_constant(self, h):
        assert h in SUPPORTED_HORIZONS


# ── 2. Layer 1: DirectionalSignal rejection ────────────────────────────────────

class TestDirectionalSignalHorizonEnforcement:
    """DirectionalSignal.__post_init__ is Layer 1 enforcement."""

    @pytest.mark.parametrize("h", _UNSUPPORTED)
    def test_unsupported_horizon_raises_at_directional_signal(self, h):
        """Horizon {h} raises ValueError at DirectionalSignal construction."""
        with pytest.raises(ValueError, match="horizon_minutes"):
            DirectionalSignal(
                asset="BTC",
                horizon_minutes=h,
                timestamp_utc=_NOW,
                direction=Direction.UP,
                confidence=0.72,
            )

    @pytest.mark.parametrize("h", _SUPPORTED)
    def test_supported_horizon_accepted_at_directional_signal(self, h):
        """Horizon {h} is accepted at DirectionalSignal construction."""
        sig = DirectionalSignal(
            asset="BTC",
            horizon_minutes=h,
            timestamp_utc=_NOW,
            direction=Direction.UP,
            confidence=0.72,
        )
        assert sig.horizon_minutes == h


# ── 3. Layer 2: decide() step 5 rejection ─────────────────────────────────────

class TestDecideHorizonEnforcement:
    """
    RawSignalOutput does not validate horizon (it's an observation input).
    decide() step 5 is Layer 2 enforcement for the calibration path.
    """

    @pytest.mark.parametrize("h", _UNSUPPORTED)
    def test_unsupported_horizon_rejects_in_paper(self, h):
        """Horizon {h} rejects in paper mode at decide() step 5."""
        cal = _cal(h)
        result = decide(cal, _pricing(), config=PAPER_CAL_CONFIG, now_utc=_NOW)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.UNSUPPORTED_HORIZON

    @pytest.mark.parametrize("h", _UNSUPPORTED)
    def test_unsupported_horizon_rejects_in_live(self, h):
        """Horizon {h} rejects in live mode at decide() step 5."""
        cal = _cal(h)
        result = decide(cal, _pricing(), config=LIVE_CAL_CONFIG, now_utc=_NOW,
                        intended_size_usdc=20.0)
        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.UNSUPPORTED_HORIZON

    @pytest.mark.parametrize("h", _SUPPORTED)
    def test_supported_horizon_passes_step5_paper(self, h):
        """Horizon {h} passes horizon check in paper mode."""
        cal = _cal(h)
        result = decide(cal, _pricing(), config=PAPER_CAL_CONFIG, now_utc=_NOW)
        assert result.rejection_reason != CalibrationRejectionReason.UNSUPPORTED_HORIZON

    def test_horizon_rejection_before_pricing_validation(self):
        """
        Ordering: horizon check (step 5) fires before pricing validation (step 6).
        Even with invalid pricing, unsupported horizon is the rejection reason.
        """
        cal = _cal(30)
        bad_pricing = MarketPricingSnapshot(
            market_id="mkt",
            ask_yes=1.50, bid_yes=0.10,  # invalid pricing
            ask_no=0.57,  bid_no=0.55,
            liquidity=5000.0,
            timestamp_utc=_NOW,
        )
        result = decide(cal, bad_pricing, config=PAPER_CAL_CONFIG, now_utc=_NOW)
        assert result.rejection_reason == CalibrationRejectionReason.UNSUPPORTED_HORIZON

    def test_rejection_rationale_names_supported_horizons(self):
        """Rejection rationale must mention the supported horizon list."""
        cal = _cal(60)
        result = decide(cal, _pricing(), config=PAPER_CAL_CONFIG, now_utc=_NOW)
        assert result.rejection_reason == CalibrationRejectionReason.UNSUPPORTED_HORIZON
        # Rationale should mention which horizons are supported
        assert "5" in result.rationale or "[5" in result.rationale
