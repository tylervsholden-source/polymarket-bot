"""
tests/test_dashboard_api.py

Tests for operator_layer/api.py.

Verifies that each handler returns valid JSON bytes with expected top-level keys.
Uses monkeypatching to inject predictable aggregator output without real file I/O.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from operator_layer import api
from operator_layer.types import (
    ChamberSummary,
    ClosedTrade,
    DecisionChain,
    DecisionEvent,
    EquityState,
    HealthAlert,
    HealthState,
    OpenPosition,
    ProfileComparisonRow,
    ProfileComparisonSummary,
    ReadinessState,
)


# ── Minimal fixture factories ──────────────────────────────────────────────────

def _now():
    return datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


def _equity():
    return EquityState(
        timestamp_utc=_now(),
        cash_available=90.0,
        capital_committed=10.0,
        unrealized_pnl=1.5,
        realized_pnl_day=0.5,
        realized_pnl_total=2.0,
        total_equity=101.5,
        active_positions_count=1,
        initial_capital=100.0,
        blocked_reason=None,
    )


def _health():
    return HealthState(
        journal_healthy=True,
        journal_bad_line_fraction=0.0,
        journal_total_lines=50,
        drift_alerts=[],
        pricing_sanity_alerts=[],
        suspicious_underround_rate=0.05,
        stale_pricing_rate=0.03,
        partial_fill_rate=0.0,
        policy_divergence_alerts=[],
        cycle_running=True,
        last_cycle_at="2026-03-15T12:00:00Z",
        cycles_completed=42,
        blocker_active=False,
        blocker_reasons=[],
        last_updated_utc=_now(),
    )


def _readiness():
    return ReadinessState(
        status="INSUFFICIENT_EVIDENCE",
        verdict_reason="Not enough data yet.",
        evidence_sufficient=False,
        last_reviewed_at=None,
        blocker_count=0, fail_count=0, warn_count=0,
        checks=[], blockers=[], fails=[], warns=[],
        live_like_evaluated=5, live_like_executes=1, live_like_rejects=4,
        observation_days=0.1,
        evidence_gaps=["Need more data."],
        live_summary=None, strict_summary=None, loose_summary=None,
        pilot_max_positions=1, pilot_asset="BTC", pilot_horizon_min=15,
        pilot_max_usdc=10.0, pilot_daily_loss_usdc=5.0, pilot_kill_usdc=3.0,
    )


def _comparison():
    return ProfileComparisonSummary(
        profiles_found=["live", "paper_loose"],
        per_profile={
            "live": {"total": 5, "execute": 1, "reject": 4, "exec_rate": 0.2},
            "paper_loose": {"total": 5, "execute": 3, "reject": 2, "exec_rate": 0.6},
        },
        divergent_rows=[],
        loose_only_count=2,
        strict_not_live_count=0,
        total_records=10,
    )


def _summary():
    return ChamberSummary(
        generated_utc=_now(),
        equity=_equity(),
        health=_health(),
        readiness=_readiness(),
        recent_decisions=[],
        open_positions=[],
        closed_trades=[],
        profile_comparison=_comparison(),
        environment="simulation",
        active_profile_mode="paper_loose",
        journal_files_found=1,
        total_shadow_records=10,
    )


# ── Handler tests ──────────────────────────────────────────────────────────────

class TestHandleSummary:
    def test_returns_valid_json(self):
        with patch("operator_layer.api.build_chamber_summary", return_value=_summary()):
            data = api.handle_summary()
        obj = json.loads(data)
        assert "equity" in obj
        assert "health" in obj
        assert "readiness" in obj
        assert "recent_decisions" in obj
        assert "profile_comparison" in obj
        assert "generated_utc" in obj

    def test_equity_fields_present(self):
        with patch("operator_layer.api.build_chamber_summary", return_value=_summary()):
            obj = json.loads(api.handle_summary())
        eq = obj["equity"]
        assert abs(eq["total_equity"] - 101.5) < 1e-9
        assert abs(eq["cash_available"] - 90.0) < 1e-9
        assert eq["active_positions_count"] == 1

    def test_environment_field(self):
        with patch("operator_layer.api.build_chamber_summary", return_value=_summary()):
            obj = json.loads(api.handle_summary())
        assert obj["environment"] == "simulation"


class TestHandleDecisions:
    def test_decisions_key_present(self):
        with patch("operator_layer.api.build_chamber_summary", return_value=_summary()):
            obj = json.loads(api.handle_decisions())
        assert "decisions" in obj
        assert "total_records" in obj

    def test_total_records_correct(self):
        with patch("operator_layer.api.build_chamber_summary", return_value=_summary()):
            obj = json.loads(api.handle_decisions())
        assert obj["total_records"] == 10


class TestHandlePositions:
    def test_positions_key_present(self):
        with patch("operator_layer.api.build_chamber_summary", return_value=_summary()):
            obj = json.loads(api.handle_positions())
        assert "positions" in obj
        assert "equity" in obj

    def test_empty_positions(self):
        with patch("operator_layer.api.build_chamber_summary", return_value=_summary()):
            obj = json.loads(api.handle_positions())
        assert obj["positions"] == []


class TestHandleTrades:
    def test_trades_key_present(self):
        with patch("operator_layer.api.build_chamber_summary", return_value=_summary()):
            obj = json.loads(api.handle_trades())
        assert "trades" in obj
        assert "equity" in obj


class TestHandleHealth:
    def test_health_key_present(self):
        with patch("operator_layer.api.build_chamber_summary", return_value=_summary()):
            obj = json.loads(api.handle_health())
        assert "health" in obj
        h = obj["health"]
        assert h["journal_healthy"] is True
        assert h["cycle_running"] is True
        assert h["cycles_completed"] == 42


class TestHandleReadiness:
    def test_readiness_key_present(self):
        with patch("operator_layer.api.build_chamber_summary", return_value=_summary()):
            obj = json.loads(api.handle_readiness())
        assert "readiness" in obj
        rd = obj["readiness"]
        assert rd["status"] == "INSUFFICIENT_EVIDENCE"
        assert rd["evidence_sufficient"] is False


class TestHandleProfileComparison:
    def test_comparison_key_present(self):
        with patch("operator_layer.api.build_chamber_summary", return_value=_summary()):
            obj = json.loads(api.handle_profile_comparison())
        assert "comparison" in obj
        cmp = obj["comparison"]
        assert cmp["loose_only_count"] == 2
        assert "live" in cmp["profiles_found"]


class TestHandleEquity:
    def test_equity_key_present(self):
        with patch("operator_layer.api.build_chamber_summary", return_value=_summary()):
            obj = json.loads(api.handle_equity())
        assert "equity" in obj


class TestHandleDecisionChain:
    def test_chain_returned_when_found(self):
        chain = DecisionChain(
            record_id="rec-1",
            timestamp_utc=_now(),
            asset="BTC",
            market_id="mkt-1",
            bridge_step={"intent_side": "YES", "effective_yes_prob": 0.68},
            calibration_step={"quality": "good"},
            pricing_step={"ask_yes": 0.55},
            ev_step={"execution_adjusted_ev": 0.04},
            policy_step={"final_decision": "EXECUTE_YES", "rejection_reason": None},
            replay_available=True,
        )
        with patch("operator_layer.api.get_decision_chain", return_value=chain):
            obj = json.loads(api.handle_decision_chain("rec-1"))
        assert obj["record_id"] == "rec-1"
        assert obj["asset"] == "BTC"
        assert obj["replay_available"] is True

    def test_error_returned_when_not_found(self):
        with patch("operator_layer.api.get_decision_chain", return_value=None):
            obj = json.loads(api.handle_decision_chain("missing"))
        assert "error" in obj


class TestSerializationEdgeCases:
    def test_datetime_serialized_as_string(self):
        with patch("operator_layer.api.build_chamber_summary", return_value=_summary()):
            obj = json.loads(api.handle_summary())
        assert isinstance(obj["generated_utc"], str)
        assert "2026" in obj["generated_utc"]

    def test_none_fields_serialized_as_null(self):
        with patch("operator_layer.api.build_chamber_summary", return_value=_summary()):
            obj = json.loads(api.handle_readiness())
        rd = obj["readiness"]
        assert rd["last_reviewed_at"] is None
