"""
Regression test: Orchestrator._readiness_clears_live() has its own,
independent copy of the readiness-freshness check that
control_plane/live_gate.py::_check_readiness() implements — and the 53rd
daily review's fix ("readiness gate, generated_utc alanı eksik/boşsa yaş
kontrolünü tamamen atlıyor", commit 6b6806e) only patched the
control_plane copy. The orchestrator's own copy, which is what actually
gates real order placement (`Orchestrator._is_live_trading()` →
`action="ORDER"` vs `"SIM_BUY"`), still has the original bug:

    generated_str = data.get("generated_utc", "")
    if generated_str:          # <- skipped entirely when missing/empty
        ... staleness check ...
    return True                # <- falls straight through to here

This is exactly the INC-2026-03-15-001 scenario the 53rd review's fix was
meant to close: a manually-edited or legacy `readiness_verdict.json` with
no (or an empty) `generated_utc` is treated as infinitely fresh instead of
untrusted, silently disabling the READINESS_MAX_AGE_HOURS freshness gate
in the one place that actually matters for live orders.
"""
from __future__ import annotations

import json
import os

from agents.orchestrator import Orchestrator

VERDICT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "readiness_verdict.json",
)


def _write_verdict(payload: dict | None):
    os.makedirs(os.path.dirname(VERDICT_PATH), exist_ok=True)
    if payload is None:
        if os.path.exists(VERDICT_PATH):
            os.remove(VERDICT_PATH)
        return
    with open(VERDICT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def _restore(original: str | None):
    if original is None:
        if os.path.exists(VERDICT_PATH):
            os.remove(VERDICT_PATH)
    else:
        with open(VERDICT_PATH, "w", encoding="utf-8") as f:
            f.write(original)


def _backup() -> str | None:
    if os.path.exists(VERDICT_PATH):
        with open(VERDICT_PATH, encoding="utf-8") as f:
            return f.read()
    return None


def test_missing_generated_utc_blocks_live_trading():
    """No generated_utc field at all -> gate must block, not pass forever."""
    original = _backup()
    try:
        _write_verdict({"verdict": "TINY_PILOT_CANDIDATE"})
        orch = Orchestrator.__new__(Orchestrator)
        assert orch._readiness_clears_live() is False
    finally:
        _restore(original)


def test_empty_generated_utc_blocks_live_trading():
    """generated_utc = "" -> gate must block, not pass forever."""
    original = _backup()
    try:
        _write_verdict({"verdict": "TINY_PILOT_CANDIDATE", "generated_utc": ""})
        orch = Orchestrator.__new__(Orchestrator)
        assert orch._readiness_clears_live() is False
    finally:
        _restore(original)


def test_fresh_generated_utc_still_clears():
    """Sanity check: a normal, fresh verdict must still clear the gate."""
    from datetime import datetime, timezone

    original = _backup()
    try:
        _write_verdict({
            "verdict": "TINY_PILOT_CANDIDATE",
            "generated_utc": datetime.now(timezone.utc).isoformat(),
        })
        orch = Orchestrator.__new__(Orchestrator)
        assert orch._readiness_clears_live() is True
    finally:
        _restore(original)
