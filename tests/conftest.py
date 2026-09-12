"""Shared pytest fixtures.

Freezes ArbitrageEngine's wall-clock read so time-of-day gates (e.g. GATE 3
in _evaluate_market, which blocks ALL trading during ET hours 21-00 based on
historical win-rate data) don't make the suite pass or fail depending on
what time of day the tests happen to run.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

# 2026-03-16 16:00 UTC = 12:00 ET (EDT) — outside the {21, 22, 23, 0} bad-hour
# block, and consistent with the "March 16" dates already used in test fixtures.
_FROZEN_NOW = datetime(2026, 3, 16, 16, 0, 0, tzinfo=timezone.utc)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _FROZEN_NOW.astimezone(tz) if tz else _FROZEN_NOW


@pytest.fixture(autouse=True)
def _freeze_arbitrage_engine_clock(monkeypatch):
    monkeypatch.setattr("strategies.arbitrage_engine.datetime", _FrozenDatetime)
