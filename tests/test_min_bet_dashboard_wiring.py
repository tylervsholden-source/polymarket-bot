"""
Regression test for the dashboard min_bet control wiring in Orchestrator._cycle.

Bug: docs/architecture.md documents "min_bet: dashboard 1/5/10/20$ -> orchestrator
okur -> bet_size = max(min_bet, kelly)". `_cycle` did read the dashboard's value
from data/control.json into `min_bet_override`, but only ever passed it to
`_record_shadow_decisions` (the shadow/logging journal) — the real sizing call
(`compute_bet_size(..., min_bet=self._min_bet, ...)`) used the static
MIN_BET_SIZE env var set once at startup instead, so changing the dashboard
slider had zero effect on live order sizes. Same "control exists but isn't
connected to the live path" pattern as the previously-fixed MIN_MARKET_VOLUME/
OPT-2/OPT-3/OPT-6 bugs.

Wiring a real dashboard value (up to $20 per the documented slider range) into
compute_bet_size also exposed a latent gap in compute_bet_size itself: for
large-capital accounts, the min_pct floor (4% of capital) can exceed the
hard_max_bet ceiling once min_bet is no longer the tiny static default,
letting the floor silently override the "never more than $4/trade" watchdog
cap. This locks in both the wiring and the floor-never-exceeds-ceiling clamp.
"""
from __future__ import annotations

import inspect

from agents.orchestrator import Orchestrator, compute_bet_size


def test_cycle_passes_dashboard_min_bet_override_to_compute_bet_size():
    src = inspect.getsource(Orchestrator._cycle)
    assert "min_bet_override" in src
    assert "min_bet=min_bet_override" in src, (
        "compute_bet_size() call in _cycle must use the dashboard's "
        "min_bet_override, not the static self._min_bet env default"
    )


def test_compute_bet_size_floor_never_exceeds_hard_cap():
    # capital=$500, dashboard min_bet=$20 (the documented slider max): the
    # naive 4%-of-capital floor (min(20, 500*0.04)=20) exceeds hard_max_bet.
    bet_size, effective_min = compute_bet_size(
        capital=500.0,
        signal_size=0.0,
        min_bet=20.0,
        max_bet=8.0,
        max_position_pct=0.20,
        hard_max_bet=4.0,
    )
    assert bet_size <= 4.0
    assert effective_min <= 4.0


def test_compute_bet_size_floor_never_exceeds_ceiling_generally():
    for capital in (100.0, 250.0, 500.0, 1000.0):
        for min_bet in (1.0, 5.0, 10.0, 20.0):
            bet_size, effective_min = compute_bet_size(
                capital=capital,
                signal_size=0.0,
                min_bet=min_bet,
                max_bet=8.0,
                max_position_pct=0.20,
                hard_max_bet=4.0,
            )
            assert bet_size <= 4.0 + 1e-9, (capital, min_bet, bet_size)
            assert effective_min <= bet_size + 1e-9
