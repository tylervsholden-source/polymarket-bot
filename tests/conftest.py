"""
Shared test fixtures.

GATE 3 in strategies/arbitrage_engine.py (_evaluate_market) blocks trades during
historically bad ET hours (21-00) by reading the real wall clock via
`datetime.now(timezone.utc)`. That makes any test exercising _evaluate_market
flaky: it silently returns None whenever the suite happens to run during one
of those hours in the real world, independent of the scenario under test.

This fixture pins that module's `datetime.now()` to a fixed, known-good ET
hour for the duration of every test, so direction/execution-path tests assert
on the scenario they set up rather than on the clock.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

import strategies.arbitrage_engine as arbitrage_engine_module


class _FixedDatetime(datetime):
    """datetime.now() pinned to 15:00 UTC (~11:00 ET) — outside GATE 3's bad hours."""

    @classmethod
    def now(cls, tz=None):
        fixed = datetime(2026, 1, 1, 15, 0, 0, tzinfo=timezone.utc)
        return fixed.astimezone(tz) if tz is not None else fixed


@pytest.fixture(autouse=True)
def _pin_arbitrage_engine_clock(monkeypatch):
    monkeypatch.setattr(arbitrage_engine_module, "datetime", _FixedDatetime)
