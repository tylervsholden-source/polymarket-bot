import os
import json
import pytest
from pathlib import Path
from unittest.mock import patch


@pytest.fixture(autouse=True)
def tmp_data_dir(tmp_path, monkeypatch):
    """Her test için izole data dizini kullan."""
    import core.position_manager as pm_module
    monkeypatch.setattr(pm_module, "DATA_FILE", tmp_path / "positions.json")
    monkeypatch.setattr(pm_module, "LOCK_FILE", tmp_path / "positions.lock")
    return tmp_path


@pytest.fixture
def pm():
    from core.position_manager import PositionManager
    with patch.dict(os.environ, {"INITIAL_CAPITAL": "1000", "MAX_POSITION_PCT": "0.20"}):
        return PositionManager()


def test_initial_capital(pm):
    assert pm.data["capital"] == 1000.0


def test_available_capital_no_positions(pm):
    assert pm.available_capital() == 1000.0


def test_add_position_reduces_capital(pm):
    order = {"order_id": "TEST-1", "outcome": "YES", "amount": 200.0, "price": 0.5, "status": "LIVE"}
    pm.add_position("market-1", order, "Test Market")
    assert pm.available_capital() == 800.0


def test_has_position(pm):
    order = {"order_id": "TEST-1", "outcome": "YES", "amount": 100.0, "price": 0.5, "status": "LIVE"}
    pm.add_position("market-1", order, "Test Market")
    assert pm.has_position("market-1") is True
    assert pm.has_position("market-2") is False


def test_open_position_count(pm):
    assert pm.open_position_count() == 0
    order = {"order_id": "TEST-1", "outcome": "YES", "amount": 100.0, "price": 0.5, "status": "LIVE"}
    pm.add_position("market-1", order, "Test")
    assert pm.open_position_count() == 1


def test_daily_stop_loss_not_triggered(pm):
    assert pm.daily_loss_exceeded(0.15) is False


def test_daily_stop_loss_triggered(pm):
    from datetime import datetime, timezone
    pm.data["daily"]["date"] = str(datetime.now(timezone.utc).date())
    pm.data["daily"]["pnl"] = -160.0  # -%16
    assert pm.daily_loss_exceeded(0.15) is True


def test_daily_stop_loss_resets_on_new_day(pm):
    pm.data["daily"] = {"date": "2020-01-01", "pnl": -500.0}
    # Farklı gün → reset → kayıp yok
    assert pm.daily_loss_exceeded(0.15) is False
