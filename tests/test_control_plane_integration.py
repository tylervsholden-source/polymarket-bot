"""Integration tests — full control plane pipeline."""
import json
import time
import pytest
from datetime import datetime, timedelta, timezone

from control_plane.types import ApprovalState
from control_plane.process_lock import ProcessLock
from control_plane.approval_queue import ApprovalQueue
from control_plane.reentry_guard import ReentryGuard
from control_plane.expiry_guard import ExpiryGuard
from control_plane.live_gate import check_live_gate


@pytest.fixture
def env(tmp_path):
    """Full control plane environment."""
    ctrl = tmp_path / "control.json"
    ctrl.write_text(json.dumps({"live_trading": True}))

    readiness = tmp_path / "readiness.json"
    readiness.write_text(json.dumps({
        "verdict": "TINY_PILOT_CANDIDATE",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }))

    queue = ApprovalQueue(pending_file=str(tmp_path / "pending.json"))
    guard = ReentryGuard(cooldown_file=str(tmp_path / "cooldowns.json"))
    expiry = ExpiryGuard(min_hours=0.0, max_hours=24.0)
    lock = ProcessLock(lock_path=tmp_path / "bot.lock")

    return {
        "ctrl_file": str(ctrl),
        "readiness_file": str(readiness),
        "queue": queue,
        "guard": guard,
        "expiry": expiry,
        "lock": lock,
    }


def test_full_order_lifecycle(env):
    """Emir yasam dongusu: enqueue -> approve -> gate check -> execute."""
    queue = env["queue"]

    # 1. Sinyal: emir kuyruga eklenir
    oid = queue.enqueue({
        "market_id": "0xtest",
        "question": "Will BTC go up?",
        "direction": "YES",
        "amount": 10.0,
        "entry_price": 0.45,
    })
    assert len(queue.get_pending()) == 1

    # 2. Operator onaylar
    queue.approve(oid)
    assert len(queue.get_approved()) == 1

    # 3. LiveGate kontrolu
    dt = datetime.now(timezone.utc) + timedelta(hours=5)
    market = {
        "condition_id": "0xtest",
        "question": "Will BTC go up?",
        "end_date_iso": dt.isoformat(),
    }

    result = check_live_gate(
        control_file=env["ctrl_file"],
        readiness_file=env["readiness_file"],
        daily_loss_exceeded=False,
        open_position_count=0,
        max_open_positions=5,
        max_orders_per_hour=3,
        market_id="0xtest",
        reentry_guard=env["guard"],
        market=market,
        expiry_guard=env["expiry"],
        is_approved=True,
        available_capital=100.0,
        required_capital=10.0,
    )
    assert result.passed is True

    # 4. Emir verilir
    queue.mark_executed(oid)
    assert len(queue.get_approved()) == 0

    # 5. Market cooldown'a alinir
    env["guard"].mark_traded("0xtest")
    assert env["guard"].is_blocked("0xtest") is True


def test_blocked_order_lifecycle(env):
    """Engellenen emir: enqueue -> approve -> gate fails -> block."""
    queue = env["queue"]

    oid = queue.enqueue({
        "market_id": "0xblocked",
        "direction": "NO",
        "amount": 200.0,
    })
    queue.approve(oid)

    # Gate fails: insufficient capital
    result = check_live_gate(
        control_file=env["ctrl_file"],
        readiness_file=env["readiness_file"],
        available_capital=5.0,
        required_capital=200.0,
    )
    assert result.passed is False

    # Block the order
    queue.block_execution(oid, reason="Yetersiz sermaye")
    assert len(queue.get_approved()) == 0


def test_reentry_prevents_duplicate(env):
    """Cooldown'daki market LiveGate'i gecemez."""
    env["guard"].mark_closed("0xdup")

    result = check_live_gate(
        control_file=env["ctrl_file"],
        readiness_file=env["readiness_file"],
        market_id="0xdup",
        reentry_guard=env["guard"],
    )
    assert result.passed is False
    assert any("cooldown" in b for b in result.blockers)


def test_expired_market_prevented(env):
    """Expired market LiveGate'i gecemez."""
    market = {
        "condition_id": "0xexpired",
        "question": "Already ended?",
        "end_date_iso": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
    }
    result = check_live_gate(
        control_file=env["ctrl_file"],
        readiness_file=env["readiness_file"],
        market=market,
        expiry_guard=env["expiry"],
    )
    assert result.passed is False
    assert any("EXPIRED" in b for b in result.blockers)


def test_state_machine_illegal_transition(env):
    """Terminal state'ten gecis denenemez."""
    queue = env["queue"]
    oid = queue.enqueue({"market_id": "x"})
    queue.reject(oid)
    # Rejected -> Approved olmamali
    assert queue.approve(oid) is False
    # Rejected -> Executed olmamali (mark_executed returns None, check state unchanged)
    queue.mark_executed(oid)
    order = [o for o in queue.get_all() if o["id"] == oid][0]
    assert order["status"] == "rejected"
