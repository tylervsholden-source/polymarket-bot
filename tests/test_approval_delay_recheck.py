"""Tests: approval delay can push a market out of its entry window.

Market used throughout:
    "Bitcoin Up or Down - March 16, 7:10PM-7:15PM ET"
    start UTC = 2026-03-16 23:10:00 UTC  (7:10PM ET = UTC-4)
    5m horizon  → policy: opens 600s before start, closes 600s after start
    window_opens  = 23:00:00 UTC
    window_closes = 23:20:00 UTC
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from control_plane.entry_window_guard import (
    check_entry_window,
    EntryWindowRejection,
    EntryWindowPolicy,
    EntryWindowConfig,
    DEFAULT_ENTRY_WINDOW_POLICY,
)
from control_plane.live_gate import check_live_gate


# ── Fixture / constants ───────────────────────────────────────────────────────

QUESTION = "Bitcoin Up or Down - March 16, 7:10PM-7:15PM ET"

# 2026-03-16 23:10:00 UTC  (start time)
START_UTC = datetime(2026, 3, 16, 23, 10, 0, tzinfo=timezone.utc)

# 5m policy: 600s before, 600s after
WINDOW_OPENS = START_UTC - timedelta(seconds=600)   # 23:00:00 UTC
WINDOW_CLOSES = START_UTC + timedelta(seconds=600)  # 23:20:00 UTC


@pytest.fixture
def ctrl_file(tmp_path):
    f = tmp_path / "control.json"
    f.write_text(json.dumps({"live_trading": True}))
    return str(f)


@pytest.fixture
def readiness_file(tmp_path):
    f = tmp_path / "readiness.json"
    f.write_text(json.dumps({
        "verdict": "TINY_PILOT_CANDIDATE",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }))
    return str(f)


# ── entry_window_guard: direct check_entry_window tests ──────────────────────

class TestNormalCheckTooLate:
    """Test 1: Normal check (no approval recheck) after window closes → TOO_LATE."""

    def test_rejection_enum(self):
        # 3 seconds past the window close
        now = WINDOW_CLOSES + timedelta(seconds=3)
        result = check_entry_window(QUESTION, now_utc=now)
        assert result.passed is False
        assert result.rejection == EntryWindowRejection.TOO_LATE_FOR_ENTRY_WINDOW

    def test_not_approval_delay(self):
        now = WINDOW_CLOSES + timedelta(seconds=3)
        result = check_entry_window(QUESTION, now_utc=now)
        assert result.rejection != EntryWindowRejection.APPROVAL_DELAY_PUSHED_OUT_OF_WINDOW

    def test_window_boundaries_in_result(self):
        now = WINDOW_CLOSES + timedelta(seconds=3)
        result = check_entry_window(QUESTION, now_utc=now)
        assert result.window_opens_at == WINDOW_OPENS
        assert result.window_closes_at == WINDOW_CLOSES


class TestRecheckAfterApprovalTooLate:
    """Test 2: Recheck after approval delay, time is past window → APPROVAL_DELAY."""

    def test_rejection_enum(self):
        now = WINDOW_CLOSES + timedelta(seconds=3)
        result = check_entry_window(QUESTION, now_utc=now, is_recheck_after_approval=True)
        assert result.passed is False
        assert result.rejection == EntryWindowRejection.APPROVAL_DELAY_PUSHED_OUT_OF_WINDOW

    def test_not_too_late(self):
        now = WINDOW_CLOSES + timedelta(seconds=3)
        result = check_entry_window(QUESTION, now_utc=now, is_recheck_after_approval=True)
        assert result.rejection != EntryWindowRejection.TOO_LATE_FOR_ENTRY_WINDOW

    def test_passed_is_false(self):
        now = WINDOW_CLOSES + timedelta(seconds=60)
        result = check_entry_window(QUESTION, now_utc=now, is_recheck_after_approval=True)
        assert result.passed is False


class TestNormalCheckTooEarly:
    """Test 3: Normal check before window opens → TOO_EARLY."""

    def test_rejection_enum(self):
        # 10 seconds before the window opens
        now = WINDOW_OPENS - timedelta(seconds=10)
        result = check_entry_window(QUESTION, now_utc=now)
        assert result.passed is False
        assert result.rejection == EntryWindowRejection.TOO_EARLY_FOR_ENTRY_WINDOW

    def test_not_approval_delay(self):
        now = WINDOW_OPENS - timedelta(seconds=10)
        result = check_entry_window(QUESTION, now_utc=now)
        assert result.rejection != EntryWindowRejection.APPROVAL_DELAY_PUSHED_OUT_OF_WINDOW


class TestRecheckAfterApprovalTooEarly:
    """Test 4: Recheck after approval, time still before window → APPROVAL_DELAY.

    Edge case: approval queue was delayed so long that we've somehow ended up
    earlier than the window — unlikely but the code path must be exercised.
    """

    def test_rejection_enum(self):
        now = WINDOW_OPENS - timedelta(seconds=10)
        result = check_entry_window(QUESTION, now_utc=now, is_recheck_after_approval=True)
        assert result.passed is False
        assert result.rejection == EntryWindowRejection.APPROVAL_DELAY_PUSHED_OUT_OF_WINDOW

    def test_not_too_early(self):
        now = WINDOW_OPENS - timedelta(seconds=10)
        result = check_entry_window(QUESTION, now_utc=now, is_recheck_after_approval=True)
        assert result.rejection != EntryWindowRejection.TOO_EARLY_FOR_ENTRY_WINDOW


class TestRecheckWithinWindow:
    """Test 5: Recheck after approval, but still within window → passes."""

    def test_passes_at_start(self):
        # Exactly at start time — deepest into the window that approval might arrive
        result = check_entry_window(QUESTION, now_utc=START_UTC, is_recheck_after_approval=True)
        assert result.passed is True

    def test_passes_just_after_window_opens(self):
        now = WINDOW_OPENS + timedelta(seconds=1)
        result = check_entry_window(QUESTION, now_utc=now, is_recheck_after_approval=True)
        assert result.passed is True

    def test_passes_before_window_closes(self):
        now = WINDOW_CLOSES - timedelta(seconds=1)
        result = check_entry_window(QUESTION, now_utc=now, is_recheck_after_approval=True)
        assert result.passed is True

    def test_no_rejection_set(self):
        result = check_entry_window(QUESTION, now_utc=START_UTC, is_recheck_after_approval=True)
        assert result.rejection is None


class TestApprovalDelayScenario:
    """Test 6: Realistic scenario — market in window at first check; approval comes
    back 2 minutes later, pushing us past window_closes."""

    def test_initial_check_passes(self):
        # Operator sees the signal 20s before window closes (still inside)
        initial_time = WINDOW_CLOSES - timedelta(seconds=20)
        result = check_entry_window(QUESTION, now_utc=initial_time)
        assert result.passed is True

    def test_recheck_after_2_min_delay_fails(self):
        # Approval takes 2 minutes; now we are 100s past window_closes
        approval_time = WINDOW_CLOSES + timedelta(seconds=100)
        result = check_entry_window(
            QUESTION,
            now_utc=approval_time,
            is_recheck_after_approval=True,
        )
        assert result.passed is False
        assert result.rejection == EntryWindowRejection.APPROVAL_DELAY_PUSHED_OUT_OF_WINDOW

    def test_1s_before_close_still_passes(self):
        # Fast approval — 1s before window closes
        approval_time = WINDOW_CLOSES - timedelta(seconds=1)
        result = check_entry_window(
            QUESTION,
            now_utc=approval_time,
            is_recheck_after_approval=True,
        )
        assert result.passed is True

    def test_1s_after_close_rejected_with_approval_delay(self):
        # Approval arrives 1s after window_closes
        approval_time = WINDOW_CLOSES + timedelta(seconds=1)
        result = check_entry_window(
            QUESTION,
            now_utc=approval_time,
            is_recheck_after_approval=True,
        )
        assert result.passed is False
        assert result.rejection == EntryWindowRejection.APPROVAL_DELAY_PUSHED_OUT_OF_WINDOW

    def test_reason_string_populated(self):
        approval_time = WINDOW_CLOSES + timedelta(seconds=100)
        result = check_entry_window(
            QUESTION,
            now_utc=approval_time,
            is_recheck_after_approval=True,
        )
        assert result.reason  # non-empty string


# ── live_gate: is_recheck_after_approval passes through ──────────────────────

class TestLiveGateRecheckPassthrough:
    """Test 7: check_live_gate passes is_recheck_after_approval to entry_window_guard."""

    def _make_gate_call(self, tmp_path, *, now_utc: datetime, is_recheck: bool):
        """Helper — calls check_live_gate with entry window check active.

        live_gate calls check_entry_window without injecting now_utc, so we
        patch datetime.now inside entry_window_guard to control the clock.
        """
        ctrl = tmp_path / "control.json"
        ctrl.write_text(json.dumps({"live_trading": True}))
        rdns = tmp_path / "readiness.json"
        rdns.write_text(json.dumps({
            "verdict": "TINY_PILOT_CANDIDATE",
            "generated_utc": datetime.now(timezone.utc).isoformat(),
        }))

        with patch(
            "control_plane.entry_window_guard.datetime",
        ) as mock_dt:
            # Make datetime.now(timezone.utc) return our controlled time
            mock_dt.now.return_value = now_utc
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            return check_live_gate(
                control_file=str(ctrl),
                readiness_file=str(rdns),
                market_question=QUESTION,
                entry_window_policy=DEFAULT_ENTRY_WINDOW_POLICY,
                is_recheck_after_approval=is_recheck,
                is_approved=True,
            )

    def test_recheck_within_window_passes_entry_window(self, tmp_path):
        now = START_UTC  # within window
        result = self._make_gate_call(tmp_path, now_utc=now, is_recheck=True)
        # entry_window check should pass; only other checks may block
        entry_check = next(c for c in result.checks if c.name == "entry_window")
        assert entry_check.passed is True

    def test_recheck_past_window_entry_window_blocked(self, tmp_path):
        now = WINDOW_CLOSES + timedelta(seconds=60)
        result = self._make_gate_call(tmp_path, now_utc=now, is_recheck=True)
        entry_check = next(c for c in result.checks if c.name == "entry_window")
        assert entry_check.passed is False
        assert EntryWindowRejection.APPROVAL_DELAY_PUSHED_OUT_OF_WINDOW.value in entry_check.reason

    def test_normal_check_past_window_too_late_reason(self, tmp_path):
        now = WINDOW_CLOSES + timedelta(seconds=60)
        result = self._make_gate_call(tmp_path, now_utc=now, is_recheck=False)
        entry_check = next(c for c in result.checks if c.name == "entry_window")
        assert entry_check.passed is False
        assert EntryWindowRejection.TOO_LATE_FOR_ENTRY_WINDOW.value in entry_check.reason

    def test_is_recheck_false_is_default(self, tmp_path):
        """Verify that omitting is_recheck_after_approval defaults to False behaviour."""
        ctrl = tmp_path / "control.json"
        ctrl.write_text(json.dumps({"live_trading": True}))
        rdns = tmp_path / "readiness.json"
        rdns.write_text(json.dumps({
            "verdict": "TINY_PILOT_CANDIDATE",
            "generated_utc": datetime.now(timezone.utc).isoformat(),
        }))

        now = WINDOW_CLOSES + timedelta(seconds=60)
        with patch("control_plane.entry_window_guard.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            result = check_live_gate(
                control_file=str(ctrl),
                readiness_file=str(rdns),
                market_question=QUESTION,
                entry_window_policy=DEFAULT_ENTRY_WINDOW_POLICY,
                # is_recheck_after_approval not passed → defaults to False
                is_approved=True,
            )

        entry_check = next(c for c in result.checks if c.name == "entry_window")
        assert EntryWindowRejection.TOO_LATE_FOR_ENTRY_WINDOW.value in entry_check.reason
