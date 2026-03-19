"""Tests for control_plane/entry_window_guard.py — entry window policy."""

from datetime import datetime, timezone
import pytest

from control_plane.entry_window_guard import (
    EntryWindowRejection,
    EntryWindowConfig,
    EntryWindowPolicy,
    EntryWindowResult,
    check_entry_window,
    parse_market_start_time,
    DEFAULT_ENTRY_WINDOW_POLICY,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UTC = timezone.utc

QUESTION_5M = "Bitcoin Up or Down - March 16, 7:10PM-7:15PM ET"
QUESTION_15M = "Bitcoin Up or Down - March 16, 7:00PM-7:15PM ET"
QUESTION_NO_TIME = "Will the Fed raise rates in 2026?"
QUESTION_30M = "Bitcoin Up or Down - March 16, 7:00PM-7:30PM ET"

# 5m start: 2026-03-16 23:10:00 UTC  (ET = UTC-4, so 7:10PM ET -> 23:10 UTC)
START_5M = datetime(2026, 3, 16, 23, 10, 0, tzinfo=UTC)

# 15m start: 2026-03-16 23:00:00 UTC  (7:00PM ET -> 23:00 UTC)
START_15M = datetime(2026, 3, 16, 23, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# 1. parse_market_start_time
# ---------------------------------------------------------------------------


def test_parse_5m_start_time():
    dt = parse_market_start_time(QUESTION_5M)
    assert dt == START_5M


def test_parse_15m_start_time():
    dt = parse_market_start_time(QUESTION_15M)
    assert dt == START_15M


def test_parse_missing_start_time_returns_none():
    dt = parse_market_start_time(QUESTION_NO_TIME)
    assert dt is None


# ---------------------------------------------------------------------------
# 2. 5m market — within window → passed
# ---------------------------------------------------------------------------


def test_5m_within_window_passes():
    # 10 seconds before start — well inside [45s before, 90s after]
    now = datetime(2026, 3, 16, 23, 9, 50, tzinfo=UTC)
    result = check_entry_window(QUESTION_5M, now_utc=now)
    assert result.passed is True
    assert result.rejection is None
    assert result.horizon_minutes == 5


# ---------------------------------------------------------------------------
# 3. 5m market — too early → TOO_EARLY_FOR_ENTRY_WINDOW
# ---------------------------------------------------------------------------


def test_5m_too_early_rejected():
    # 2 minutes before start — outside the 45s-before window
    now = datetime(2026, 3, 16, 23, 8, 0, tzinfo=UTC)
    result = check_entry_window(QUESTION_5M, now_utc=now)
    assert result.passed is False
    assert result.rejection == EntryWindowRejection.TOO_EARLY_FOR_ENTRY_WINDOW


# ---------------------------------------------------------------------------
# 4. 5m market — too late → TOO_LATE_FOR_ENTRY_WINDOW
# ---------------------------------------------------------------------------


def test_5m_too_late_rejected():
    # 3 minutes after start — outside the 90s-after window
    now = datetime(2026, 3, 16, 23, 13, 0, tzinfo=UTC)
    result = check_entry_window(QUESTION_5M, now_utc=now)
    assert result.passed is False
    assert result.rejection == EntryWindowRejection.TOO_LATE_FOR_ENTRY_WINDOW


# ---------------------------------------------------------------------------
# 5. 15m market — within window → passed
# ---------------------------------------------------------------------------


def test_15m_within_window_passes():
    # 30 seconds before start — inside [60s before, 180s after]
    now = datetime(2026, 3, 16, 22, 59, 30, tzinfo=UTC)
    result = check_entry_window(QUESTION_15M, now_utc=now)
    assert result.passed is True
    assert result.rejection is None
    assert result.horizon_minutes == 15


# ---------------------------------------------------------------------------
# 6. 15m market — too early → rejection
# ---------------------------------------------------------------------------


def test_15m_too_early_rejected():
    # 2 minutes before start — outside the 60s-before window
    now = datetime(2026, 3, 16, 22, 58, 0, tzinfo=UTC)
    result = check_entry_window(QUESTION_15M, now_utc=now)
    assert result.passed is False
    assert result.rejection == EntryWindowRejection.TOO_EARLY_FOR_ENTRY_WINDOW


# ---------------------------------------------------------------------------
# 7. 15m market — too late → rejection
# ---------------------------------------------------------------------------


def test_15m_too_late_rejected():
    # 4 minutes after start — outside the 180s-after window
    now = datetime(2026, 3, 16, 23, 4, 0, tzinfo=UTC)
    result = check_entry_window(QUESTION_15M, now_utc=now)
    assert result.passed is False
    assert result.rejection == EntryWindowRejection.TOO_LATE_FOR_ENTRY_WINDOW


# ---------------------------------------------------------------------------
# 8. Missing start time → START_TIME_MISSING
# ---------------------------------------------------------------------------


def test_missing_start_time_returns_rejection():
    now = datetime(2026, 3, 16, 12, 0, 0, tzinfo=UTC)
    result = check_entry_window(QUESTION_NO_TIME, now_utc=now)
    assert result.passed is False
    assert result.rejection == EntryWindowRejection.START_TIME_MISSING


# ---------------------------------------------------------------------------
# 9. Unsupported horizon (30m) → ENTRY_WINDOW_UNAVAILABLE
# ---------------------------------------------------------------------------


def test_unsupported_horizon_returns_unavailable():
    now = datetime(2026, 3, 16, 19, 10, 0, tzinfo=UTC)
    result = check_entry_window(QUESTION_30M, now_utc=now)
    assert result.passed is False
    assert result.rejection == EntryWindowRejection.ENTRY_WINDOW_UNAVAILABLE


# ---------------------------------------------------------------------------
# 10. Custom policy overrides defaults
# ---------------------------------------------------------------------------


def test_custom_policy_overrides_defaults():
    # Custom policy: 5m window is 10s before / 20s after — very tight
    custom_policy = EntryWindowPolicy(
        windows_5m=EntryWindowConfig(entry_before_start_sec=10, entry_after_start_sec=20),
        windows_15m=EntryWindowConfig(entry_before_start_sec=60, entry_after_start_sec=180),
    )
    # 30 seconds before start → would pass default (45s), fails custom (10s)
    now = datetime(2026, 3, 16, 23, 9, 30, tzinfo=UTC)
    result = check_entry_window(QUESTION_5M, now_utc=now, policy=custom_policy)
    assert result.passed is False
    assert result.rejection == EntryWindowRejection.TOO_EARLY_FOR_ENTRY_WINDOW

    # 5 seconds before start → passes custom (10s before)
    now_inside = datetime(2026, 3, 16, 23, 9, 55, tzinfo=UTC)
    result_inside = check_entry_window(QUESTION_5M, now_utc=now_inside, policy=custom_policy)
    assert result_inside.passed is True


# ---------------------------------------------------------------------------
# 11. Window bounds — 5m: opens 45s before, closes 90s after start
# ---------------------------------------------------------------------------


def test_5m_window_bounds_computed_correctly():
    now = datetime(2026, 3, 16, 23, 9, 50, tzinfo=UTC)
    result = check_entry_window(QUESTION_5M, now_utc=now)

    expected_opens = datetime(2026, 3, 16, 23, 9, 15, tzinfo=UTC)   # 23:10 - 45s
    expected_closes = datetime(2026, 3, 16, 23, 11, 30, tzinfo=UTC)  # 23:10 + 90s

    assert result.window_opens_at == expected_opens
    assert result.window_closes_at == expected_closes
    assert result.market_start_utc == START_5M


# ---------------------------------------------------------------------------
# 12. Window bounds — 15m: opens 60s before, closes 180s after start
# ---------------------------------------------------------------------------


def test_15m_window_bounds_computed_correctly():
    now = datetime(2026, 3, 16, 22, 59, 30, tzinfo=UTC)
    result = check_entry_window(QUESTION_15M, now_utc=now)

    expected_opens = datetime(2026, 3, 16, 22, 59, 0, tzinfo=UTC)   # 23:00 - 60s
    expected_closes = datetime(2026, 3, 16, 23, 3, 0, tzinfo=UTC)   # 23:00 + 180s

    assert result.window_opens_at == expected_opens
    assert result.window_closes_at == expected_closes
    assert result.market_start_utc == START_15M


# ---------------------------------------------------------------------------
# 13. seconds_to_start is populated correctly
# ---------------------------------------------------------------------------


def test_seconds_to_start_populated():
    # 20 seconds before start
    now = datetime(2026, 3, 16, 23, 9, 40, tzinfo=UTC)
    result = check_entry_window(QUESTION_5M, now_utc=now)
    assert result.seconds_to_start is not None
    assert abs(result.seconds_to_start - 20.0) < 1.0


# ---------------------------------------------------------------------------
# 14. APPROVAL_DELAY_PUSHED_OUT_OF_WINDOW via is_recheck_after_approval
# ---------------------------------------------------------------------------


def test_approval_delay_pushed_out_of_window():
    # now is 95 seconds after start — beyond 90s window
    # when this is a recheck after approval, expect specific rejection
    now = datetime(2026, 3, 16, 23, 11, 35, tzinfo=UTC)
    result = check_entry_window(
        QUESTION_5M, now_utc=now, is_recheck_after_approval=True
    )
    assert result.passed is False
    assert result.rejection == EntryWindowRejection.APPROVAL_DELAY_PUSHED_OUT_OF_WINDOW


# ---------------------------------------------------------------------------
# 15. DEFAULT_ENTRY_WINDOW_POLICY has expected default values
# ---------------------------------------------------------------------------


def test_default_policy_values():
    assert DEFAULT_ENTRY_WINDOW_POLICY.windows_5m.entry_before_start_sec == 45
    assert DEFAULT_ENTRY_WINDOW_POLICY.windows_5m.entry_after_start_sec == 90
    assert DEFAULT_ENTRY_WINDOW_POLICY.windows_15m.entry_before_start_sec == 60
    assert DEFAULT_ENTRY_WINDOW_POLICY.windows_15m.entry_after_start_sec == 180
