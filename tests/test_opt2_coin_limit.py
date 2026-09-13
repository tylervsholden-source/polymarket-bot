"""
Regression test for OPT-2 (max 1 coin/period) in Orchestrator._limit_coins_per_period.

Bug: the live call site in `_cycle()` called
`self._limit_coins_per_period(all_signals, max_per_period=5)` — CLAUDE.md documents
OPT-2 as "Max 1 Coin/Period — COIN_LIMIT 2->1. Korelasyon %99, 2 coin = 2x risk 1x
bilgi", and even the function's own default parameter still reflected the older
v8 value of 2. The live call site allowed 5 coins into the same time slot at once
(5x the documented risk budget), traced to the same `9b5fd52` mega-commit that
silently loosened several other documented risk rules (daily stop-loss,
MAX_OPEN_POSITIONS, OPT-3, OPT-5) fixed in prior reviews.

This locks in that both the call site and the default now match OPT-2 (1).
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

from agents.orchestrator import Orchestrator


def _make_orchestrator() -> Orchestrator:
    orch = Orchestrator.__new__(Orchestrator)
    orch._last_loss_slots = set()
    orch._sim_trades = []
    orch.position_manager = SimpleNamespace(data={"positions": {}})
    return orch


def _signal(question: str, edge: float, direction: str = "NO") -> SimpleNamespace:
    return SimpleNamespace(market={"question": question}, edge=edge, direction=direction)


def test_default_max_per_period_is_one():
    sig = inspect.signature(Orchestrator._limit_coins_per_period)
    assert sig.parameters["max_per_period"].default == 1


def test_only_top_edge_signal_kept_per_slot_by_default():
    orch = _make_orchestrator()
    signals = [
        _signal("BTC Up or Down - March 22, 8:10AM-8:15AM", edge=0.10),
        _signal("ETH Up or Down - March 22, 8:10AM-8:15AM", edge=0.30),
        _signal("SOL Up or Down - March 22, 8:10AM-8:15AM", edge=0.20),
    ]

    filtered = orch._limit_coins_per_period(signals)

    assert len(filtered) == 1
    assert filtered[0].edge == 0.30


def test_second_coin_in_same_slot_dropped_when_one_already_open():
    orch = _make_orchestrator()
    orch.position_manager.data["positions"]["m1"] = {
        "question": "BTC Up or Down - March 22, 8:10AM-8:15AM"
    }
    signals = [_signal("ETH Up or Down - March 22, 8:10AM-8:15AM", edge=0.30)]

    filtered = orch._limit_coins_per_period(signals)

    assert filtered == []
