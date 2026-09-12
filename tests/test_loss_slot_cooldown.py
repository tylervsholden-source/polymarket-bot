"""
Regression test for OPT-6 (loss slot cooldown) in Orchestrator._limit_coins_per_period.

Bug: `_is_adjacent_to_loss_slot()` was fully implemented but never called anywhere
— the enforcement point in `_limit_coins_per_period` had been replaced with a
"LOSS_COOLDOWN kaldırıldı" (removed) comment, while `_last_loss_slots` bookkeeping
(populated/cleared elsewhere in the cycle) kept running with no effect. CLAUDE.md
documents OPT-6 as active ("Kayıp olan slot'tan sonraki slot'u atla"); the dead
code silently let every post-loss slot trade through (dead-cat-bounce risk).
"""
from __future__ import annotations

from types import SimpleNamespace

from agents.orchestrator import Orchestrator


def _make_orchestrator(loss_slots: set[str]) -> Orchestrator:
    orch = Orchestrator.__new__(Orchestrator)
    orch._last_loss_slots = loss_slots
    orch._sim_trades = []
    orch.position_manager = SimpleNamespace(data={"positions": {}})
    return orch


def _signal(question: str, edge: float, direction: str = "NO") -> SimpleNamespace:
    return SimpleNamespace(market={"question": question}, edge=edge, direction=direction)


def test_slot_immediately_after_loss_slot_is_skipped():
    # Loss recorded in 8:05AM-8:10AM -> next slot 8:10AM-8:15AM must be blocked.
    orch = _make_orchestrator({"8:05AM-8:10AM"})
    signals = [_signal("BTC Up or Down - March 22, 8:10AM-8:15AM", edge=0.30)]

    filtered = orch._limit_coins_per_period(signals, max_per_period=5)

    assert filtered == []


def test_non_adjacent_slot_passes_through():
    orch = _make_orchestrator({"8:05AM-8:10AM"})
    signals = [_signal("BTC Up or Down - March 22, 9:00AM-9:05AM", edge=0.30)]

    filtered = orch._limit_coins_per_period(signals, max_per_period=5)

    assert filtered == signals


def test_no_loss_slots_all_pass_through():
    orch = _make_orchestrator(set())
    signals = [_signal("BTC Up or Down - March 22, 8:10AM-8:15AM", edge=0.30)]

    filtered = orch._limit_coins_per_period(signals, max_per_period=5)

    assert filtered == signals
