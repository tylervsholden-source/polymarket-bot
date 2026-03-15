"""
calibration/tests/test_audit_breakdown.py

Tests that verify the Phase 9 audit fields exist and are correctly populated
in TradeDecision as documented in DECISION_FLOW.md output contract.

Checks:
- All audit fields exist as attributes on TradeDecision
- Field types are correct
- Values are consistent with edge_estimate and config
- Fields survive round-trip (no field is silently dropped)
"""
from __future__ import annotations

from dataclasses import fields as dataclass_fields
from datetime import datetime, timedelta, timezone
from typing import get_type_hints

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
    TradeDecision,
    TradeDecisionType,
)

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_signal(up=0.72, down=0.18, no_trade=0.10, horizon=5,
                 quality=CalibrationQuality.STRONG):
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
        calibration_quality=quality,
    )
    assert err is None
    return cal


def _make_pricing(ask_yes=0.44, bid_yes=0.42, ask_no=0.57, bid_no=0.55,
                  liquidity=5000.0, age_seconds=0):
    ts = _NOW - timedelta(seconds=age_seconds)
    return MarketPricingSnapshot(
        market_id="mkt-audit",
        ask_yes=ask_yes,
        bid_yes=bid_yes,
        ask_no=ask_no,
        bid_no=bid_no,
        liquidity=liquidity,
        timestamp_utc=ts,
    )


# ── DECISION_FLOW.md output contract compliance ───────────────────────────────

class TestOutputContractFields:
    """
    Verify every field listed in DECISION_FLOW.md output contract
    exists on TradeDecision as a dataclass field.
    """

    REQUIRED_AUDIT_FIELDS = [
        "theoretical_hold_ev",
        "net_ev_after_fee",
        "final_gate_metric",
        "final_gate_threshold",
        "passes_final_gate",
        "policy_mode",
        "intended_size_usdc_used",
    ]

    REQUIRED_CORE_FIELDS = [
        "decision",
        "rationale",
        "rejection_reason",
        "edge_estimate",
        "executable_cost_breakdown",
        "calibrated_signal",
    ]

    def _all_field_names(self):
        return {f.name for f in dataclass_fields(TradeDecision)}

    def test_all_audit_fields_exist_on_trade_decision(self):
        """Every Phase 9 audit field must exist as a dataclass field."""
        field_names = self._all_field_names()
        for field_name in self.REQUIRED_AUDIT_FIELDS:
            assert field_name in field_names, (
                f"Missing audit field on TradeDecision: '{field_name}'"
            )

    def test_all_core_fields_exist_on_trade_decision(self):
        """All core fields from the output contract must exist."""
        field_names = self._all_field_names()
        for field_name in self.REQUIRED_CORE_FIELDS:
            assert field_name in field_names, (
                f"Missing core field on TradeDecision: '{field_name}'"
            )

    def test_final_gate_metric_default_is_executable_ev(self):
        """final_gate_metric defaults to 'executable_ev'."""
        # Access field default
        for f in dataclass_fields(TradeDecision):
            if f.name == "final_gate_metric":
                assert f.default == "executable_ev", (
                    f"final_gate_metric default should be 'executable_ev', "
                    f"got {f.default!r}"
                )
                break

    def test_passes_final_gate_default_is_false(self):
        """passes_final_gate defaults to False (conservative)."""
        for f in dataclass_fields(TradeDecision):
            if f.name == "passes_final_gate":
                assert f.default is False, (
                    f"passes_final_gate default should be False, got {f.default!r}"
                )
                break

    def test_policy_mode_default_is_unknown(self):
        """policy_mode defaults to 'unknown' when not set."""
        for f in dataclass_fields(TradeDecision):
            if f.name == "policy_mode":
                assert f.default == "unknown", (
                    f"policy_mode default should be 'unknown', got {f.default!r}"
                )
                break

    def test_intended_size_usdc_used_default_is_20(self):
        """intended_size_usdc_used defaults to 20.0."""
        for f in dataclass_fields(TradeDecision):
            if f.name == "intended_size_usdc_used":
                assert f.default == 20.0, (
                    f"intended_size_usdc_used default should be 20.0, got {f.default!r}"
                )
                break


# ── CalibrationConfig.mode field ──────────────────────────────────────────────

class TestCalibrationConfigMode:
    """CalibrationConfig must have a mode field."""

    def test_calibration_config_has_mode_field(self):
        from calibration.types import CalibrationConfig
        field_names = {f.name for f in dataclass_fields(CalibrationConfig)}
        assert "mode" in field_names, "CalibrationConfig missing 'mode' field"

    def test_default_cal_config_mode_is_default(self):
        assert DEFAULT_CAL_CONFIG.mode == "default"

    def test_paper_cal_config_mode_is_paper(self):
        assert PAPER_CAL_CONFIG.mode == "paper"

    def test_live_cal_config_mode_is_live(self):
        assert LIVE_CAL_CONFIG.mode == "live"


# ── Audit fields populated correctly on EXECUTE ───────────────────────────────

class TestAuditFieldValuesOnExecute:
    """Verify audit field values are correct on EXECUTE decisions."""

    def _execute_result(self):
        cal = _make_signal(up=0.72, down=0.18, no_trade=0.10)
        pr = _make_pricing(ask_yes=0.44, bid_yes=0.42, ask_no=0.57, bid_no=0.55)
        return decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW, intended_size_usdc=20.0)

    def test_execute_passes_final_gate_true(self):
        result = self._execute_result()
        assert result.decision == TradeDecisionType.EXECUTE_YES
        assert result.passes_final_gate is True

    def test_execute_final_gate_metric_is_executable_ev(self):
        result = self._execute_result()
        assert result.final_gate_metric == "executable_ev"

    def test_execute_final_gate_threshold_matches_config(self):
        result = self._execute_result()
        assert result.final_gate_threshold == PAPER_CAL_CONFIG.min_execution_adjusted_edge

    def test_execute_theoretical_hold_ev_is_positive(self):
        """For a passing trade, theoretical_hold_ev must be > 0."""
        result = self._execute_result()
        assert result.theoretical_hold_ev is not None
        assert result.theoretical_hold_ev > 0.0

    def test_execute_net_ev_less_than_theoretical(self):
        """net_ev_after_fee < theoretical_hold_ev (fee removes value)."""
        result = self._execute_result()
        assert result.theoretical_hold_ev is not None
        assert result.net_ev_after_fee is not None
        assert result.net_ev_after_fee < result.theoretical_hold_ev

    def test_execute_policy_mode_is_paper(self):
        result = self._execute_result()
        assert result.policy_mode == "paper"

    def test_execute_intended_size_used_is_20(self):
        result = self._execute_result()
        assert result.intended_size_usdc_used == 20.0

    def test_execute_edge_estimate_and_breakdown_non_none(self):
        result = self._execute_result()
        assert result.edge_estimate is not None
        assert result.executable_cost_breakdown is not None
        assert result.calibrated_signal is not None


# ── Audit fields on REJECT ────────────────────────────────────────────────────

class TestAuditFieldValuesOnReject:
    """Verify audit fields are correctly set on various REJECT paths."""

    def test_negative_edge_reject_passes_final_gate_false(self):
        """NEGATIVE_EDGE reject: passes_final_gate=False."""
        cal = _make_signal(up=0.56, down=0.30, no_trade=0.14)
        pr = _make_pricing(ask_yes=0.55, bid_yes=0.54, ask_no=0.46, bid_no=0.44)

        result = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)

        if result.rejection_reason == CalibrationRejectionReason.NEGATIVE_EDGE:
            assert result.passes_final_gate is False
            assert result.final_gate_metric == "executable_ev"
            assert result.final_gate_threshold == PAPER_CAL_CONFIG.min_execution_adjusted_edge

    def test_high_spread_reject_passes_final_gate_false(self):
        """HIGH_SPREAD reject: passes_final_gate=False, audit fields populated."""
        cal = _make_signal(up=0.72, down=0.18, no_trade=0.10)
        pr = _make_pricing(ask_yes=0.60, bid_yes=0.20, ask_no=0.41, bid_no=0.39)

        result = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)

        assert result.rejection_reason == CalibrationRejectionReason.HIGH_SPREAD
        assert result.passes_final_gate is False
        assert result.final_gate_metric == "executable_ev"
        assert result.theoretical_hold_ev is not None  # edge was computed
        assert result.policy_mode == "paper"
        assert result.intended_size_usdc_used == 20.0

    def test_stale_pricing_reject_ev_fields_none(self):
        """STALE_PRICING reject (before edge): EV fields are None."""
        cal = _make_signal()
        pr = _make_pricing(age_seconds=400)  # > max_snapshot_age_seconds=300

        result = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)

        assert result.rejection_reason == CalibrationRejectionReason.STALE_PRICING
        assert result.passes_final_gate is False
        assert result.theoretical_hold_ev is None
        assert result.net_ev_after_fee is None

    def test_low_liquidity_reject_ev_fields_none(self):
        """LOW_LIQUIDITY reject (before edge): EV fields are None."""
        cal = _make_signal()
        pr = _make_pricing(liquidity=100.0)  # < min_liquidity

        result = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)

        assert result.rejection_reason == CalibrationRejectionReason.LOW_LIQUIDITY
        assert result.passes_final_gate is False
        assert result.theoretical_hold_ev is None

    def test_all_rejects_have_final_gate_metric(self):
        """final_gate_metric='executable_ev' on all REJECT paths."""
        test_cases = [
            # (up, down, no_trade, ask_yes, bid_yes, ask_no, bid_no, liq, age)
            (0.72, 0.18, 0.10, 0.44, 0.42, 0.57, 0.55, 100.0, 0),    # low liq
            (0.72, 0.18, 0.10, 0.44, 0.42, 0.57, 0.55, 5000.0, 400),  # stale
            (0.72, 0.18, 0.10, 0.60, 0.20, 0.41, 0.39, 5000.0, 0),    # high spread
        ]
        for (up, down, nt, ay, by, ano, bno, liq, age) in test_cases:
            cal = _make_signal(up=up, down=down, no_trade=nt)
            pr = _make_pricing(ask_yes=ay, bid_yes=by, ask_no=ano, bid_no=bno,
                               liquidity=liq, age_seconds=age)
            result = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)

            assert result.decision == TradeDecisionType.REJECT
            assert result.final_gate_metric == "executable_ev", (
                f"final_gate_metric should always be 'executable_ev', "
                f"got {result.final_gate_metric!r} for rejection={result.rejection_reason}"
            )


# ── Breakdown consistency ─────────────────────────────────────────────────────

class TestBreakdownConsistency:
    """
    Verify TradeDecision.executable_cost_breakdown and edge_estimate.executable_cost_breakdown
    point to the same object on EXECUTE decisions.
    """

    def test_breakdown_consistent_between_decision_and_edge(self):
        """
        TradeDecision.executable_cost_breakdown is the same object as
        edge_estimate.executable_cost_breakdown.
        """
        cal = _make_signal()
        pr = _make_pricing()

        result = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)

        if result.decision in (TradeDecisionType.EXECUTE_YES, TradeDecisionType.EXECUTE_NO):
            assert result.executable_cost_breakdown is not None
            assert result.edge_estimate is not None
            assert result.edge_estimate.executable_cost_breakdown is not None
            # Should be the same object
            assert result.executable_cost_breakdown is result.edge_estimate.executable_cost_breakdown

    def test_theoretical_hold_ev_matches_breakdown_field(self):
        """
        TradeDecision.theoretical_hold_ev should match the breakdown's
        theoretical_hold_ev field.
        """
        cal = _make_signal()
        pr = _make_pricing()

        result = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)

        if (
            result.decision == TradeDecisionType.EXECUTE_YES
            and result.executable_cost_breakdown is not None
        ):
            assert result.theoretical_hold_ev == pytest.approx(
                result.executable_cost_breakdown.theoretical_hold_ev, abs=1e-9
            )

    def test_net_ev_after_fee_matches_edge_estimate(self):
        """TradeDecision.net_ev_after_fee matches edge_estimate.net_expected_value."""
        cal = _make_signal()
        pr = _make_pricing()

        result = decide(cal, pr, PAPER_CAL_CONFIG, now_utc=_NOW)

        if result.decision == TradeDecisionType.EXECUTE_YES and result.edge_estimate is not None:
            assert result.net_ev_after_fee == pytest.approx(
                result.edge_estimate.net_expected_value, abs=1e-9
            )
