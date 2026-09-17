"""
Entry Window Guard — Start-time-based entry window enforcement.

Problem solved:
    Previous timing logic was based on time-to-resolution (end time).
    This guard enforces entry windows relative to market START time.

Policy:
    For each horizon (5m, 15m), define:
      - entry_before_start_sec: how many seconds BEFORE start time entry is allowed
      - entry_after_start_sec: how many seconds AFTER start time entry is still allowed

    Example for 5m market:
      entry_before_start_sec = 45   (can enter up to 45s before market opens)
      entry_after_start_sec  = 90   (can enter up to 90s after market opens)

    Example for 15m market:
      entry_before_start_sec = 60   (can enter up to 60s before market opens)
      entry_after_start_sec  = 180  (can enter up to 180s after market opens)

Rejection reasons:
    TOO_EARLY_FOR_ENTRY_WINDOW  — current time is before the allowed window
    TOO_LATE_FOR_ENTRY_WINDOW   — current time is after the allowed window
    START_TIME_MISSING          — market start time cannot be determined
    ENTRY_WINDOW_UNAVAILABLE    — no policy defined for this horizon
    APPROVAL_DELAY_PUSHED_OUT_OF_WINDOW — re-check after approval delay failed
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


class EntryWindowRejection(str, Enum):
    TOO_EARLY_FOR_ENTRY_WINDOW = "TOO_EARLY_FOR_ENTRY_WINDOW"
    TOO_LATE_FOR_ENTRY_WINDOW = "TOO_LATE_FOR_ENTRY_WINDOW"
    START_TIME_MISSING = "START_TIME_MISSING"
    ENTRY_WINDOW_UNAVAILABLE = "ENTRY_WINDOW_UNAVAILABLE"
    APPROVAL_DELAY_PUSHED_OUT_OF_WINDOW = "APPROVAL_DELAY_PUSHED_OUT_OF_WINDOW"


@dataclass
class EntryWindowConfig:
    """Configurable entry window per horizon."""
    # How many seconds before market start time is entry allowed
    entry_before_start_sec: int = 60
    # How many seconds after market start time is entry still allowed
    entry_after_start_sec: int = 120


@dataclass
class EntryWindowPolicy:
    """Complete entry window policy for all horizons."""
    windows_5m: EntryWindowConfig = None  # type: ignore[assignment]
    windows_15m: EntryWindowConfig = None  # type: ignore[assignment]
    windows_1h: EntryWindowConfig = None  # type: ignore[assignment]

    def __post_init__(self):
        # Entry window genişletildi — sinyal neyse o, zamanlama engellemesin
        if self.windows_5m is None:
            self.windows_5m = EntryWindowConfig(
                entry_before_start_sec=600,
                entry_after_start_sec=600,
            )
        if self.windows_15m is None:
            self.windows_15m = EntryWindowConfig(
                entry_before_start_sec=900,
                entry_after_start_sec=900,
            )
        if self.windows_1h is None:
            self.windows_1h = EntryWindowConfig(
                entry_before_start_sec=3600,
                entry_after_start_sec=3600,
            )

    def get_window(self, horizon_minutes: int) -> Optional[EntryWindowConfig]:
        if horizon_minutes == 5:
            return self.windows_5m
        if horizon_minutes == 15:
            return self.windows_15m
        if horizon_minutes == 60:
            return self.windows_1h
        return None


@dataclass
class EntryWindowResult:
    """Result of entry window check."""
    passed: bool
    rejection: Optional[EntryWindowRejection] = None
    reason: str = ""
    market_start_utc: Optional[datetime] = None
    seconds_to_start: Optional[float] = None
    window_opens_at: Optional[datetime] = None
    window_closes_at: Optional[datetime] = None
    horizon_minutes: int = 0


# Default policy
DEFAULT_ENTRY_WINDOW_POLICY = EntryWindowPolicy()


# ── Market start time parsing ────────────────────────────────────────────────

_TIME_RE = re.compile(
    r'(\d{1,2}):(\d{2})\s*(AM|PM)\s*[-–]\s*(\d{1,2}):(\d{2})\s*(AM|PM)',
    re.IGNORECASE,
)

_DATE_RE = re.compile(
    r'(January|February|March|April|May|June|July|August|September|October|November|December)'
    r'\s+(\d{1,2})',
    re.IGNORECASE,
)

_MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def parse_market_start_time(question: str, reference_year: int | None = None) -> Optional[datetime]:
    """
    Parse market start time from question text.

    Example: "Bitcoin Up or Down - March 16, 7:10PM-7:15PM ET"
    Returns: datetime(2026, 3, 16, 23, 10, tzinfo=UTC)  (7:10PM ET = 23:10 UTC)

    ET = UTC-4 (simplified; DST-aware would need pytz/zoneinfo).
    Returns None if parsing fails.

    reference_year: the calendar year to assume for the (year-less) date in
    the question text. Defaults to the real current UTC year rather than a
    frozen literal — Polymarket question text never includes a year, so
    hardcoding one here silently misdated every market (and, downstream,
    permanently failed every check_entry_window() call as
    TOO_LATE_FOR_ENTRY_WINDOW) as soon as the real calendar moved past that
    literal. check_entry_window() instead passes the year of its own
    (mockable) `now_utc`, so tests stay deterministic.
    """
    if reference_year is None:
        reference_year = datetime.now(timezone.utc).year
    time_match = _TIME_RE.search(question)
    date_match = _DATE_RE.search(question)

    if not time_match or not date_match:
        return None

    try:
        month_name = date_match.group(1).lower()
        day = int(date_match.group(2))
        month = _MONTH_MAP.get(month_name)
        if month is None:
            return None

        h1 = int(time_match.group(1))
        m1 = int(time_match.group(2))
        ap1 = time_match.group(3).upper()

        # Convert 12h to 24h
        hour_24 = (h1 % 12) + (12 if ap1 == "PM" else 0)

        # Build ET time then convert to UTC (DST-aware)
        et_dt = datetime(reference_year, month, day, hour_24, m1, 0, tzinfo=_ET)
        utc_dt = et_dt.astimezone(timezone.utc)
        return utc_dt
    except (ValueError, TypeError):
        return None


def parse_market_times(question: str, reference_year: int | None = None) -> tuple[Optional[datetime], Optional[datetime]]:
    """Parse both start and end times from question text. Returns (start_utc, end_utc).

    See parse_market_start_time() for why `reference_year` defaults to the
    real current UTC year instead of a hardcoded literal.
    """
    if reference_year is None:
        reference_year = datetime.now(timezone.utc).year
    time_match = _TIME_RE.search(question)
    date_match = _DATE_RE.search(question)

    if not time_match or not date_match:
        return None, None

    try:
        month_name = date_match.group(1).lower()
        day = int(date_match.group(2))
        month = _MONTH_MAP.get(month_name)
        if month is None:
            return None, None

        h1, m1 = int(time_match.group(1)), int(time_match.group(2))
        ap1 = time_match.group(3).upper()
        h2, m2 = int(time_match.group(4)), int(time_match.group(5))
        ap2 = time_match.group(6).upper()

        start_h = (h1 % 12) + (12 if ap1 == "PM" else 0)
        end_h = (h2 % 12) + (12 if ap2 == "PM" else 0)

        start_et = datetime(reference_year, month, day, start_h, m1, 0, tzinfo=_ET)
        end_et = datetime(reference_year, month, day, end_h, m2, 0, tzinfo=_ET)

        # Midnight crossover: "11:55 PM - 12:00 AM" → end is next day
        if end_et <= start_et:
            end_et = end_et + timedelta(days=1)

        start_utc = start_et.astimezone(timezone.utc)
        end_utc = end_et.astimezone(timezone.utc)

        return start_utc, end_utc
    except (ValueError, TypeError):
        return None, None


def compute_horizon_minutes(question: str) -> Optional[int]:
    """Compute horizon from question text time range."""
    start, end = parse_market_times(question)
    if start and end:
        diff = (end - start).total_seconds() / 60
        if diff > 0:
            return int(round(diff))
    return None


# ── Entry window check ───────────────────────────────────────────────────────

def check_entry_window(
    question: str,
    now_utc: Optional[datetime] = None,
    policy: EntryWindowPolicy = DEFAULT_ENTRY_WINDOW_POLICY,
    is_recheck_after_approval: bool = False,
) -> EntryWindowResult:
    """
    Check if current time is within the allowed entry window for a market.

    Parameters
    ----------
    question : market question text (contains time range)
    now_utc  : current time (None → now)
    policy   : entry window policy config
    is_recheck_after_approval : if True, uses APPROVAL_DELAY rejection reason

    Returns
    -------
    EntryWindowResult with passed=True if within window
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    # Parse market start time — reference_year follows `now_utc` (real or
    # mocked) so a question's year-less date resolves to the year actually
    # in effect right now, not a hardcoded literal that goes stale.
    market_start = parse_market_start_time(question, reference_year=now_utc.year)
    if market_start is None:
        return EntryWindowResult(
            passed=False,
            rejection=EntryWindowRejection.START_TIME_MISSING,
            reason="Cannot parse market start time from question text",
        )

    # Determine horizon
    horizon = compute_horizon_minutes(question)
    if horizon is None:
        return EntryWindowResult(
            passed=False,
            rejection=EntryWindowRejection.ENTRY_WINDOW_UNAVAILABLE,
            reason="Cannot determine market horizon from question text",
            market_start_utc=market_start,
        )

    # Normalize to closest supported horizon
    if horizon <= 7:
        horizon_key = 5
    elif horizon <= 20:
        horizon_key = 15
    elif horizon <= 65:
        horizon_key = 60
    else:
        return EntryWindowResult(
            passed=False,
            rejection=EntryWindowRejection.ENTRY_WINDOW_UNAVAILABLE,
            reason=f"No entry window policy for horizon={horizon}m (only 5m/15m/1h supported)",
            market_start_utc=market_start,
            horizon_minutes=horizon,
        )

    window_config = policy.get_window(horizon_key)
    if window_config is None:
        return EntryWindowResult(
            passed=False,
            rejection=EntryWindowRejection.ENTRY_WINDOW_UNAVAILABLE,
            reason=f"No entry window config for {horizon_key}m horizon",
            market_start_utc=market_start,
            horizon_minutes=horizon_key,
        )

    # Calculate window bounds
    window_opens = market_start - timedelta(seconds=window_config.entry_before_start_sec)
    window_closes = market_start + timedelta(seconds=window_config.entry_after_start_sec)

    seconds_to_start = (market_start - now_utc).total_seconds()

    # Check if within window
    if now_utc < window_opens:
        rej = (
            EntryWindowRejection.APPROVAL_DELAY_PUSHED_OUT_OF_WINDOW
            if is_recheck_after_approval
            else EntryWindowRejection.TOO_EARLY_FOR_ENTRY_WINDOW
        )
        return EntryWindowResult(
            passed=False,
            rejection=rej,
            reason=(
                f"Too early: {abs(seconds_to_start):.0f}s before start, "
                f"window opens {window_config.entry_before_start_sec}s before start"
            ),
            market_start_utc=market_start,
            seconds_to_start=seconds_to_start,
            window_opens_at=window_opens,
            window_closes_at=window_closes,
            horizon_minutes=horizon_key,
        )

    if now_utc > window_closes:
        rej = (
            EntryWindowRejection.APPROVAL_DELAY_PUSHED_OUT_OF_WINDOW
            if is_recheck_after_approval
            else EntryWindowRejection.TOO_LATE_FOR_ENTRY_WINDOW
        )
        return EntryWindowResult(
            passed=False,
            rejection=rej,
            reason=(
                f"Too late: {abs(seconds_to_start):.0f}s {'past' if seconds_to_start < 0 else 'to'} start, "
                f"window closed {window_config.entry_after_start_sec}s after start"
            ),
            market_start_utc=market_start,
            seconds_to_start=seconds_to_start,
            window_opens_at=window_opens,
            window_closes_at=window_closes,
            horizon_minutes=horizon_key,
        )

    # Within window
    return EntryWindowResult(
        passed=True,
        reason=(
            f"Within entry window: {seconds_to_start:.0f}s to start | "
            f"window=[{window_config.entry_before_start_sec}s before, "
            f"{window_config.entry_after_start_sec}s after]"
        ),
        market_start_utc=market_start,
        seconds_to_start=seconds_to_start,
        window_opens_at=window_opens,
        window_closes_at=window_closes,
        horizon_minutes=horizon_key,
    )
