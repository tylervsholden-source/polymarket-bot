"""Regression test: control_plane/entry_window_guard.py's date parsers used a
hardcoded `reference_year=2026` literal instead of tracking the actual
calendar year.

Polymarket question text never includes a year (e.g. "Bitcoin Up or Down -
January 5, 7:10PM-7:15PM ET"), so parse_market_start_time()/parse_market_times()
have to assume one. The hardcoded 2026 literal worked only by coincidence
while the real date happened to also be in 2026. As soon as the real
calendar moved past 2026, every market got parsed a year (or more) in the
past relative to `now_utc`, and check_entry_window() -- called from
control_plane/live_gate.py's check_live_gate() on every live order --
started rejecting every single trade as TOO_LATE_FOR_ENTRY_WINDOW,
permanently freezing live trading. The same hardcoded default fed
agents/orchestrator.py._check_binance_resolutions()'s `end_utc`, which would
make every open position look already-ended (`now_utc < end_utc + 60s`
always False), triggering premature/incorrect resolution of real positions.

Fix: reference_year now defaults to (and, in check_entry_window(), is
explicitly passed as) the year of the *current* `now_utc` -- real wall-clock
time in production, and the test's own mocked `now_utc` in tests -- instead
of a literal that goes stale.
"""
from datetime import datetime, timezone

from control_plane.entry_window_guard import (
    EntryWindowRejection,
    check_entry_window,
    parse_market_start_time,
    parse_market_times,
)

QUESTION_5M_NEXT_YEAR = "Bitcoin Up or Down - January 5, 7:10PM-7:15PM ET"
# 7:10PM ET in January (EST, UTC-5) = 00:10 UTC the next day.
EXPECTED_START_UTC_HOUR_MINUTE = (0, 10)


def test_parse_market_start_time_honors_explicit_reference_year():
    dt = parse_market_start_time(QUESTION_5M_NEXT_YEAR, reference_year=2027)
    assert dt is not None
    assert dt.year == 2027
    assert (dt.hour, dt.minute) == EXPECTED_START_UTC_HOUR_MINUTE


def test_parse_market_times_honors_explicit_reference_year():
    start, end = parse_market_times(QUESTION_5M_NEXT_YEAR, reference_year=2027)
    assert start is not None and end is not None
    assert start.year == 2027
    assert end.year == 2027


def test_check_entry_window_tracks_now_utc_year_not_hardcoded_2026():
    # It is currently 2027 (per `now_utc`) and the market starts a few
    # seconds from now. The old hardcoded reference_year=2026 would parse
    # the market's start time as being over a year in the PAST, so this
    # would incorrectly fail as TOO_LATE_FOR_ENTRY_WINDOW.
    now = datetime(2027, 1, 6, 0, 9, 50, tzinfo=timezone.utc)
    result = check_entry_window(QUESTION_5M_NEXT_YEAR, now_utc=now)

    assert result.passed is True
    assert result.rejection is None
    assert result.market_start_utc is not None
    assert result.market_start_utc.year == 2027


def test_check_entry_window_still_rejects_genuinely_stale_2027_market():
    # Sanity check: the fix must not simply always pass. A market that is
    # genuinely far past its (correctly-parsed, 2027) window should still be
    # rejected as too late.
    now = datetime(2027, 1, 6, 1, 0, 0, tzinfo=timezone.utc)  # ~50 min after start
    result = check_entry_window(QUESTION_5M_NEXT_YEAR, now_utc=now)

    assert result.passed is False
    assert result.rejection == EntryWindowRejection.TOO_LATE_FOR_ENTRY_WINDOW
    assert result.market_start_utc.year == 2027
