"""Regression test: cycle_guard() must let exceptions propagate to its
caller instead of silently swallowing them.

agents/orchestrator.py's main loop and agents/resilience.py's infinite_loop
both wrap their entire cycle body in `async with cycle_guard(...)`, and both
rely on their OWN outer `except Exception as e: had_error = True` to detect
a failed cycle and feed HealthMonitor.record_cycle()/should_backoff(). Before
this fix, cycle_guard's `except Exception` (and `except asyncio.TimeoutError`)
branches only logged and returned normally, so any exception raised inside
the `async with` block never reached the caller's except clause — had_error
stayed False for every single failing cycle, HealthMonitor's error rate and
consecutive-error counters could never increment, should_backoff() always
returned 0, and get_health()'s status could never leave "HEALTHY" no matter
how many cycles actually failed.
"""
import asyncio

import pytest

from agents.resilience import HealthMonitor, cycle_guard


@pytest.mark.asyncio
async def test_cycle_guard_reraises_exception():
    with pytest.raises(ValueError):
        async with cycle_guard("test_cycle", timeout=5):
            raise ValueError("boom")


@pytest.mark.asyncio
async def test_cycle_guard_reraises_timeout():
    with pytest.raises(asyncio.TimeoutError):
        async with cycle_guard("test_cycle", timeout=0.01):
            await asyncio.sleep(1)


@pytest.mark.asyncio
async def test_orchestrator_style_loop_records_errors_via_cycle_guard():
    """Mirrors the exact try/except shape of Orchestrator.run()'s main loop."""
    monitor = HealthMonitor()

    for _ in range(4):
        had_error = False
        try:
            async with cycle_guard("main_cycle", timeout=5):
                raise RuntimeError("simulated cycle failure")
        except asyncio.CancelledError:
            raise
        except Exception:
            had_error = True
        monitor.record_cycle(10.0, had_error)

    health = monitor.get_health()
    assert monitor._consecutive_errors == 4
    assert health["status"] in ("DEGRADED", "CRITICAL")
    assert monitor.should_backoff() > 0
