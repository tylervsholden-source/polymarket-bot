"""Tests for control_plane.types."""
import pytest
from control_plane.types import (
    ApprovalState,
    LiveGateCheck,
    LiveGateResult,
    ExpiryRejection,
    ProcessLockInfo,
)


# --- ApprovalState ---

def test_approval_state_values():
    assert ApprovalState.PENDING.value == "pending"
    assert ApprovalState.APPROVED.value == "approved"
    assert ApprovalState.REJECTED.value == "rejected"
    assert ApprovalState.EXPIRED.value == "expired"
    assert ApprovalState.EXECUTED.value == "executed"
    assert ApprovalState.EXECUTION_BLOCKED.value == "execution_blocked"


def test_valid_transitions_from_pending():
    S = ApprovalState
    assert S.PENDING.can_transition_to(S.APPROVED)
    assert S.PENDING.can_transition_to(S.REJECTED)
    assert S.PENDING.can_transition_to(S.EXPIRED)
    assert not S.PENDING.can_transition_to(S.EXECUTED)


def test_valid_transitions_from_approved():
    S = ApprovalState
    assert S.APPROVED.can_transition_to(S.EXECUTED)
    assert S.APPROVED.can_transition_to(S.EXECUTION_BLOCKED)
    assert S.APPROVED.can_transition_to(S.EXPIRED)
    assert not S.APPROVED.can_transition_to(S.PENDING)


def test_terminal_states_no_transitions():
    S = ApprovalState
    for state in [S.REJECTED, S.EXPIRED, S.EXECUTED, S.EXECUTION_BLOCKED]:
        for target in S:
            assert not state.can_transition_to(target)


# --- LiveGateCheck ---

def test_live_gate_check_pass():
    c = LiveGateCheck(name="test", passed=True, reason="")
    assert c.passed
    assert c.name == "test"


def test_live_gate_check_fail():
    c = LiveGateCheck(name="capital", passed=False, reason="Yetersiz")
    assert not c.passed
    assert c.reason == "Yetersiz"


# --- LiveGateResult ---

def test_live_gate_result_all_pass():
    checks = [
        LiveGateCheck(name="a", passed=True),
        LiveGateCheck(name="b", passed=True),
    ]
    r = LiveGateResult(passed=True, checks=checks)
    assert r.passed
    assert r.blockers == []


def test_live_gate_result_with_blockers():
    checks = [
        LiveGateCheck(name="ok", passed=True),
        LiveGateCheck(name="fail1", passed=False, reason="R1"),
        LiveGateCheck(name="fail2", passed=False, reason="R2"),
    ]
    r = LiveGateResult(passed=False, checks=checks)
    assert not r.passed
    assert r.blockers == ["R1", "R2"]


def test_live_gate_result_to_dict():
    checks = [LiveGateCheck(name="x", passed=True)]
    r = LiveGateResult(passed=True, checks=checks)
    d = r.to_dict()
    assert d["passed"] is True
    assert "x" in d["checks"]
    assert d["checks"]["x"]["passed"] is True


# --- ExpiryRejection ---

def test_expiry_rejection():
    r = ExpiryRejection(market_id="abc", reason="EXPIRED", hours_to_close=-1.0)
    assert r.reason == "EXPIRED"
    assert r.hours_to_close == -1.0


# --- ProcessLockInfo ---

def test_process_lock_info():
    info = ProcessLockInfo(pid=123, lock_file="test.lock", is_current=True)
    assert info.pid == 123
    assert info.is_current
