"""
Regression test: PositionManager's daily -15% stop-loss PnL bucket must not
lose a trade's PnL across the UTC midnight boundary.

Bug: `_close_position()` / `_close_position_neutral()` mutated
`self.data["daily"]["pnl"]` directly, while only `daily_loss_exceeded()`
checked/reset `self.data["daily"]["date"]` against `today`. Since
`update_positions()` (which calls `_close_position()`) runs at the top of
every orchestrator cycle — before `daily_loss_exceeded()` is next called
that cycle — a position resolving right after midnight UTC had its PnL
added to the PREVIOUS day's stale bucket. The next `daily_loss_exceeded()`
call then saw the date mismatch and reset `daily` to
`{"date": <new day>, "pnl": 0}`, silently erasing that trade's PnL from the
day's running total. This can only ever under-report the day's real loss,
letting the bot keep trading through a breached -15% daily stop — CLAUDE.md's
explicitly non-negotiable "Günlük stop-loss: -%15 → bot o gün durur" rule.

Fix: `_close_position()` / `_close_position_neutral()` and
`daily_loss_exceeded()` now share `_roll_daily_if_needed()`, called before
either one touches `daily["pnl"]` — whichever runs first across the
boundary performs the reset, so a closing trade's PnL always lands in the
correct (already-rolled-over) day's bucket instead of being discarded.
"""
from __future__ import annotations

from datetime import datetime

import pytest


class _FrozenDatetime(datetime):
    _frozen_now: datetime

    @classmethod
    def now(cls, tz=None):
        return cls._frozen_now.astimezone(tz) if tz else cls._frozen_now


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    import core.position_manager as pm_mod
    monkeypatch.setattr(pm_mod, "DATA_FILE", tmp_path / "positions.json")
    monkeypatch.setenv("INITIAL_CAPITAL", "1000.0")
    return pm_mod


def _freeze(monkeypatch, pm_mod, iso: str):
    _FrozenDatetime._frozen_now = datetime.fromisoformat(iso)
    monkeypatch.setattr(pm_mod, "datetime", _FrozenDatetime)


def _make_pm(pm_mod, capital: float, daily_date: str, daily_pnl: float):
    pm = pm_mod.PositionManager()
    pm.data["capital"] = capital
    pm.data["daily"] = {"date": daily_date, "pnl": daily_pnl}
    return pm


def _add_open_position(pm, market_id="mkt1", amount=100.0, entry_price=0.50):
    pm.data["positions"][market_id] = {
        "order_id": f"o-{market_id}", "question": "BTC up?",
        "outcome": "YES", "amount": amount, "entry_price": entry_price, "status": "LIVE",
    }


def test_close_after_midnight_starts_new_day_bucket(monkeypatch, tmp_data):
    pm_mod = tmp_data
    # 09-14 ended with capital=900 after a -100 loss (day_start_capital=1000).
    pm = _make_pm(pm_mod, capital=900.0, daily_date="2026-09-14", daily_pnl=-100.0)
    _add_open_position(pm)

    # It's now 00:01 UTC on 09-15 — a new trading day — when this position
    # resolves for a $60 loss (shares=200, payout=200*0.20=40, pnl=40-100=-60).
    _freeze(monkeypatch, pm_mod, "2026-09-15T00:01:00+00:00")
    pm._close_position("mkt1", 0.20)

    assert pm.data["daily"]["date"] == "2026-09-15", (
        "closing a position after the UTC day boundary must roll the daily "
        "bucket over to the new date"
    )
    assert pm.data["daily"]["pnl"] == pytest.approx(-60.0), (
        f"the new day's PnL must start from this trade's -60, got "
        f"{pm.data['daily']['pnl']} — carrying it into the stale 09-14 "
        "bucket would erase it on the next rollover check"
    )
    assert pm.data["capital"] == pytest.approx(840.0)


def test_pnl_not_erased_by_later_daily_loss_exceeded_check(monkeypatch, tmp_data):
    """The exact bug scenario end-to-end: a position closes just after
    midnight, then the same cycle's daily_loss_exceeded() check runs.
    Pre-fix, the close left `daily["date"]` stale, so this check's own
    rollover wiped the -60 PnL it had just recorded (reset to 0) instead of
    reflecting it."""
    pm_mod = tmp_data
    pm = _make_pm(pm_mod, capital=900.0, daily_date="2026-09-14", daily_pnl=-100.0)
    _add_open_position(pm)

    _freeze(monkeypatch, pm_mod, "2026-09-15T00:01:00+00:00")
    pm._close_position("mkt1", 0.20)  # -60 pnl, capital -> 840

    exceeded = pm.daily_loss_exceeded(0.15)

    assert pm.data["daily"]["pnl"] == pytest.approx(-60.0), (
        "daily_loss_exceeded() must not silently reset/erase the PnL a "
        "same-day close already recorded"
    )
    # day_start_capital = 840 - (-60) = 900; loss_pct = 60/900 = 6.7% < 15%
    assert exceeded is False


def test_neutral_close_after_midnight_also_rolls_bucket(monkeypatch, tmp_data):
    """_close_position_neutral() (unfilled/refunded order, pnl=0) must roll
    the daily bucket too, so the date stays current even when no PnL moves."""
    pm_mod = tmp_data
    pm = _make_pm(pm_mod, capital=900.0, daily_date="2026-09-14", daily_pnl=-100.0)
    _add_open_position(pm)

    _freeze(monkeypatch, pm_mod, "2026-09-15T00:01:00+00:00")
    pm._close_position_neutral("mkt1")

    assert pm.data["daily"] == {"date": "2026-09-15", "pnl": 0}
