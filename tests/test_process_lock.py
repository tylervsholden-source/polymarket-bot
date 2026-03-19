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
