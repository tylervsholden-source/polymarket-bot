"""Shared pytest fixtures."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest


@pytest.fixture(autouse=True)
def _pin_et_hour_gate(monkeypatch):
    """Pin ArbitrageEngine's bad-hour gate to a neutral hour so tests are
    deterministic regardless of the real wall-clock time they run at.
    Without this, tests that reach GATE 3 in _evaluate_market randomly
    fail whenever the suite happens to run during 21:00-00:59 ET.
    """
    try:
        import strategies.arbitrage_engine as ae
        monkeypatch.setattr(ae, "_get_current_et_hour", lambda: 12)
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def _pin_position_manager_et_clock(monkeypatch):
    """Pin PositionManager.update_positions()'s question-derived TIME_EXPIRED
    check to a neutral ET wall-clock time. That check compares the real
    current time-of-day against each position's parsed end time-of-day
    (e.g. "1:00PM-1:05PM ET") over a rolling 12-hour window — same
    wall-clock-flakiness class as the ET-hour gate above (see #9). Any test
    whose fixture position ends up inside that 12-hour window at whatever
    moment the suite happens to run intermittently skips the orderbook
    valuation path it's meant to exercise. Pinned to 09:00 ET, matched by
    tests that intentionally build an "expired" position around this hour.
    """
    try:
        import core.position_manager as pm
        monkeypatch.setattr(
            pm, "_now_et",
            lambda: datetime(2026, 1, 1, 9, 0, tzinfo=ZoneInfo("America/New_York")),
        )
    except ImportError:
        pass
