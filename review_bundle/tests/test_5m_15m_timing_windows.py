"""
Tests for entry window policies per horizon (5m vs 15m).

Covers:
- Default 5m policy: entry_before_start_sec=45, entry_after_start_sec=90
- Default 15m policy: entry_before_start_sec=60, entry_after_start_sec=180
- Boundary conditions (1s inside/outside window edges)
- compute_horizon_minutes parsing
- Custom policy overrides
"""
from datetime import datetime, timedelta, timezone

import pytest

from control_plane.entry_window_guard import (
    DEFAULT_ENTRY_WINDOW_POLICY,
    EntryWindowConfig,
    EntryWindowPolicy,
    EntryWindowRejection,
    check_entry_window,
    compute_horizon_minutes,
    parse_market_start_time,
)

# ── Market question fixtures ──────────────────────────────────────────────────

# 5m: 7:10PM–7:15PM ET  → start UTC = 23:10:00
Q_5M = "Bitcoin Up or Down - March 16, 7:10PM-7:15PM ET"
# 15m: 7:00PM–7:15PM ET → start UTC = 23:00:00
Q_15M = "Bitcoin Up or Down - March 16, 7:00PM-7:15PM ET"

# Precomputed start times (ET + 4h = UTC)
START_5M_UTC = datetime(2026, 3, 16, 23, 10, 0, tzinfo=timezone.utc)
START_15M_UTC = datetime(2026, 3, 16, 23, 0, 0, tzinfo=timezone.utc)


# ── Helper ────────────────────────────────────────────────────────────────────

def at_offset(base: datetime, seconds: float) -> datetime:
    """Return base + seconds (negative = before base)."""
    return base + timedelta(seconds=seconds)


# ── Test 1: 5m market — verify parse and window bounds ───────────────────────

def test_5m_market_start_parsed_correctly():
    start = parse_market_start_time(Q_5M)
    assert start == START_5M_UTC, f"Expected {START_5M_UTC}, got {start}"


def test_15m_market_start_parsed_correctly():
    start = parse_market_start_time(Q_15M)
    assert start == START_15M_UTC, f"Expected {START_15M_UTC}, got {start}"


# ── Test 2: compute_horizon_minutes ──────────────────────────────────────────

def test_compute_horizon_5m():
    """Test 12: compute_horizon_minutes returns 5 for 5m market."""
    h = compute_horizon_minutes(Q_5M)
    assert h == 5


def test_compute_horizon_15m():
    """Test 12: compute_horizon_minutes returns 15 for 15m market."""
    h = compute_horizon_minutes(Q_15M)
    assert h == 15


# ── Test 3: 5m at 44s before start → passes ──────────────────────────────────

def test_5m_44s_before_start_passes():
    """Test 3: 44s before start is within the 45s pre-window → should pass."""
    now = at_offset(START_5M_UTC, -44)
    result = check_entry_window(Q_5M, now_utc=now)
    assert result.passed is True, f"Expected pass, got: {result.reason}"
    assert result.horizon_minutes == 5


# ── Test 4: 5m at 46s before start → fails ───────────────────────────────────

def test_5m_46s_before_start_fails():
    """Test 4: 46s before start is outside the 45s pre-window → too early."""
    now = at_offset(START_5M_UTC, -46)
    result = check_entry_window(Q_5M, now_utc=now)
    assert result.passed is False
    assert result.rejection == EntryWindowRejection.TOO_EARLY_FOR_ENTRY_WINDOW


# ── Test 5: 5m at 89s after start → passes ───────────────────────────────────

def test_5m_89s_after_start_passes():
    """Test 5: 89s after start is within the 90s post-window → should pass."""
    now = at_offset(START_5M_UTC, 89)
    result = check_entry_window(Q_5M, now_utc=now)
    assert result.passed is True, f"Expected pass, got: {result.reason}"


# ── Test 6: 5m at 91s after start → fails ────────────────────────────────────

def test_5m_91s_after_start_fails():
    """Test 6: 91s after start exceeds the 90s post-window → too late."""
    now = at_offset(START_5M_UTC, 91)
    result = check_entry_window(Q_5M, now_utc=now)
    assert result.passed is False
    assert result.rejection == EntryWindowRejection.TOO_LATE_FOR_ENTRY_WINDOW


# ── Test 7: 15m at 59s before start → passes ─────────────────────────────────

def test_15m_59s_before_start_passes():
    """Test 7: 59s before start is within the 60s pre-window → should pass."""
    now = at_offset(START_15M_UTC, -59)
    result = check_entry_window(Q_15M, now_utc=now)
    assert result.passed is True, f"Expected pass, got: {result.reason}"
    assert result.horizon_minutes == 15


# ── Test 8: 15m at 61s before start → fails ──────────────────────────────────

def test_15m_61s_before_start_fails():
    """Test 8: 61s before start is outside the 60s pre-window → too early."""
    now = at_offset(START_15M_UTC, -61)
    result = check_entry_window(Q_15M, now_utc=now)
    assert result.passed is False
    assert result.rejection == EntryWindowRejection.TOO_EARLY_FOR_ENTRY_WINDOW


# ── Test 9: 15m at 179s after start → passes ─────────────────────────────────

def test_15m_179s_after_start_passes():
    """Test 9: 179s after start is within the 180s post-window → should pass."""
    now = at_offset(START_15M_UTC, 179)
    result = check_entry_window(Q_15M, now_utc=now)
    assert result.passed is True, f"Expected pass, got: {result.reason}"


# ── Test 10: 15m at 181s after start → fails ─────────────────────────────────

def test_15m_181s_after_start_fails():
    """Test 10: 181s after start exceeds the 180s post-window → too late."""
    now = at_offset(START_15M_UTC, 181)
    result = check_entry_window(Q_15M, now_utc=now)
    assert result.passed is False
    assert result.rejection == EntryWindowRejection.TOO_LATE_FOR_ENTRY_WINDOW


# ── Test 11: Custom policy for 5m market ─────────────────────────────────────

def test_custom_policy_5m_30s_60s():
    """Test 11: Custom policy (30s/60s) produces tighter window than default."""
    custom_policy = EntryWindowPolicy(
        windows_5m=EntryWindowConfig(
            entry_before_start_sec=30,
            entry_after_start_sec=60,
        ),
        windows_15m=EntryWindowConfig(
            entry_before_start_sec=60,
            entry_after_start_sec=180,
        ),
    )

    # 29s before start → within 30s pre-window → passes
    now_pass = at_offset(START_5M_UTC, -29)
    result_pass = check_entry_window(Q_5M, now_utc=now_pass, policy=custom_policy)
    assert result_pass.passed is True, f"Expected pass at 29s before start, got: {result_pass.reason}"

    # 31s before start → outside 30s pre-window → fails
    now_fail_early = at_offset(START_5M_UTC, -31)
    result_fail_early = check_entry_window(Q_5M, now_utc=now_fail_early, policy=custom_policy)
    assert result_fail_early.passed is False
    assert result_fail_early.rejection == EntryWindowRejection.TOO_EARLY_FOR_ENTRY_WINDOW

    # 59s after start → within 60s post-window → passes
    now_pass_late = at_offset(START_5M_UTC, 59)
    result_pass_late = check_entry_window(Q_5M, now_utc=now_pass_late, policy=custom_policy)
    assert result_pass_late.passed is True, f"Expected pass at 59s after start, got: {result_pass_late.reason}"

    # 61s after start → outside 60s post-window → fails
    now_fail_late = at_offset(START_5M_UTC, 61)
    result_fail_late = check_entry_window(Q_5M, now_utc=now_fail_late, policy=custom_policy)
    assert result_fail_late.passed is False
    assert result_fail_late.rejection == EntryWindowRejection.TOO_LATE_FOR_ENTRY_WINDOW


# ── Test: window boundary metadata ───────────────────────────────────────────

def test_5m_result_contains_correct_window_bounds():
    """EntryWindowResult exposes correct window_opens_at and window_closes_at."""
    now = at_offset(START_5M_UTC, 0)  # exactly at start
    result = check_entry_window(Q_5M, now_utc=now)

    assert result.passed is True
    assert result.window_opens_at == START_5M_UTC - timedelta(seconds=45)
    assert result.window_closes_at == START_5M_UTC + timedelta(seconds=90)
    assert result.market_start_utc == START_5M_UTC


def test_15m_result_contains_correct_window_bounds():
    """EntryWindowResult exposes correct window_opens_at and window_closes_at."""
    now = at_offset(START_15M_UTC, 0)  # exactly at start
    result = check_entry_window(Q_15M, now_utc=now)

    assert result.passed is True
    assert result.window_opens_at == START_15M_UTC - timedelta(seconds=60)
    assert result.window_closes_at == START_15M_UTC + timedelta(seconds=180)
    assert result.market_start_utc == START_15M_UTC


# ── Test: horizon > 20 → ENTRY_WINDOW_UNAVAILABLE ────────────────────────────

def test_horizon_above_20_returns_unavailable():
    """Horizon > 20m has no defined policy → ENTRY_WINDOW_UNAVAILABLE."""
    q_long = "Bitcoin Up or Down - March 16, 7:00PM-7:30PM ET"  # 30m market
    now = datetime(2026, 3, 16, 23, 0, 0, tzinfo=timezone.utc)
    result = check_entry_window(q_long, now_utc=now)
    assert result.passed is False
    assert result.rejection == EntryWindowRejection.ENTRY_WINDOW_UNAVAILABLE


# ── Test: default policy values match specification ───────────────────────────

def test_default_policy_5m_values():
    """Default 5m policy must be 45s / 90s per spec."""
    assert DEFAULT_ENTRY_WINDOW_POLICY.windows_5m.entry_before_start_sec == 45
    assert DEFAULT_ENTRY_WINDOW_POLICY.windows_5m.entry_after_start_sec == 90


def test_default_policy_15m_values():
    """Default 15m policy must be 60s / 180s per spec."""
    assert DEFAULT_ENTRY_WINDOW_POLICY.windows_15m.entry_before_start_sec == 60
    assert DEFAULT_ENTRY_WINDOW_POLICY.windows_15m.entry_after_start_sec == 180
