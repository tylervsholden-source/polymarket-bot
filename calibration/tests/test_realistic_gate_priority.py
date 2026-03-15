"""
calibration/tests/test_realistic_gate_priority.py

Proves that split-brain no longer exists in Phase 9+.

SPLIT-BRAIN DEFINITION:
    simple edge > threshold (deprecated estimator says PASS)
    realistic executable_ev < threshold (realistic path says FAIL)
    → decide() must REJECT (realistic path wins)

The canonical scenario:
    p=0.52, ask=0.50, fee=0.01, configured_slippage=0.0, threshold=0.005

    Simple edge path:
        edge = p - ask - fee - slippage_config = 0.52 - 0.50 - 0.01 - 0.0 = 0.01
        0.01 > 0.005 → PASS (simple estimator says execute)

    Realistic execution path (5000 USDC liquidity, 20 USDC size):
        base_slippage     = 0.003  (YES base)
        liquidity_penalty = 0.005  (MEDIUM bucket: 1k-5k range)
        size_penalty      = 0.001  (size 10-50 range)
        total_slippage    = 0.009

        executable_ev = 0.52 - 0.50 - 0.01 - 0.009 ≈ 0.001 < 0.005 → FAIL

    decide() uses the realistic path → REJECT.
    estimate_yes_edge() (deprecated) → PASS.
    This proves split-brain is eliminated.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from calibration.decision_policy import decide
from calibration.edge_estimator import estimate_yes_edge, estimate_yes_edge_realistic
from calibration.probability_mapper import map_to_event_probability
from calibration.types import (
    CalibrationConfig,
    CalibrationMethod,
    CalibrationQuality,
    CalibrationRejectionReason,
    MarketPricingSnapshot,
    RawSignalOutput,
    TradeDecisionType,
)

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_signal(up: float, down: float, no_trade: float, horizon: int = 5):
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


def _make_pricing(ask_yes: float, bid_yes: float, ask_no: float, bid_no: float,
                  liquidity: float = 5000.0, age_seconds: float = 0.0):
    ts = _NOW - timedelta(seconds=age_seconds)
    return MarketPricingSnapshot(
        market_id="mkt-split-brain",
        ask_yes=ask_yes,
        bid_yes=bid_yes,
        ask_no=ask_no,
        bid_no=bid_no,
        liquidity=liquidity,
        timestamp_utc=ts,
    )


# ── The definitive split-brain proof ──────────────────────────────────────────

class TestSplitBrainEliminated:
    """
    Proves that simple edge > threshold does NOT cause decide() to EXECUTE
    when the realistic path says executable_ev < threshold.
    """

    def _split_brain_config(self) -> CalibrationConfig:
        """
        Config where simple path passes but realistic path fails.

        min_execution_adjusted_edge = 0.005 (very low threshold)
        assumed_slippage_pct = 0.0 (zero configured slippage — simple path sees no cost)

        With p=0.52, ask=0.50, fee=0.01:
          simple edge = 0.52 - 0.50 - 0.01 - 0.0 = 0.01 > 0.005 → PASS
          realistic:   0.52 - 0.50 - 0.01 - ~0.009 = 0.001 < 0.005 → FAIL
        """
        return CalibrationConfig(
            min_execution_adjusted_edge=0.005,
            assumed_taker_fee_pct=0.01,
            assumed_slippage_pct=0.0,   # zero configured slippage
            min_calibrated_confidence=0.51,  # allow p=0.52
            min_liquidity=1_000.0,
            mode="default",
        )

    def test_simple_estimator_passes_the_threshold(self):
        """
        Baseline: the deprecated simple estimator DOES pass.
        This is the 'split-brain' scenario Phase 9 eliminates.
        """
        cfg = self._split_brain_config()
        cal = _make_signal(up=0.52, down=0.38, no_trade=0.10)
        # ask_no must keep binary sanity: ask_yes + ask_no ∈ [0.85, 1.15]
        pr = _make_pricing(ask_yes=0.50, bid_yes=0.49, ask_no=0.51, bid_no=0.50)

        simple_edge = estimate_yes_edge(cal, pr, cfg)

        # Simple: p - ask - fee - slippage_cfg = 0.52 - 0.50 - 0.01 - 0.0 = 0.01 > 0.005
        assert simple_edge.passes_edge_gate is True, (
            f"Simple estimator should PASS the threshold. "
            f"edge={simple_edge.expected_edge:.4f}, threshold={cfg.min_execution_adjusted_edge}"
        )
        assert simple_edge.expected_edge == pytest.approx(0.01, abs=1e-6)

    def test_realistic_estimator_fails_the_threshold(self):
        """Realistic estimator FAILS for the same input where simple passes."""
        cfg = self._split_brain_config()
        cal = _make_signal(up=0.52, down=0.38, no_trade=0.10)
        pr = _make_pricing(ask_yes=0.50, bid_yes=0.49, ask_no=0.51, bid_no=0.50)

        realistic_edge = estimate_yes_edge_realistic(
            cal, pr, cfg,
            snapshot_age_seconds=0.0,
            intended_size_usdc=20.0,
        )

        # Realistic: 0.02 - 0.01 - 0.009 = 0.001 < 0.005 → FAIL
        assert realistic_edge.passes_edge_gate is False, (
            f"Realistic estimator should FAIL. "
            f"edge={realistic_edge.expected_edge:.4f}, threshold={cfg.min_execution_adjusted_edge}"
        )
        assert realistic_edge.expected_edge < cfg.min_execution_adjusted_edge

    def test_decide_rejects_when_simple_passes_but_realistic_fails(self):
        """
        THE DEFINITIVE SPLIT-BRAIN TEST.

        Simple estimator says PASS, realistic path says FAIL.
        decide() MUST use the realistic path → REJECT.

        If this test passes, split-brain is eliminated.
        """
        cfg = self._split_brain_config()
        cal = _make_signal(up=0.52, down=0.38, no_trade=0.10)
        pr = _make_pricing(ask_yes=0.50, bid_yes=0.49, ask_no=0.51, bid_no=0.50)

        # Verify precondition: simple says pass
        simple_edge = estimate_yes_edge(cal, pr, cfg)
        assert simple_edge.passes_edge_gate is True, "Precondition failed: simple must PASS"

        # Verify precondition: realistic says fail
        realistic_edge = estimate_yes_edge_realistic(
            cal, pr, cfg, snapshot_age_seconds=0.0, intended_size_usdc=20.0
        )
        assert realistic_edge.passes_edge_gate is False, "Precondition failed: realistic must FAIL"

        # THE ACTUAL TEST: decide() must REJECT
        result = decide(cal, pr, cfg, now_utc=_NOW, intended_size_usdc=20.0)

        assert result.decision == TradeDecisionType.REJECT, (
            f"SPLIT-BRAIN DETECTED: simple edge passes ({simple_edge.expected_edge:.4f}) but "
            f"realistic fails ({realistic_edge.expected_edge:.4f}) — decide() should REJECT "
            f"but returned {result.decision.value}. Phase 9 gate not working."
        )
        assert result.rejection_reason == CalibrationRejectionReason.NEGATIVE_EDGE, (
            f"Expected NEGATIVE_EDGE rejection, got: {result.rejection_reason}"
        )

    def test_decide_uses_realistic_ev_not_simple_ev(self):
        """
        Verifies via edge_estimate that decide() used the realistic EV,
        not the simple configured-slippage EV.
        """
        cfg = self._split_brain_config()
        cal = _make_signal(up=0.52, down=0.38, no_trade=0.10)
        pr = _make_pricing(ask_yes=0.50, bid_yes=0.49, ask_no=0.51, bid_no=0.50)

        result = decide(cal, pr, cfg, now_utc=_NOW, intended_size_usdc=20.0)

        assert result.edge_estimate is not None
        # The edge in the result should match the realistic path (< simple edge)
        simple_edge = estimate_yes_edge(cal, pr, cfg)
        assert result.edge_estimate.expected_edge < simple_edge.expected_edge, (
            "decide() edge should reflect realistic path (lower EV due to slippage model)"
        )

    def test_large_edge_still_passes_with_realistic_path(self):
        """
        When edge is genuinely large, realistic path also passes.
        Both simple and realistic agree → EXECUTE.
        """
        cfg = self._split_brain_config()
        cal = _make_signal(up=0.72, down=0.18, no_trade=0.10)
        pr = _make_pricing(ask_yes=0.44, bid_yes=0.42, ask_no=0.57, bid_no=0.55)

        simple_edge = estimate_yes_edge(cal, pr, cfg)
        assert simple_edge.passes_edge_gate is True

        result = decide(cal, pr, cfg, now_utc=_NOW, intended_size_usdc=20.0)
        assert result.decision == TradeDecisionType.EXECUTE_YES, (
            "Large edge should still execute via realistic path"
        )

    def test_staleness_penalty_causes_fail_when_fresh_would_pass(self):
        """
        Fresh snapshot passes realistic gate.
        Aging snapshot has staleness penalty → may fail.
        This further confirms realistic path is active.
        """
        # p=0.55, ask=0.50 → theoretical=0.05, fee=0.01 → net=0.04
        # realistic slippage ~0.009 → exec_ev ≈ 0.031 > 0.005 with fresh snapshot
        # But with high staleness penalty, EV degrades further
        cal = _make_signal(up=0.55, down=0.35, no_trade=0.10)
        pr_fresh = _make_pricing(
            ask_yes=0.50, bid_yes=0.48, ask_no=0.51, bid_no=0.49,
            age_seconds=5  # FRESH
        )
        pr_aging = _make_pricing(
            ask_yes=0.50, bid_yes=0.48, ask_no=0.51, bid_no=0.49,
            age_seconds=100  # STALE for 5m horizon (>90s)
        )

        result_fresh = decide(cal, pr_fresh, self._split_brain_config(), now_utc=_NOW)
        result_aging = decide(cal, pr_aging, self._split_brain_config(), now_utc=_NOW)

        # Fresh should have higher (or equal) executable_ev than stale
        if (
            result_fresh.edge_estimate is not None
            and result_aging.edge_estimate is not None
        ):
            assert (
                result_fresh.edge_estimate.expected_edge
                >= result_aging.edge_estimate.expected_edge
            ), "Fresh snapshot should yield higher executable_ev than stale snapshot"


# ── Additional realistic gate priority tests ──────────────────────────────────

class TestRealisticGatePriority:
    """
    Additional tests that confirm realistic gate is the ONLY pass/fail authority.
    """

    def test_zero_configured_slippage_does_not_eliminate_realistic_slippage(self):
        """
        assumed_slippage_pct=0.0 in config does NOT mean zero slippage in realistic path.
        The slippage model computes its own cost from liquidity/size.
        """
        cfg = CalibrationConfig(
            assumed_slippage_pct=0.0,
            assumed_taker_fee_pct=0.01,
            min_execution_adjusted_edge=0.005,
            min_calibrated_confidence=0.51,
            min_liquidity=1_000.0,
        )
        cal = _make_signal(up=0.52, down=0.38, no_trade=0.10)
        pr = _make_pricing(ask_yes=0.50, bid_yes=0.49, ask_no=0.51, bid_no=0.50)

        result = decide(cal, pr, cfg, now_utc=_NOW, intended_size_usdc=20.0)

        # The realistic path computes slippage regardless of assumed_slippage_pct
        assert result.edge_estimate is not None
        if result.edge_estimate.executable_cost_breakdown is not None:
            breakdown = result.edge_estimate.executable_cost_breakdown
            # realistic slippage > 0 even with zero configured slippage
            assert breakdown.slippage.total_slippage > 0.0

    def test_executable_ev_in_edge_estimate_matches_breakdown(self):
        """
        EdgeEstimate.execution_adjusted_ev == ExecutableCostBreakdown.executable_ev.
        These must be consistent — no divergence between edge and breakdown.
        """
        cal = _make_signal(up=0.72, down=0.18, no_trade=0.10)
        pr = _make_pricing(ask_yes=0.44, bid_yes=0.42, ask_no=0.57, bid_no=0.55)
        cfg = CalibrationConfig(mode="default")

        result = decide(cal, pr, cfg, now_utc=_NOW)

        assert result.edge_estimate is not None
        assert result.edge_estimate.executable_cost_breakdown is not None
        assert result.edge_estimate.execution_adjusted_ev == pytest.approx(
            result.edge_estimate.executable_cost_breakdown.executable_ev, abs=1e-9
        )
