"""
Regression test for the live-order ExpiryGuard wiring in agents/orchestrator.py.

Bug: both live-gate call sites (the direct-order path in Orchestrator._cycle
and the approval-queue path in Orchestrator._execute_approved_orders) only
passed `market`/`expiry_guard` into check_live_gate() when the market's
end-date string contained "T":

    market={"condition_id": market_id, "end_date_iso": end_iso} if "T" in end_iso else None,
    expiry_guard=self._expiry_guard if "T" in end_iso else None,

Gamma's `endDateIso` field — the only source `core/polymarket_client.py`'s
_normalize_markets()/`m["end_date_iso"]` ever populates for real markets —
is date-only ("2026-03-16", no "T"), exactly as documented at
core/polymarket_client.py:275-276 ("endDateIso is date-only ... don't use
for time comparison") and agents/orchestrator.py's own `_hours_to_close()`
("Prefer endDate (full timestamp) over endDateIso (date-only)"). So for
every real market flowing through the live order path, `"T" in end_iso`
was always False, `market`/`expiry_guard` were always None, and
control_plane/live_gate.py's check #8 ("expiry_guard") silently reported
passed=True ("Kontrol atlandı") instead of ever calling
ExpiryGuard.check() — the exact final-layer defense
control_plane/expiry_guard.py's docstring says exists to stop orders on
expired markets (INC-2026-03-15-001).

ExpiryGuard.hours_to_close() already pads a 10-char date-only string to
end-of-day, so it does not need a full timestamp to work correctly — the
gate only needed to stop being skipped. The fix drops the "T in end_iso"
condition in favor of "end_iso truthy" (and prefers the full `endDate`
timestamp when Gamma provides one, matching `_hours_to_close()`).
"""
from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta, timezone

from agents.orchestrator import Orchestrator
from control_plane.expiry_guard import ExpiryGuard
from control_plane.live_gate import check_live_gate


def _ctrl_file(tmp_path):
    f = tmp_path / "control.json"
    f.write_text(json.dumps({"live_trading": True}))
    return str(f)


def _readiness_file(tmp_path):
    f = tmp_path / "readiness.json"
    f.write_text(json.dumps({
        "verdict": "TINY_PILOT_CANDIDATE",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }))
    return str(f)


def test_orchestrator_source_no_longer_gates_expiry_guard_on_t_in_end_iso():
    """Neither live-gate call site may key expiry_guard wiring off "T in ...".

    A regex/substring check is used (rather than exercising the full async
    cycle, which needs a live exchange feed) because the bug is a pure
    wiring mistake in _cycle/_execute_approved_orders, not in ExpiryGuard
    or check_live_gate themselves (both are already covered and correct in
    isolation — see tests/test_expiry_guard.py, tests/test_live_gate.py).
    """
    cycle_src = inspect.getsource(Orchestrator._cycle)
    exec_src = inspect.getsource(Orchestrator._execute_approved_orders)

    assert '"T" in end_iso' not in cycle_src
    assert '"T" in order_req.get("end_date_iso"' not in exec_src

    # Direct-order path: must prefer the full timestamp and fall back to
    # the (date-only) end_date_iso, gating on truthiness only.
    assert 'end_iso = market.get("endDate") or market.get("end_date_iso"' in cycle_src
    assert 'if end_iso else None' in cycle_src

    # Approval-queue path: must gate on truthiness only.
    assert 'if _order_end_iso else None' in exec_src


def test_date_only_expired_market_is_actually_rejected_end_to_end(tmp_path):
    """Simulates the real Gamma shape (date-only end_date_iso) for an
    already-expired market and proves the fixed wiring rejects it, where
    the old "T in end_iso" wiring would have silently let it through.
    """
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    # Exactly what _normalize_markets() puts on a real market dict: no "T".
    market = {"condition_id": "0xabc", "end_date_iso": yesterday}
    end_iso = market.get("end_date_iso", "")
    assert "T" not in end_iso  # sanity: matches real Gamma shape

    guard = ExpiryGuard(min_hours=0.0, max_hours=24.0)
    common = dict(
        control_file=_ctrl_file(tmp_path),
        readiness_file=_readiness_file(tmp_path),
        daily_loss_exceeded=False,
        open_position_count=0,
        max_open_positions=5,
        max_orders_per_hour=3,
        market_id="0xabc",
        is_approved=True,
        available_capital=100.0,
        required_capital=10.0,
    )

    # OLD (buggy) wiring: gate is skipped for a date-only end date.
    buggy_result = check_live_gate(
        market={"condition_id": "0xabc", "end_date_iso": end_iso} if "T" in end_iso else None,
        expiry_guard=guard if "T" in end_iso else None,
        **common,
    )
    expiry_check = next(c for c in buggy_result.checks if c.name == "expiry_guard")
    assert expiry_check.passed is True
    assert expiry_check.reason == "Kontrol atlandı"  # bug: check never ran

    # NEW (fixed) wiring: expired date-only market is caught and blocked.
    fixed_result = check_live_gate(
        market={"condition_id": "0xabc", "end_date_iso": end_iso} if end_iso else None,
        expiry_guard=guard if end_iso else None,
        **common,
    )
    fixed_expiry_check = next(c for c in fixed_result.checks if c.name == "expiry_guard")
    assert fixed_expiry_check.passed is False
    assert "EXPIRED" in fixed_expiry_check.reason
    assert fixed_result.passed is False
    assert any("EXPIRED" in b for b in fixed_result.blockers)
