"""Tests for control_plane.expiry_guard."""
import pytest
from datetime import datetime, timedelta, timezone
from control_plane.expiry_guard import ExpiryGuard


@pytest.fixture
def guard():
    return ExpiryGuard(min_hours=0.0, max_hours=24.0)


def _market(hours_from_now=None, end_date=None, market_id="test"):
    """Helper to create a market dict with optional end_date."""
    m = {"condition_id": market_id, "question": "Test market?"}
    if hours_from_now is not None:
        dt = datetime.now(timezone.utc) + timedelta(hours=hours_from_now)
        m["end_date_iso"] = dt.isoformat()
    elif end_date is not None:
        m["end_date_iso"] = end_date
    return m


def test_valid_market_passes(guard):
    m = _market(hours_from_now=5)
    assert guard.check(m) is None


def test_expired_market_rejected(guard):
    m = _market(hours_from_now=-1)
    rej = guard.check(m)
    assert rej is not None
    assert rej.reason == "EXPIRED"


def test_no_end_date_rejected(guard):
    m = {"condition_id": "x", "question": "No end date"}
    rej = guard.check(m)
    assert rej is not None
    assert rej.reason == "NO_END_DATE"


def test_too_far_rejected():
    g = ExpiryGuard(min_hours=0.0, max_hours=4.0)
    m = _market(hours_from_now=10)
    rej = g.check(m)
    assert rej is not None
    assert rej.reason == "TOO_FAR"


def test_too_near_rejected():
    g = ExpiryGuard(min_hours=1.0, max_hours=24.0)
    m = _market(hours_from_now=0.5)
    rej = g.check(m)
    assert rej is not None
    assert rej.reason == "TOO_NEAR"


def test_hours_to_close_positive(guard):
    m = _market(hours_from_now=3)
    h = guard.hours_to_close(m)
    assert h is not None
    assert 2.9 < h < 3.1


def test_hours_to_close_negative_for_expired(guard):
    m = _market(hours_from_now=-2)
    h = guard.hours_to_close(m)
    assert h is not None
    assert h < 0


def test_hours_to_close_none_for_no_date(guard):
    m = {"condition_id": "x"}
    assert guard.hours_to_close(m) is None


def test_filter_markets(guard):
    markets = [
        _market(hours_from_now=5, market_id="ok1"),
        _market(hours_from_now=-1, market_id="expired"),
        _market(hours_from_now=3, market_id="ok2"),
        {"condition_id": "nodate", "question": "No date"},
    ]
    passed, rejected = guard.filter_markets(markets)
    assert len(passed) == 2
    assert len(rejected) == 2
    rejected_ids = {r.market_id for r in rejected}
    assert "expired" in rejected_ids
    assert "nodate" in rejected_ids


def test_date_only_string(guard):
    """Date-only string (2026-03-20) should parse with T23:59:00."""
    m = _market(end_date="2099-12-31")
    h = guard.hours_to_close(m)
    assert h is not None and h > 0


def test_z_suffix_handled(guard):
    dt = datetime.now(timezone.utc) + timedelta(hours=5)
    iso = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    m = _market(end_date=iso)
    rej = guard.check(m)
    assert rej is None
