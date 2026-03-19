"""Tests for control_plane.reentry_guard."""
import json
import pytest
from control_plane.reentry_guard import ReentryGuard


@pytest.fixture
def guard(tmp_path):
    f = str(tmp_path / "cooldowns.json")
    return ReentryGuard(cooldown_file=f, cooldown_hours=24.0)


def test_not_blocked_initially(guard):
    assert guard.is_blocked("market_1") is False


def test_blocked_after_mark_closed(guard):
    guard.mark_closed("market_1")
    assert guard.is_blocked("market_1") is True


def test_blocked_after_mark_traded(guard):
    guard.mark_traded("market_2")
    assert guard.is_blocked("market_2") is True


def test_clear_removes_block(guard):
    guard.mark_closed("market_1")
    guard.clear("market_1")
    assert guard.is_blocked("market_1") is False


def test_blocked_count(guard):
    guard.mark_closed("a")
    guard.mark_closed("b")
    guard.mark_traded("c")
    assert guard.blocked_count() == 3


def test_blocked_markets_returns_set(guard):
    guard.mark_closed("x")
    guard.mark_traded("y")
    result = guard.blocked_markets()
    assert isinstance(result, set)
    assert "x" in result
    assert "y" in result


def test_persistence(tmp_path):
    f = str(tmp_path / "cooldowns.json")
    g1 = ReentryGuard(cooldown_file=f, cooldown_hours=24.0)
    g1.mark_closed("persist_market")

    g2 = ReentryGuard(cooldown_file=f, cooldown_hours=24.0)
    assert g2.is_blocked("persist_market") is True


def test_cleanup_stale(tmp_path):
    import time
    f = str(tmp_path / "cooldowns.json")
    # Write a stale entry
    with open(f, "w") as fp:
        json.dump({"old_market": time.time() - 100000}, fp)  # > 24h ago
    guard = ReentryGuard(cooldown_file=f, cooldown_hours=24.0)
    removed = guard.cleanup_stale()
    assert removed == 1
    assert guard.is_blocked("old_market") is False
