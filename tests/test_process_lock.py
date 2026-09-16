"""Tests for control_plane.process_lock."""
import os
import pytest
from pathlib import Path
from control_plane.process_lock import ProcessLock


@pytest.fixture
def tmp_lock(tmp_path):
    return ProcessLock(lock_path=tmp_path / "test.lock")


def test_acquire_creates_lock_file(tmp_lock):
    info = tmp_lock.acquire()
    assert info.pid == os.getpid()
    assert info.is_current is True
    assert Path(tmp_lock._path).exists()


def test_is_mine_after_acquire(tmp_lock):
    tmp_lock.acquire()
    assert tmp_lock.is_mine() is True


def test_is_held_after_acquire(tmp_lock):
    tmp_lock.acquire()
    assert tmp_lock.is_held() is True


def test_release_removes_file(tmp_lock):
    tmp_lock.acquire()
    tmp_lock.release()
    assert not Path(tmp_lock._path).exists()
    assert tmp_lock.is_held() is False


def test_is_held_false_when_no_file(tmp_lock):
    assert tmp_lock.is_held() is False


def test_holder_pid_none_when_no_file(tmp_lock):
    assert tmp_lock.holder_pid() is None


def test_info_no_lock(tmp_lock):
    info = tmp_lock.info()
    assert info.pid == 0
    assert info.is_current is False


def test_stale_lock_cleaned_up(tmp_path):
    """Stale lock (dead PID) should be cleaned and re-acquired."""
    lock_path = tmp_path / "stale.lock"
    lock_path.write_text("999999999")  # Dead PID
    lock = ProcessLock(lock_path=lock_path)
    info = lock.acquire()
    assert info.pid == os.getpid()


def test_double_release_idempotent(tmp_lock):
    tmp_lock.acquire()
    tmp_lock.release()
    tmp_lock.release()  # Should not raise


def test_acquire_refuses_when_old_process_cannot_be_killed(tmp_path, monkeypatch):
    """59th daily review: if the previous holder is alive and the kill attempt
    doesn't actually stop it, acquire() must refuse (sys.exit(1)) instead of
    silently stealing the lock file — otherwise both processes believe
    is_mine() is True (an in-memory flag, never re-checked against the file)
    and both go on to place real orders, the exact INC-2026-03-15-001
    scenario this lock exists to prevent.
    """
    lock_path = tmp_path / "unkillable.lock"
    lock_path.write_text("424242")  # arbitrary "other" PID
    lock = ProcessLock(lock_path=lock_path)

    # Simulate: the other PID is alive and stays alive no matter what kill
    # signal we send (e.g. os.kill silently no-ops in a sandboxed/foreign
    # PID-namespace environment, or the kill genuinely fails).
    monkeypatch.setattr(ProcessLock, "_pid_alive", lambda self, pid: True)
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)

    with pytest.raises(SystemExit) as exc_info:
        lock.acquire()
    assert exc_info.value.code == 1

    # Lock file must still point at the other (unkilled) PID — never stolen.
    assert lock_path.read_text().strip() == "424242"
    assert lock.is_mine() is False


def test_acquire_succeeds_once_old_process_actually_dies(tmp_path, monkeypatch):
    """Kill attempt that actually works should still let acquire() succeed."""
    lock_path = tmp_path / "killable.lock"
    lock_path.write_text("424243")
    lock = ProcessLock(lock_path=lock_path)

    state = {"alive": True}
    monkeypatch.setattr(ProcessLock, "_pid_alive", lambda self, pid: state["alive"])

    def _fake_kill(pid, sig):
        state["alive"] = False  # signal "worked"

    monkeypatch.setattr(os, "kill", _fake_kill)

    info = lock.acquire()
    assert info.pid == os.getpid()
    assert lock.is_mine() is True
    assert lock_path.read_text().strip() == str(os.getpid())
