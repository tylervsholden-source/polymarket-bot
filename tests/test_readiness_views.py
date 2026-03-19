"""
tests/test_readiness_views.py

Tests for operator_layer/readiness_view.py.

Covers:
- ReadinessState when no verdict file exists → NOT_REVIEWED
- ReadinessState when verdict = NO_GO → correct fields
- ReadinessState when verdict = TINY_PILOT_CANDIDATE → correct fields
- Evidence gaps displayed correctly
- Profile summaries computed from records
- Pilot constraints always present
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from operator_layer.readiness_view import build_readiness_state
from operator_layer.types import ReadinessState


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _verdict(
    verdict: str = "NO_GO",
    evidence_sufficient: bool = True,
    reason: str = "Test reason",
    blockers: list = None,
    fails: list = None,
    warns: list = None,
    checks: list = None,
    ts: str = "2026-03-15T06:00:00Z",
    ev_result: dict = None,
) -> dict:
    return {
        "verdict": verdict,
        "evidence_sufficient": evidence_sufficient,
        "verdict_reason": reason,
        "blockers": blockers or [],
        "fails": fails or [],
        "warns": warns or [],
        "checks": checks or [],
        "generated_utc": ts,
        "evidence_result": ev_result or {
            "live_like_evaluated": 100,
            "live_like_executes": 30,
            "live_like_rejects": 70,
            "observation_days": 6.5,
            "gaps": [],
        },
        "pilot_constraints": {
            "max_open_positions": 1,
            "pilot_asset": "BTC",
            "pilot_horizon_minutes": 15,
            "max_nominal_usdc": 10.0,
            "daily_max_loss_usdc": 5.0,
            "single_loss_kill_usdc": 3.0,
        },
    }


def _check(name: str, level: str = "OK", val: float = None, msg: str = "ok") -> dict:
    return {"name": name, "level": level, "metric_value": val, "message": msg}


def _make_record(profile: str, decision: str, ev: float = None) -> dict:
    ts = "2026-03-15T10:00:00Z"
    return {
        "record_id": str(uuid.uuid4()),
        "run_id": "run-1",
        "ts_recorded_utc": ts,
        "policy_profile": profile,
        "evidence_source": "live_shadow",
        "signal": {"asset": "BTC", "horizon_minutes": 15},
        "decision": {
            "decision": decision,
            "execution_adjusted_ev": ev,
        },
    }


# ── NOT_REVIEWED state ─────────────────────────────────────────────────────────

class TestNotReviewedState:
    def test_not_reviewed_returned_when_no_file(self):
        state = build_readiness_state({"verdict": "NOT_REVIEWED", "generated_utc": None}, [])
        assert isinstance(state, ReadinessState)
        assert state.status == "NOT_REVIEWED"
        assert state.evidence_sufficient is False
        assert state.blocker_count == 0
        assert state.fail_count == 0

    def test_pilot_constraints_always_present(self):
        state = build_readiness_state({"verdict": "NOT_REVIEWED"}, [])
        assert state.pilot_asset == "BTC"
        assert state.pilot_max_positions == 1
        assert state.pilot_max_usdc == 10.0

    def test_profile_summaries_from_records_when_not_reviewed(self):
        recs = [_make_record("live", "REJECT") for _ in range(5)]
        state = build_readiness_state({"verdict": "NOT_REVIEWED"}, recs)
        assert state.live_summary is not None
        assert state.live_summary.total_evaluated == 5


# ── NO_GO state ────────────────────────────────────────────────────────────────

class TestNoGoState:
    def test_no_go_verdict_set(self):
        blocker = _check("execution_rate", "BLOCKER", 0.01, "execution rate too low")
        state = build_readiness_state(_verdict("NO_GO", blockers=[blocker]), [])
        assert state.status == "NO_GO"
        assert state.blocker_count == 1
        assert state.blockers[0].name == "execution_rate"
        assert state.blockers[0].level == "BLOCKER"

    def test_fail_count_correct(self):
        fail = _check("ev_haircut", "FAIL", 0.55, "haircut too high")
        state = build_readiness_state(_verdict("NO_GO", fails=[fail]), [])
        assert state.fail_count == 1
        assert state.fails[0].level == "FAIL"

    def test_evidence_sufficient_false_for_insufficient(self):
        ev = {
            "live_like_evaluated": 10,
            "live_like_executes": 2,
            "live_like_rejects": 8,
            "observation_days": 0.5,
            "gaps": [
                {"message": "Need 100 evaluated decisions; have 10."},
            ],
        }
        state = build_readiness_state(
            _verdict("NO_GO", evidence_sufficient=False, ev_result=ev),
            [],
        )
        assert state.evidence_sufficient is False
        assert len(state.evidence_gaps) > 0


# ── TINY_PILOT_CANDIDATE state ─────────────────────────────────────────────────

class TestTinyPilotCandidateState:
    def test_go_verdict(self):
        checks = [
            _check("execution_rate",   "OK",  0.28, "ok"),
            _check("ev_haircut_pct",   "OK",  0.12, "ok"),
            _check("stale_pricing",    "OK",  0.05, "ok"),
        ]
        state = build_readiness_state(
            _verdict("TINY_PILOT_CANDIDATE", checks=checks),
            [],
        )
        assert state.status == "TINY_PILOT_CANDIDATE"
        assert state.blocker_count == 0
        assert state.fail_count == 0
        assert len(state.checks) == 3

    def test_pilot_constraints_populated(self):
        state = build_readiness_state(_verdict("TINY_PILOT_CANDIDATE"), [])
        assert state.pilot_asset == "BTC"
        assert state.pilot_horizon_min == 15
        assert state.pilot_max_usdc == 10.0
        assert state.pilot_daily_loss_usdc == 5.0
        assert state.pilot_kill_usdc == 3.0


# ── CONDITIONAL_REVIEW ─────────────────────────────────────────────────────────

class TestConditionalReview:
    def test_warn_count_correct(self):
        warns = [
            _check("stale_pricing", "WARN", 0.18, "slightly high"),
            _check("suspicious_underround", "WARN", 0.22, "elevated"),
        ]
        state = build_readiness_state(
            _verdict("CONDITIONAL_REVIEW", warns=warns),
            [],
        )
        assert state.status == "CONDITIONAL_REVIEW"
        assert state.warn_count == 2


# ── Profile summaries ──────────────────────────────────────────────────────────

class TestProfileSummaries:
    def test_live_summary_computed(self):
        recs = (
            [_make_record("live", "EXECUTE_YES", ev=0.04) for _ in range(3)] +
            [_make_record("live", "REJECT") for _ in range(7)]
        )
        state = build_readiness_state(_verdict("NO_GO"), recs)
        s = state.live_summary
        assert s is not None
        assert s.total_evaluated == 10
        assert s.execute_count == 3
        assert s.reject_count == 7
        assert abs(s.execution_rate - 0.3) < 1e-9

    def test_mean_ev_computed(self):
        recs = [_make_record("live", "EXECUTE_YES", ev=0.04) for _ in range(5)]
        state = build_readiness_state(_verdict("NO_GO"), recs)
        assert state.live_summary is not None
        assert abs(state.live_summary.mean_ev - 0.04) < 1e-9

    def test_no_data_returns_none_summary(self):
        state = build_readiness_state(_verdict("NO_GO"), [])
        assert state.live_summary is None
        assert state.strict_summary is None
        assert state.loose_summary is None

    def test_synthetic_records_excluded(self):
        """Synthetic records must not contribute to profile summaries."""
        recs = [_make_record("live", "EXECUTE_YES") for _ in range(5)]
        # Mark them synthetic
        for r in recs:
            r["evidence_source"] = "synthetic"
        state = build_readiness_state(_verdict("NO_GO"), recs)
        assert state.live_summary is None


# ── Evidence fields ────────────────────────────────────────────────────────────

class TestEvidenceFields:
    def test_evidence_numbers_populated(self):
        ev = {
            "live_like_evaluated": 100,
            "live_like_executes": 35,
            "live_like_rejects": 65,
            "observation_days": 7.2,
            "gaps": [],
        }
        state = build_readiness_state(_verdict("TINY_PILOT_CANDIDATE", ev_result=ev), [])
        assert state.live_like_evaluated == 100
        assert state.live_like_executes == 35
        assert state.live_like_rejects == 65
        assert abs(state.observation_days - 7.2) < 1e-9
        assert state.evidence_gaps == []
