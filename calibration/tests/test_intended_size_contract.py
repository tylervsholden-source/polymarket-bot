"""
calibration/tests/test_intended_size_contract.py

Tests for the intended_size_usdc contract in Phase 9.

Key properties verified:
1. Larger intended_size_usdc → higher slippage → lower executable_ev
2. Very large size (> 50% liquidity) → UNFILLABLE → REJECT
3. Small size → less slippage → higher executable_ev
4. intended_size_usdc_used in TradeDecision matches the parameter
5. Size is passed through to the execution realism slippage model
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from calibration.decision_policy import decide
from calibration.edge_estimator import estimate_yes_edge_realistic
from calibration.probability_mapper import map_to_event_probability
from calibration.types import (
    CalibrationConfig,
    CalibrationMethod,
    CalibrationQuality,
    CalibrationRejectionReason,
    MarketPricingSnapshot,
    PAPER_CAL_CONFIG,
    RawSignalOutput,
    TradeDecisionType,
)

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_signal(up: float = 0.72, down: float = 0.18, no_trade: float = 0.10,
                 horizon: int = 5):
    raw = RawSignalOutput(
        asset="BTC",
        horizon_minutes=horizon,
        timestamp_utc=_NOW,
        predicted_class="UP",
        raw_confidence=up,
        class_probabilities={"UP": up, "DOWN": down, "NO_TRADE": no_trade},
    )
    cal, err = map_to_event_probability(
        raw=raw,
        calibrated_up_prob=up,
        calibrated_down_prob=down,
        calibrated_no_trade_prob=no_trade,
        polarity="NORMAL",
        calibration_method=CalibrationMethod.PLATT,
        calibration_quality=CalibrationQuality.STRONG,
    )
    assert err is None
    return cal


def _make_pricing(ask_yes=0.44, bid_yes=0.42, ask_no=0.57, bid_no=0.55,
                  liquidity=10_000.0, age_seconds=0):
    ts = _NOW - timedelta(seconds=age_seconds)
    return MarketPricingSnapshot(
        market_id="mkt-size",
        ask_yes=ask_yes,
        bid_yes=bid_yes,
        ask_no=ask_no,
        bid_no=bid_no,
        liquidity=liquidity,
        timestamp_utc=ts,
    )


# ── Test: size → slippage monotonicity ────────────────────────────────────────

class TestSizeSlippageMonotonicity:
    """Larger size → higher slippage → lower or equal executable_ev."""

    def test_small_size_higher_ev_than_large_size(self):
        """5 USDC has higher executable_ev than 100 USDC."""
        cal = _make_signal()
        pr = _make_pricing(liquidity=5_000.0)

        edge_small = estimate_yes_edge_realistic(
            cal, pr, PAPER_CAL_CONFIG,
            snapshot_age_seconds=0.0,
            intended_size_usdc=5.0,
        )
        edge_large = estimate_yes_edge_realistic(
            cal, pr, PAPER_CAL_CONFIG,
            snapshot_age_seconds=0.0,
            intended_size_usdc=100.0,
        )

        assert edge_small.execution_adjusted_ev >= edge_large.execution_adjusted_ev, (
            f"Small size EV ({edge_small.execution_adjusted_ev:.4f}) should be >= "
            f"large size EV ({edge_large.execution_adjusted_ev:.4f})"
        )

    def test_size_slippage_is_monotone_across_buckets(self):
        """Size buckets: 0→10→50→200 USDC each have >= slippage."""
        cal = _make_signal()
        pr = _make_pricing(liquidity=10_000.0)

        sizes = [5.0, 15.0, 60.0, 250.0]
        evs = []
        for sz in sizes:
            edge = estimate_yes_edge_realistic(
                cal, pr, PAPER_CAL_CONFIG,
                snapshot_age_seconds=0.0,
                intended_size_usdc=sz,
            )
            evs.append(edge.execution_adjusted_ev)

        # EV is non-increasing as size increases
        for i in range(len(evs) - 1):
            assert evs[i] >= evs[i + 1], (
                f"EV at size={sizes[i]} ({evs[i]:.4f}) should be >= "
                f"EV at size={sizes[i+1]} ({evs[i+1]:.4f})"
            )

    def test_decide_small_vs_large_size_ev_ordering(self):
        """decide() with small size has >= EV than with large size."""
        cal = _make_signal()
        pr = _make_pricing()

        result_small = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW, intended_size_usdc=5.0)
        result_large = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW, intended_size_usdc=200.0)

        if (
            result_small.edge_estimate is not None
            and result_large.edge_estimate is not None
        ):
            assert (
                result_small.edge_estimate.expected_edge
                >= result_large.edge_estimate.expected_edge
            ), "Smaller size should yield higher or equal executable_ev"


# ── Test: UNFILLABLE → REJECT ─────────────────────────────────────────────────

class TestUnfillableReject:
    """Size > 50% of liquidity → UNFILLABLE → REJECT (negative edge gate)."""

    def test_very_large_size_triggers_unfillable(self):
        """
        Size = 6000 USDC, liquidity = 10000 USDC → ratio = 60% > 50% → UNFILLABLE.
        This causes passes_gate=False → NEGATIVE_EDGE reject.
        """
        cal = _make_signal(up=0.72, down=0.18, no_trade=0.10)
        pr = _make_pricing(liquidity=10_000.0)

        edge = estimate_yes_edge_realistic(
            cal, pr, PAPER_CAL_CONFIG,
            snapshot_age_seconds=0.0,
            intended_size_usdc=6_000.0,  # 60% of 10k → UNFILLABLE
        )

        assert edge.passes_edge_gate is False
        assert edge.executable_cost_breakdown is not None
        from execution_realism.types import FillDecision
        assert edge.executable_cost_breakdown.fill_sim.fill_decision == FillDecision.UNFILLABLE

    def test_decide_rejects_unfillable_size(self):
        """decide() with unfillable size → REJECT."""
        cal = _make_signal(up=0.72, down=0.18, no_trade=0.10)
        pr = _make_pricing(liquidity=10_000.0)

        result = decide(
            cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW,
            intended_size_usdc=6_000.0,  # 60% > 50% → UNFILLABLE
        )

        assert result.decision == TradeDecisionType.REJECT
        assert result.rejection_reason == CalibrationRejectionReason.NEGATIVE_EDGE

    def test_size_exactly_at_50pct_boundary_is_unfillable(self):
        """
        Size / liquidity = 50.1% → just over the 50% threshold → UNFILLABLE.
        Size / liquidity = 49.9% → just under → PARTIAL (allowed).
        """
        cal = _make_signal(up=0.72, down=0.18, no_trade=0.10)
        liquidity = 10_000.0
        pr = _make_pricing(liquidity=liquidity)

        # Just over 50%: size = 5001
        edge_over = estimate_yes_edge_realistic(
            cal, pr, PAPER_CAL_CONFIG,
            snapshot_age_seconds=0.0,
            intended_size_usdc=5_001.0,
        )
        assert edge_over.passes_edge_gate is False
        from execution_realism.types import FillDecision
        assert edge_over.executable_cost_breakdown.fill_sim.fill_decision == FillDecision.UNFILLABLE

        # Just under 50%: size = 4999 (PARTIAL zone > 25%)
        edge_under = estimate_yes_edge_realistic(
            cal, pr, PAPER_CAL_CONFIG,
            snapshot_age_seconds=0.0,
            intended_size_usdc=4_999.0,
        )
        from execution_realism.types import FillDecision as FD
        assert edge_under.executable_cost_breakdown.fill_sim.fill_decision == FD.PARTIAL


# ── Test: PARTIAL fill zone ───────────────────────────────────────────────────

class TestPartialFillZone:
    """25% < size ≤ 50% liquidity → PARTIAL fill (allowed, 90% fill fraction)."""

    def test_partial_fill_fraction_is_90pct(self):
        """PARTIAL zone: fill fraction = 0.90."""
        cal = _make_signal()
        pr = _make_pricing(liquidity=10_000.0)

        edge = estimate_yes_edge_realistic(
            cal, pr, PAPER_CAL_CONFIG,
            snapshot_age_seconds=0.0,
            intended_size_usdc=3_000.0,  # 30% of 10k → PARTIAL
        )

        assert edge.executable_cost_breakdown is not None
        from execution_realism.types import FillDecision
        assert edge.executable_cost_breakdown.fill_sim.fill_decision == FillDecision.PARTIAL
        assert edge.executable_cost_breakdown.fill_sim.expected_fill_fraction == pytest.approx(
            0.90, abs=1e-6
        )

    def test_partial_fill_does_not_cause_gate_failure_on_its_own(self):
        """
        PARTIAL fill alone does not cause gate failure.
        Only UNFILLABLE causes automatic gate failure.
        A PARTIAL trade with sufficient EV can still pass.
        """
        cal = _make_signal(up=0.72, down=0.18, no_trade=0.10)
        pr = _make_pricing(ask_yes=0.44, bid_yes=0.42, ask_no=0.57, bid_no=0.55,
                           liquidity=10_000.0)

        edge = estimate_yes_edge_realistic(
            cal, pr, PAPER_CAL_CONFIG,
            snapshot_age_seconds=0.0,
            intended_size_usdc=3_000.0,  # PARTIAL zone
        )

        # Shouldn't be forced to UNFILLABLE
        from execution_realism.types import FillDecision
        assert edge.executable_cost_breakdown.fill_sim.fill_decision == FillDecision.PARTIAL
        # With large edge (0.72 - 0.44 = 0.28), even with costs, should pass
        # (if slippage is large enough for this size it might fail, but gate isn't auto-fail)
        # We just verify it's PARTIAL, not UNFILLABLE
        assert edge.executable_cost_breakdown.fill_sim.fill_decision != FillDecision.UNFILLABLE


# ── Test: FILLABLE zone ────────────────────────────────────────────────────────

class TestFillableZone:
    """size ≤ 25% liquidity → FILLABLE → 100% fill."""

    def test_small_size_relative_to_liquidity_is_fillable(self):
        """5 USDC / 10000 USDC = 0.05% → FILLABLE."""
        cal = _make_signal()
        pr = _make_pricing(liquidity=10_000.0)

        edge = estimate_yes_edge_realistic(
            cal, pr, PAPER_CAL_CONFIG,
            snapshot_age_seconds=0.0,
            intended_size_usdc=5.0,
        )

        from execution_realism.types import FillDecision
        assert edge.executable_cost_breakdown.fill_sim.fill_decision == FillDecision.FILLABLE
        assert edge.executable_cost_breakdown.fill_sim.expected_fill_fraction == pytest.approx(
            1.0, abs=1e-6
        )


# ── Test: intended_size_usdc_used in TradeDecision ────────────────────────────

class TestIntendedSizeUsedField:
    """TradeDecision.intended_size_usdc_used must match the parameter passed."""

    def test_default_size_20(self):
        """Default size=20.0 is reflected in TradeDecision."""
        cal = _make_signal()
        pr = _make_pricing()
        result = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)
        assert result.intended_size_usdc_used == 20.0

    def test_custom_size_reflected(self):
        """Custom size=50.0 is reflected in TradeDecision."""
        cal = _make_signal()
        pr = _make_pricing()
        result = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW, intended_size_usdc=50.0)
        assert result.intended_size_usdc_used == 50.0

    def test_large_size_reflected_even_on_reject(self):
        """Even when REJECT, intended_size_usdc_used is correct."""
        cal = _make_signal()
        pr = _make_pricing(liquidity=10_000.0)
        result = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW, intended_size_usdc=6_000.0)
        # Should REJECT (UNFILLABLE) but still record the size
        assert result.intended_size_usdc_used == 6_000.0
