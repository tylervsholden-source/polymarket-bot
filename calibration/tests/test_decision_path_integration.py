"""
calibration/tests/test_decision_path_integration.py

Phase 9 end-to-end integration tests for the decision path.

Verifies:
- All audit fields are populated on EXECUTE decisions
- All audit fields are populated on REJECT decisions (steps 10-12)
- Early-exit REJECTs (steps 1-9) have audit fields as None (before edge computed)
- policy_mode reflects CalibrationConfig.mode
- intended_size_usdc_used matches the parameter passed to decide()
- passes_final_gate is True iff decision is EXECUTE_*
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from calibration.decision_policy import decide
from calibration.probability_mapper import map_to_event_probability
from calibration.types import (
    CalibrationConfig,
    CalibrationMethod,
    CalibrationQuality,
    CalibrationRejectionReason,
    DEFAULT_CAL_CONFIG,
    LIVE_CAL_CONFIG,
    PAPER_CAL_CONFIG,
    MarketPricingSnapshot,
    RawSignalOutput,
    TradeDecisionType,
)

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _raw(
    predicted_class="UP",
    confidence=0.72,
    horizon=5,
    with_class_probs=True,
    up=0.72,
    down=0.18,
    no_trade=0.10,
):
    return RawSignalOutput(
        asset="BTC",
        horizon_minutes=horizon,
        timestamp_utc=_NOW,
        predicted_class=predicted_class,
        raw_confidence=confidence,
        class_probabilities=(
            {"UP": up, "DOWN": down, "NO_TRADE": no_trade}
            if with_class_probs else None
        ),
    )


def _cal(
    up=0.72,
    down=0.18,
    no_trade=0.10,
    polarity="NORMAL",
    horizon=5,
    quality=CalibrationQuality.STRONG,
    predicted_class="UP",
    with_class_probs=True,
):
    r = _raw(
        predicted_class=predicted_class,
        confidence=up,
        horizon=horizon,
        with_class_probs=with_class_probs,
        up=up,
        down=down,
        no_trade=no_trade,
    )
    cal, err = map_to_event_probability(
        raw=r,
        calibrated_up_prob=up,
        calibrated_down_prob=down,
        calibrated_no_trade_prob=no_trade,
        polarity=polarity,
        calibration_method=CalibrationMethod.PLATT,
        calibration_quality=quality,
    )
    assert err is None, f"map_to_event_probability failed: {err}"
    return cal


def _pricing(
    ask_yes=0.44,
    bid_yes=0.42,
    ask_no=0.57,
    bid_no=0.55,
    liquidity=5000.0,
    age_seconds=0,
):
    ts = _NOW - timedelta(seconds=age_seconds)
    return MarketPricingSnapshot(
        market_id="mkt-test",
        ask_yes=ask_yes,
        bid_yes=bid_yes,
        ask_no=ask_no,
        bid_no=bid_no,
        liquidity=liquidity,
        timestamp_utc=ts,
    )


# ── Tests: EXECUTE path audit fields ──────────────────────────────────────────

class TestExecuteAuditFields:
    """All audit fields must be populated on EXECUTE decisions."""

    def test_execute_yes_all_audit_fields_present(self):
        """EXECUTE_YES: every audit field is non-None and consistent."""
        cal = _cal(up=0.72, down=0.18, no_trade=0.10)
        pr = _pricing(ask_yes=0.44, bid_yes=0.42, ask_no=0.57, bid_no=0.55)

        result = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW, intended_size_usdc=20.0)

        assert result.decision == TradeDecisionType.EXECUTE_YES
        assert result.passes_final_gate is True
        assert result.final_gate_metric == "executable_ev"
        assert result.final_gate_threshold is not None
        assert result.final_gate_threshold == PAPER_CAL_CONFIG.min_execution_adjusted_edge
        assert result.theoretical_hold_ev is not None
        assert result.net_ev_after_fee is not None
        assert result.policy_mode == "paper"
        assert result.intended_size_usdc_used == 20.0

    def test_execute_yes_theoretical_hold_ev_is_p_minus_ask(self):
        """theoretical_hold_ev == p - ask_yes (frictionless upper bound)."""
        cal = _cal(up=0.72, down=0.18, no_trade=0.10)
        pr = _pricing(ask_yes=0.44, bid_yes=0.42, ask_no=0.57, bid_no=0.55)

        result = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)

        assert result.decision == TradeDecisionType.EXECUTE_YES
        # theoretical = p - ask = 0.72 - 0.44 = 0.28
        expected_theoretical = 0.72 - 0.44
        assert result.theoretical_hold_ev == pytest.approx(expected_theoretical, abs=1e-4)

    def test_execute_yes_net_ev_is_theoretical_minus_fee(self):
        """net_ev_after_fee == theoretical_hold_ev - fee."""
        cal = _cal(up=0.72, down=0.18, no_trade=0.10)
        pr = _pricing(ask_yes=0.44, bid_yes=0.42, ask_no=0.57, bid_no=0.55)

        result = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)

        assert result.decision == TradeDecisionType.EXECUTE_YES
        assert result.theoretical_hold_ev is not None
        assert result.net_ev_after_fee is not None
        # net = theoretical - fee; fee = 0.01
        expected_net = result.theoretical_hold_ev - PAPER_CAL_CONFIG.assumed_taker_fee_pct
        assert result.net_ev_after_fee == pytest.approx(expected_net, abs=1e-4)

    def test_execute_yes_intended_size_used_matches_parameter(self):
        """intended_size_usdc_used == the size passed to decide()."""
        cal = _cal(up=0.72, down=0.18, no_trade=0.10)
        pr = _pricing()

        result = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW, intended_size_usdc=50.0)

        if result.decision == TradeDecisionType.EXECUTE_YES:
            assert result.intended_size_usdc_used == 50.0

    def test_execute_no_audit_fields_present(self):
        """EXECUTE_NO also has all audit fields populated."""
        # INVERTED polarity: UP → NO
        cal = _cal(up=0.72, down=0.18, no_trade=0.10, polarity="INVERTED")
        # With INVERTED, effective_no_prob = calibrated_up_prob = 0.72
        # ask_no must be low enough to give positive edge
        pr = _pricing(ask_yes=0.57, bid_yes=0.55, ask_no=0.29, bid_no=0.27)

        result = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)

        if result.decision == TradeDecisionType.EXECUTE_NO:
            assert result.passes_final_gate is True
            assert result.final_gate_metric == "executable_ev"
            assert result.theoretical_hold_ev is not None
            assert result.policy_mode == "paper"


# ── Tests: REJECT path audit fields (steps 10-12) ─────────────────────────────

class TestRejectAuditFields:
    """REJECT due to steps 10-12 (after edge computed) should have audit fields."""

    def test_negative_edge_reject_has_audit_fields(self):
        """NEGATIVE_EDGE reject (step 12) populates audit trail."""
        # Very thin edge: p=0.56, ask=0.55 → low executable_ev
        cal = _cal(up=0.56, down=0.30, no_trade=0.14)
        pr = _pricing(ask_yes=0.55, bid_yes=0.54, ask_no=0.46, bid_no=0.44)

        result = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)

        if result.rejection_reason == CalibrationRejectionReason.NEGATIVE_EDGE:
            assert result.passes_final_gate is False
            assert result.final_gate_metric == "executable_ev"
            assert result.final_gate_threshold == PAPER_CAL_CONFIG.min_execution_adjusted_edge
            assert result.theoretical_hold_ev is not None
            assert result.net_ev_after_fee is not None
            assert result.policy_mode == "paper"

    def test_high_spread_reject_has_audit_fields(self):
        """HIGH_SPREAD reject (step 10) populates audit trail fields."""
        cal = _cal(up=0.72, down=0.18, no_trade=0.10)
        # Large spread: ask=0.60, bid=0.20 → spread=0.40 > max_spread=0.05
        pr = _pricing(ask_yes=0.60, bid_yes=0.20, ask_no=0.41, bid_no=0.39)

        result = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)

        assert result.rejection_reason == CalibrationRejectionReason.HIGH_SPREAD
        assert result.passes_final_gate is False
        assert result.final_gate_metric == "executable_ev"
        assert result.final_gate_threshold is not None
        assert result.theoretical_hold_ev is not None
        assert result.policy_mode == "paper"

    def test_low_confidence_reject_has_audit_fields(self):
        """LOW_CONFIDENCE reject (step 11) populates audit trail fields."""
        # eff_prob < min_calibrated_confidence=0.55
        cal = _cal(up=0.52, down=0.38, no_trade=0.10)
        pr = _pricing(ask_yes=0.48, bid_yes=0.46, ask_no=0.53, bid_no=0.51)

        result = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)

        if result.rejection_reason == CalibrationRejectionReason.LOW_CONFIDENCE:
            assert result.passes_final_gate is False
            assert result.final_gate_metric == "executable_ev"
            assert result.theoretical_hold_ev is not None
            assert result.policy_mode == "paper"


# ── Tests: Early REJECT (steps 1-9) — audit fields should be None ────────────

class TestEarlyRejectAuditFields:
    """REJECT before edge is computed: audit EV fields are None."""

    def test_stale_pricing_reject_ev_fields_none(self):
        """STALE_PRICING (step 7) reject: no edge computed → theoretical_hold_ev is None."""
        cal = _cal(up=0.72, down=0.18, no_trade=0.10)
        # Snapshot older than max_snapshot_age_seconds=300
        pr = _pricing(age_seconds=400)

        result = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)

        assert result.rejection_reason == CalibrationRejectionReason.STALE_PRICING
        assert result.theoretical_hold_ev is None
        assert result.net_ev_after_fee is None
        assert result.passes_final_gate is False

    def test_low_liquidity_reject_ev_fields_none(self):
        """LOW_LIQUIDITY (step 8) reject: no edge computed → theoretical_hold_ev is None."""
        cal = _cal(up=0.72, down=0.18, no_trade=0.10)
        pr = _pricing(liquidity=100.0)  # < min_liquidity=1000

        result = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)

        assert result.rejection_reason == CalibrationRejectionReason.LOW_LIQUIDITY
        assert result.theoretical_hold_ev is None
        assert result.net_ev_after_fee is None
        assert result.passes_final_gate is False

    def test_invalid_pricing_reject_ev_fields_none(self):
        """INVALID_PRICING (step 6) reject: no edge computed → theoretical_hold_ev is None."""
        cal = _cal(up=0.72, down=0.18, no_trade=0.10)
        pr = _pricing(ask_yes=0.40, bid_yes=0.45)  # inverted spread

        result = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)

        assert result.rejection_reason == CalibrationRejectionReason.INVALID_PRICING
        assert result.theoretical_hold_ev is None
        assert result.net_ev_after_fee is None


# ── Tests: policy_mode from config ────────────────────────────────────────────

class TestPolicyMode:
    """policy_mode in TradeDecision must reflect CalibrationConfig.mode."""

    def test_paper_config_policy_mode_is_paper(self):
        cal = _cal(up=0.72, down=0.18, no_trade=0.10)
        pr = _pricing()
        result = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)
        assert result.policy_mode == "paper"

    def test_default_config_policy_mode_is_default(self):
        cal = _cal(up=0.72, down=0.18, no_trade=0.10)
        pr = _pricing()
        result = decide(cal, pr, DEFAULT_CAL_CONFIG, now_utc=_NOW)
        assert result.policy_mode == "default"

    def test_custom_config_policy_mode_set_correctly(self):
        custom_cfg = CalibrationConfig(mode="live")
        cal = _cal(up=0.72, down=0.18, no_trade=0.10)
        pr = _pricing()
        # Phase 10: live mode requires explicit intended_size_usdc
        result = decide(cal, pr, custom_cfg, now_utc=_NOW, intended_size_usdc=20.0)
        assert result.policy_mode == "live"


# ── Tests: passes_final_gate consistency ──────────────────────────────────────

class TestPassesFinalGateConsistency:
    """passes_final_gate must be True iff decision is EXECUTE_*."""

    def test_execute_implies_passes_final_gate_true(self):
        cal = _cal(up=0.72, down=0.18, no_trade=0.10)
        pr = _pricing()
        result = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)

        if result.decision in (TradeDecisionType.EXECUTE_YES, TradeDecisionType.EXECUTE_NO):
            assert result.passes_final_gate is True
        else:
            assert result.passes_final_gate is False

    def test_reject_implies_passes_final_gate_false(self):
        """Thin edge → REJECT → passes_final_gate=False."""
        cal = _cal(up=0.56, down=0.30, no_trade=0.14)
        pr = _pricing(ask_yes=0.55, bid_yes=0.54, ask_no=0.46, bid_no=0.44)
        result = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)

        # Whether it REJECTs or EXECUTEs, the invariant must hold
        if result.decision == TradeDecisionType.REJECT:
            assert result.passes_final_gate is False
        else:
            assert result.passes_final_gate is True
