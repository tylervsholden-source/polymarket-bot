"""
Regression test: the per-cycle directional risk budget ("TOPLAM RİSK
LİMİTİ") used the raw, un-floored real CLOB balance as its capital basis
in sim/paper mode, while trade *sizing* on the very same cycle used the
SIM_CAPITAL-floored `capital` — a basis mismatch between the two.

Background: `_cycle()` computes a SIM_CAPITAL floor for `capital` (used by
Kelly/signal sizing) right after fetching `available_capital()`:

    capital = self.position_manager.available_capital()
    # Sim modda sanal sermaye kullan (gerçek sermaye $0.75 ile trade açılamaz)
    if self._is_simulation_running() and not self._is_live_trading():
        capital = max(capital, float(os.getenv("SIM_CAPITAL", 100)))

But the TOPLAM RİSK LİMİTİ block further down reads
`total_capital = self.position_manager.data.get("capital", capital)` —
`position_manager.data["capital"]` is kept pinned to the real (possibly
tiny) CLOB wallet balance by `_sync_real_balance()`
("CLOB bakiyesi TEK GERÇEK KAYNAK", called unconditionally every cycle)
regardless of sim/live mode — and never applied the same SIM_CAPITAL floor
before calling `compute_cycle_risk_budget(total_capital, directional_locked)`.

Concrete failure scenario: real CLOB balance = $0.75 (a tiny pilot wallet),
simulation_running=True, live_trading=False, SIM_CAPITAL=100 (default):
    buggy ceiling   = compute_cycle_risk_budget(0.75, 0)  = min(0.75*.18,20) = $0.135
    intended ceiling = compute_cycle_risk_budget(100, 0)  = min(100*.18,20) = $18.00
— a ~133x-tighter budget than the bankroll the bot is actually sizing sim
trades against. After one ~$4 sim trade carries into the next cycle,
directional_locked=$4 makes the buggy budget clamp to exactly $0.0,
blocking every further directional sim trade until that position resolves.

Fix: apply the identical SIM_CAPITAL floor to `total_capital` before it
feeds compute_cycle_risk_budget(), mirroring the floor already applied to
`capital` just above.
"""
from __future__ import annotations

import inspect

from agents.orchestrator import Orchestrator, compute_cycle_risk_budget


def test_buggy_vs_intended_budget_with_tiny_real_balance():
    """End-to-end arithmetic check of the fixed call site's math."""
    real_balance = 0.75
    sim_capital_floor = 100.0

    buggy_budget = compute_cycle_risk_budget(real_balance, 0.0)
    assert round(buggy_budget, 3) == 0.135

    intended_budget = compute_cycle_risk_budget(
        max(real_balance, sim_capital_floor), 0.0
    )
    assert round(intended_budget, 2) == 18.0
    assert intended_budget != buggy_budget


def test_cycle_applies_sim_capital_floor_to_total_capital_when_not_live():
    """Source-level guard: _cycle() must floor total_capital with the same
    SIM_CAPITAL check used for `capital`, before computing cycle_budget."""
    src = inspect.getsource(Orchestrator._cycle)

    idx_total_capital = src.index('total_capital = self.position_manager.data.get("capital", capital)')
    idx_budget = src.index("cycle_budget = compute_cycle_risk_budget(total_capital, directional_locked)")
    assert idx_total_capital < idx_budget

    window = src[idx_total_capital:idx_budget]
    assert "if self._is_simulation_running() and not self._is_live_trading():" in window
    assert 'total_capital = max(total_capital, float(os.getenv("SIM_CAPITAL", 100)))' in window
