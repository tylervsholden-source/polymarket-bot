"""Tests for control_plane.approval_queue."""
import json
import os
import pytest
from control_plane.approval_queue import ApprovalQueue
from control_plane.types import ApprovalState


@pytest.fixture
def queue(tmp_path):
    f = str(tmp_path / "pending.json")
    return ApprovalQueue(pending_file=f)


def test_enqueue_returns_id(queue):
    oid = queue.enqueue({"market_id": "abc", "amount": 10})
    assert isinstance(oid, str) and len(oid) > 0


def test_enqueue_state_is_pending(queue):
    oid = queue.enqueue({"market_id": "abc"})
    orders = queue.get_pending()
    assert len(orders) == 1
    assert orders[0]["status"] == "pending"
    assert orders[0]["id"] == oid


def test_approve_transitions_to_approved(queue):
    oid = queue.enqueue({"market_id": "abc"})
    ok = queue.approve(oid)
    assert ok is True
    approved = queue.get_approved()
    assert len(approved) == 1
    assert approved[0]["status"] == "approved"


def test_reject_transitions_to_rejected(queue):
    oid = queue.enqueue({"market_id": "abc"})
    ok = queue.reject(oid)
    assert ok is True
    pending = queue.get_pending()
    assert len(pending) == 0


def test_mark_executed_after_approve(queue):
    oid = queue.enqueue({"market_id": "abc"})
    queue.approve(oid)
    # mark_executed returns None (not bool), just verify no error
    queue.mark_executed(oid)
    # Verify state changed
    all_orders = queue.get_all()
    order = [o for o in all_orders if o["id"] == oid][0]
    assert order["status"] == "executed"


def test_block_execution_after_approve(queue):
    oid = queue.enqueue({"market_id": "abc"})
    queue.approve(oid)
    ok = queue.block_execution(oid, reason="LiveGate failed")
    assert ok is True


def test_cannot_approve_rejected(queue):
    oid = queue.enqueue({"market_id": "abc"})
    queue.reject(oid)
    ok = queue.approve(oid)
    assert ok is False


def test_cannot_execute_pending(queue):
    """PENDING -> EXECUTED is invalid, transition should fail."""
    oid = queue.enqueue({"market_id": "abc"})
    # mark_executed delegates to transition which returns bool,
    # but mark_executed returns None. Check state didn't change.
    queue.mark_executed(oid)
    order = [o for o in queue.get_all() if o["id"] == oid][0]
    assert order["status"] == "pending"  # Should remain pending


def test_state_history_tracked(queue):
    oid = queue.enqueue({"market_id": "abc"})
    queue.approve(oid)
    queue.mark_executed(oid)
    all_orders = queue.get_all()
    order = [o for o in all_orders if o["id"] == oid][0]
    history = order.get("state_history", [])
    states = [h["state"] for h in history]
    assert "pending" in states
    assert "approved" in states
    assert "executed" in states


def test_cleanup_expired(queue):
    oid = queue.enqueue({"market_id": "abc"})
    # Manually set enqueued_at to far past to force expiry
    orders = queue._load()
    for o in orders:
        if o["id"] == oid:
            o["enqueued_at"] = 0  # epoch 0 = 1970
    queue._save(orders)
    removed = queue.cleanup_expired()
    assert removed >= 1
    assert len(queue.get_pending()) == 0


def test_persistence(tmp_path):
    f = str(tmp_path / "persist.json")
    q1 = ApprovalQueue(pending_file=f)
    oid = q1.enqueue({"market_id": "xyz"})

    q2 = ApprovalQueue(pending_file=f)
    pending = q2.get_pending()
    assert len(pending) == 1
    assert pending[0]["id"] == oid
