"""Tests for control_plane.live_gate."""
import json
import os
import time
import pytest
from datetime import datetime, timezone
from control_plane.live_gate import check_live_gate
from control_plane.reentry_guard import ReentryGuard
from control_plane.expiry_guard import ExpiryGuard


@pytest.fixture
def ctrl_file(tmp_path):
    f = tmp_path / "control.json"
    f.write_text(json.dumps({"live_trading": True}))
    return str(f)


@pytest.fixture
def readiness_file(tmp_path):
    f = tmp_path / "readiness.json"
    f.write_text(json.dumps({
        "verdict": "TINY_PILOT_CANDIDATE",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }))
    return str(f)


def test_all_pass(ctrl_file, readiness_file):
    result = check_live_gate(
        control_file=ctrl_file,
        readiness_file=readiness_file,
        daily_loss_exceeded=False,
        open_position_count=0,
        max_open_positions=5,
        max_orders_per_hour=3,
        is_approved=True,
        available_capital=100.0,
        required_capital=10.0,
    )
    assert result.passed is True
    assert len(result.blockers) == 0
    assert len(result.checks) == 11


def test_live_trading_false_blocks(tmp_path, readiness_file):
    cf = tmp_path / "ctrl.json"
    cf.write_text(json.dumps({"live_trading": False}))
    result = check_live_gate(
        control_file=str(cf),
        readiness_file=readiness_file,
    )
    assert result.passed is False
    assert any("live_trading" in b for b in result.blockers)


def test_readiness_missing_blocks(ctrl_file, tmp_path):
    result = check_live_gate(
        control_file=ctrl_file,
        readiness_file=str(tmp_path / "nonexistent.json"),
    )
    assert result.passed is False
    assert any("bulunamadı" in b or "bulunamad" in b for b in result.blockers)


def test_readiness_wrong_verdict_blocks(ctrl_file, tmp_path):
    f = tmp_path / "readiness.json"
    f.write_text(json.dumps({
        "verdict": "NOT_READY",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }))
    result = check_live_gate(
        control_file=ctrl_file,
        readiness_file=str(f),
    )
    assert result.passed is False


def test_readiness_missing_generated_utc_blocks(ctrl_file, tmp_path):
    """Bir readiness_verdict.json'da generated_utc alanı yoksa (manuel yazım,
    eski format, bozuk dosya), yaş kontrolü atlanmamalı — fail-closed olmalı.
    """
    f = tmp_path / "readiness.json"
    f.write_text(json.dumps({"verdict": "TINY_PILOT_CANDIDATE"}))
    result = check_live_gate(
        control_file=ctrl_file,
        readiness_file=str(f),
    )
    assert result.passed is False
    assert any("generated_utc" in b for b in result.blockers)


def test_readiness_empty_generated_utc_blocks(ctrl_file, tmp_path):
    f = tmp_path / "readiness.json"
    f.write_text(json.dumps({"verdict": "TINY_PILOT_CANDIDATE", "generated_utc": ""}))
    result = check_live_gate(
        control_file=ctrl_file,
        readiness_file=str(f),
    )
    assert result.passed is False
    assert any("generated_utc" in b for b in result.blockers)


def test_daily_stop_blocks(ctrl_file, readiness_file):
    result = check_live_gate(
        control_file=ctrl_file,
        readiness_file=readiness_file,
        daily_loss_exceeded=True,
    )
    assert result.passed is False
    assert any("stop-loss" in b for b in result.blockers)


def test_position_count_blocks(ctrl_file, readiness_file):
    result = check_live_gate(
        control_file=ctrl_file,
        readiness_file=readiness_file,
        open_position_count=5,
        max_open_positions=5,
    )
    assert result.passed is False


def test_rate_limit_blocks(ctrl_file, readiness_file):
    now = time.time()
    timestamps = [now - 10, now - 20, now - 30]  # 3 recent orders
    result = check_live_gate(
        control_file=ctrl_file,
        readiness_file=readiness_file,
        order_timestamps=timestamps,
        max_orders_per_hour=3,
    )
    assert result.passed is False


def test_reentry_guard_blocks(ctrl_file, readiness_file, tmp_path):
    guard = ReentryGuard(cooldown_file=str(tmp_path / "cd.json"))
    guard.mark_closed("blocked_market")
    result = check_live_gate(
        control_file=ctrl_file,
        readiness_file=readiness_file,
        market_id="blocked_market",
        reentry_guard=guard,
    )
    assert result.passed is False


def test_expiry_guard_blocks(ctrl_file, readiness_file):
    eg = ExpiryGuard(min_hours=0.0, max_hours=24.0)
    market = {"condition_id": "x", "question": "No date"}  # NO_END_DATE
    result = check_live_gate(
        control_file=ctrl_file,
        readiness_file=readiness_file,
        market=market,
        expiry_guard=eg,
    )
    assert result.passed is False


def test_capital_insufficient_blocks(ctrl_file, readiness_file):
    result = check_live_gate(
        control_file=ctrl_file,
        readiness_file=readiness_file,
        available_capital=5.0,
        required_capital=10.0,
    )
    assert result.passed is False


def test_approval_false_blocks(ctrl_file, readiness_file):
    result = check_live_gate(
        control_file=ctrl_file,
        readiness_file=readiness_file,
        is_approved=False,
    )
    assert result.passed is False


def test_to_dict_structure(ctrl_file, readiness_file):
    result = check_live_gate(
        control_file=ctrl_file,
        readiness_file=readiness_file,
    )
    d = result.to_dict()
    assert "passed" in d
    assert "checked_at" in d
    assert "blockers" in d
    assert "checks" in d
    assert isinstance(d["checks"], dict)


def test_multiple_blockers(tmp_path):
    cf = tmp_path / "ctrl.json"
    cf.write_text(json.dumps({"live_trading": False}))
    result = check_live_gate(
        control_file=str(cf),
        readiness_file=str(tmp_path / "none.json"),
        daily_loss_exceeded=True,
        open_position_count=10,
        max_open_positions=5,
    )
    assert result.passed is False
    assert len(result.blockers) >= 4
