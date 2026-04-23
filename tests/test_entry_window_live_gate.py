"""Tests that check_live_gate enforces entry window check (check #9 of 11).

Covers:
  1. No market_question → entry_window skipped (passes)
  2. Valid question + policy, within window → entry_window passes, overall can pass
  3. Valid question + policy, too early → entry_window fails, blocks live gate
  4. Valid question + policy, too late → entry_window fails, blocks live gate
  5. Check is named "entry_window" in the checks list
  6. Total checks count is 11
  7. Blocker reason includes rejection enum value
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from functools import wraps
from unittest.mock import patch

import pytest

from control_plane.entry_window_guard import (
    EntryWindowPolicy,
    EntryWindowRejection,
    check_entry_window,
)
from control_plane.live_gate import check_live_gate

# ── Question fixture ──────────────────────────────────────────────────────────
# "Bitcoin Up or Down - March 16, 7:10PM-7:15PM ET"
# start = 7:10PM ET = 23:10 UTC  (ET+4)
# end   = 7:15PM ET = 23:15 UTC
# horizon = 5 minutes
# Default 5m policy: before=600s, after=600s
#   window_opens  = 23:00:00 UTC
#   window_closes = 23:20:00 UTC
QUESTION = "Bitcoin Up or Down - March 16, 7:10PM-7:15PM ET"
MARKET_START_UTC = datetime(2026, 3, 16, 23, 10, 0, tzinfo=timezone.utc)
POLICY = EntryWindowPolicy()


# ── Shared fixtures ───────────────────────────────────────────────────────────

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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _gate(ctrl_file, readiness_file, **kwargs):
    """Call check_live_gate with all non-entry-window checks passing."""
    defaults = dict(
        control_file=ctrl_file,
        readiness_file=readiness_file,
        daily_loss_exceeded=False,
        open_position_count=0,
        max_open_positions=5,
        max_orders_per_hour=3,
        is_approved=True,
        available_capital=100.0,
        required_capital=10.0,
    )
    defaults.update(kwargs)
    return check_live_gate(**defaults)


def _gate_at(ctrl_file, readiness_file, now_utc: datetime, **kwargs):
    """
    Call _gate with entry_window check evaluated at a fixed now_utc.

    Strategy: patch check_entry_window at its definition site
    (control_plane.entry_window_guard) and inject now_utc. live_gate imports
    it lazily with `from control_plane.entry_window_guard import check_entry_window`
    inside the function body, so patching the source module is the correct
    interception point.
    """
    real_check = check_entry_window

    def _injected(**ew_kwargs):
        # live_gate calls check_entry_window with keyword args only; inject now_utc
        ew_kwargs.setdefault("now_utc", now_utc)
        return real_check(**ew_kwargs)

    target = "control_plane.entry_window_guard.check_entry_window"
    with patch(target, side_effect=_injected):
        return _gate(ctrl_file, readiness_file, **kwargs)


# ── Test 1: no market_question / no policy → entry_window skipped ─────────────

def test_no_question_entry_window_skipped(ctrl_file, readiness_file):
    """When market_question is empty, entry_window check is skipped (passes)."""
    result = _gate(ctrl_file, readiness_file, market_question="", entry_window_policy=POLICY)
    ew_check = next(c for c in result.checks if c.name == "entry_window")
    assert ew_check.passed is True
    assert "atlandı" in ew_check.reason or ew_check.reason == "Kontrol atlandı"


def test_no_policy_entry_window_skipped(ctrl_file, readiness_file):
    """When entry_window_policy is None, entry_window check is skipped (passes)."""
    result = _gate(ctrl_file, readiness_file, market_question=QUESTION, entry_window_policy=None)
    ew_check = next(c for c in result.checks if c.name == "entry_window")
    assert ew_check.passed is True


# ── Test 2: within window → entry_window passes, overall passes ───────────────

def test_within_window_at_start_passes(ctrl_file, readiness_file):
    """Time exactly at market start (0s) is within window → entry_window passes."""
    now = MARKET_START_UTC

    result = _gate_at(
        ctrl_file, readiness_file, now,
        market_question=QUESTION,
        entry_window_policy=POLICY,
    )

    ew_check = next(c for c in result.checks if c.name == "entry_window")
    assert ew_check.passed is True
    assert result.passed is True


def test_within_window_30s_before_start(ctrl_file, readiness_file):
    """30 seconds before start (inside 600s before-window) → entry_window passes."""
    now = MARKET_START_UTC - timedelta(seconds=30)

    result = _gate_at(
        ctrl_file, readiness_file, now,
        market_question=QUESTION,
        entry_window_policy=POLICY,
    )

    ew_check = next(c for c in result.checks if c.name == "entry_window")
    assert ew_check.passed is True


def test_within_window_60s_after_start(ctrl_file, readiness_file):
    """60 seconds after start (inside 600s after-window) → entry_window passes."""
    now = MARKET_START_UTC + timedelta(seconds=60)

    result = _gate_at(
        ctrl_file, readiness_file, now,
        market_question=QUESTION,
        entry_window_policy=POLICY,
    )

    ew_check = next(c for c in result.checks if c.name == "entry_window")
    assert ew_check.passed is True


# ── Test 3: too early → entry_window fails, live gate blocked ─────────────────

def test_too_early_blocks_live_gate(ctrl_file, readiness_file):
    """Time 601s before start (before 600s window opens) → entry_window fails."""
    now = MARKET_START_UTC - timedelta(seconds=601)

    result = _gate_at(
        ctrl_file, readiness_file, now,
        market_question=QUESTION,
        entry_window_policy=POLICY,
    )

    ew_check = next(c for c in result.checks if c.name == "entry_window")
    assert ew_check.passed is False
    assert result.passed is False


def test_too_early_rejection_value_in_reason(ctrl_file, readiness_file):
    """entry_window check reason includes TOO_EARLY_FOR_ENTRY_WINDOW enum value."""
    now = MARKET_START_UTC - timedelta(seconds=601)

    result = _gate_at(
        ctrl_file, readiness_file, now,
        market_question=QUESTION,
        entry_window_policy=POLICY,
    )

    ew_check = next(c for c in result.checks if c.name == "entry_window")
    assert EntryWindowRejection.TOO_EARLY_FOR_ENTRY_WINDOW.value in ew_check.reason


# ── Test 4: too late → entry_window fails, live gate blocked ──────────────────

def test_too_late_blocks_live_gate(ctrl_file, readiness_file):
    """Time 601s after start (past 600s after-window) → entry_window fails."""
    now = MARKET_START_UTC + timedelta(seconds=601)

    result = _gate_at(
        ctrl_file, readiness_file, now,
        market_question=QUESTION,
        entry_window_policy=POLICY,
    )

    ew_check = next(c for c in result.checks if c.name == "entry_window")
    assert ew_check.passed is False
    assert result.passed is False


def test_too_late_rejection_value_in_reason(ctrl_file, readiness_file):
    """entry_window check reason includes TOO_LATE_FOR_ENTRY_WINDOW enum value."""
    now = MARKET_START_UTC + timedelta(seconds=601)

    result = _gate_at(
        ctrl_file, readiness_file, now,
        market_question=QUESTION,
        entry_window_policy=POLICY,
    )

    ew_check = next(c for c in result.checks if c.name == "entry_window")
    assert EntryWindowRejection.TOO_LATE_FOR_ENTRY_WINDOW.value in ew_check.reason


# ── Test 5: check is named "entry_window" in checks list ─────────────────────

def test_check_named_entry_window(ctrl_file, readiness_file):
    """The entry window check appears in the checks list under the name 'entry_window'."""
    result = _gate(ctrl_file, readiness_file, market_question="", entry_window_policy=None)
    names = [c.name for c in result.checks]
    assert "entry_window" in names


# ── Test 6: total checks count is 11 ─────────────────────────────────────────

def test_total_checks_count_is_11_no_entry_window(ctrl_file, readiness_file):
    """check_live_gate without entry_window args produces exactly 11 checks."""
    result = _gate(ctrl_file, readiness_file)
    assert len(result.checks) == 11


def test_total_checks_count_is_11_with_entry_window(ctrl_file, readiness_file):
    """check_live_gate with entry_window args still produces exactly 11 checks."""
    now = MARKET_START_UTC  # within window

    result = _gate_at(
        ctrl_file, readiness_file, now,
        market_question=QUESTION,
        entry_window_policy=POLICY,
    )

    assert len(result.checks) == 11


def test_total_checks_count_is_11_too_early(ctrl_file, readiness_file):
    """check_live_gate produces exactly 11 checks even when entry_window fails."""
    now = MARKET_START_UTC - timedelta(seconds=601)

    result = _gate_at(
        ctrl_file, readiness_file, now,
        market_question=QUESTION,
        entry_window_policy=POLICY,
    )

    assert len(result.checks) == 11


# ── Test 7: blocker reason includes rejection enum value ──────────────────────

def test_blockers_contains_too_early_enum_value(ctrl_file, readiness_file):
    """result.blockers includes TOO_EARLY_FOR_ENTRY_WINDOW value when too early."""
    now = MARKET_START_UTC - timedelta(seconds=601)

    result = _gate_at(
        ctrl_file, readiness_file, now,
        market_question=QUESTION,
        entry_window_policy=POLICY,
    )

    assert any(
        EntryWindowRejection.TOO_EARLY_FOR_ENTRY_WINDOW.value in b
        for b in result.blockers
    )


def test_blockers_contains_too_late_enum_value(ctrl_file, readiness_file):
    """result.blockers includes TOO_LATE_FOR_ENTRY_WINDOW value when too late."""
    now = MARKET_START_UTC + timedelta(seconds=601)

    result = _gate_at(
        ctrl_file, readiness_file, now,
        market_question=QUESTION,
        entry_window_policy=POLICY,
    )

    assert any(
        EntryWindowRejection.TOO_LATE_FOR_ENTRY_WINDOW.value in b
        for b in result.blockers
    )


# ── Recheck-after-approval ────────────────────────────────────────────────────

def test_recheck_after_approval_too_late_uses_approval_delay_rejection(ctrl_file, readiness_file):
    """is_recheck_after_approval=True + too late → APPROVAL_DELAY rejection."""
    now = MARKET_START_UTC + timedelta(seconds=601)

    result = _gate_at(
        ctrl_file, readiness_file, now,
        market_question=QUESTION,
        entry_window_policy=POLICY,
        is_recheck_after_approval=True,
    )

    ew_check = next(c for c in result.checks if c.name == "entry_window")
    assert ew_check.passed is False
    assert EntryWindowRejection.APPROVAL_DELAY_PUSHED_OUT_OF_WINDOW.value in ew_check.reason


def test_recheck_after_approval_too_early_uses_approval_delay_rejection(ctrl_file, readiness_file):
    """is_recheck_after_approval=True + too early → APPROVAL_DELAY rejection."""
    now = MARKET_START_UTC - timedelta(seconds=601)

    result = _gate_at(
        ctrl_file, readiness_file, now,
        market_question=QUESTION,
        entry_window_policy=POLICY,
        is_recheck_after_approval=True,
    )

    ew_check = next(c for c in result.checks if c.name == "entry_window")
    assert ew_check.passed is False
    assert EntryWindowRejection.APPROVAL_DELAY_PUSHED_OUT_OF_WINDOW.value in ew_check.reason


# ── Window boundary edge cases ────────────────────────────────────────────────

def test_exactly_at_window_open_boundary_passes(ctrl_file, readiness_file):
    """Exactly at window open (600s before start) → entry_window passes."""
    now = MARKET_START_UTC - timedelta(seconds=600)

    result = _gate_at(
        ctrl_file, readiness_file, now,
        market_question=QUESTION,
        entry_window_policy=POLICY,
    )

    ew_check = next(c for c in result.checks if c.name == "entry_window")
    assert ew_check.passed is True


def test_exactly_at_window_close_boundary_passes(ctrl_file, readiness_file):
    """Exactly at window close (600s after start) → entry_window passes."""
    now = MARKET_START_UTC + timedelta(seconds=600)

    result = _gate_at(
        ctrl_file, readiness_file, now,
        market_question=QUESTION,
        entry_window_policy=POLICY,
    )

    ew_check = next(c for c in result.checks if c.name == "entry_window")
    assert ew_check.passed is True


def test_one_second_before_window_open_fails(ctrl_file, readiness_file):
    """One second before window open (601s before start) → entry_window fails."""
    now = MARKET_START_UTC - timedelta(seconds=601)

    result = _gate_at(
        ctrl_file, readiness_file, now,
        market_question=QUESTION,
        entry_window_policy=POLICY,
    )

    ew_check = next(c for c in result.checks if c.name == "entry_window")
    assert ew_check.passed is False


def test_one_second_after_window_close_fails(ctrl_file, readiness_file):
    """One second after window close (601s after start) → entry_window fails."""
    now = MARKET_START_UTC + timedelta(seconds=601)

    result = _gate_at(
        ctrl_file, readiness_file, now,
        market_question=QUESTION,
        entry_window_policy=POLICY,
    )

    ew_check = next(c for c in result.checks if c.name == "entry_window")
    assert ew_check.passed is False
