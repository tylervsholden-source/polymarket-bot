"""
Regression test: reviewer REDUCE verdict's suggested_size_pct must apply
to live bet_size exactly once, not twice.

Bug: AgentCoordinator.run_cycle() (agents/subagents/coordinator.py) already
shrinks `signal.size` by `suggested_size_pct` for every REDUCE-verdict signal
before it reaches Orchestrator's per-signal loop. That same (already reduced)
`signal.size` is later fed into `compute_bet_size(signal_size=signal.size, ...)`
to derive the live `bet_size` (agents/orchestrator.py). Separately,
`AutonomousDecisionEngine.evaluate()` used to fold the *same*
`suggested_size_pct` into its own `size_multiplier`, which orchestrator then
multiplies into `bet_size` again (`bet_size *= _auto_size_mult`). The net
effect: a REDUCE verdict with suggested_size_pct=0.6 shrank the real order to
~0.6*0.6=0.36x instead of the reviewer's intended 0.6x — the same
double-application pattern FIX_CONFLICT_REPORT.md flagged for 15m
double-dampening.

This locks in the fix: evaluate() must still mark the decision as
EXECUTE_REDUCED (for downstream logging/classification) but must not clamp
size_multiplier below what the *other* independent risk factors already
imply, purely on account of the REDUCE verdict.

Both cases below freeze evaluate()'s `time.gmtime()` read to a fixed
daytime UTC hour. Without that, this test was itself flaky: evaluate()'s
own LOW_LIQUIDITY_HOURS factor (agents/autonomous_engine.py) independently
applies `size_mult = min(size_mult, 0.7)` whenever it runs between UTC
0-6, which silently failed the "no other risk factors active" assertion
below for a quarter of every day (and correspondingly masked the very
double-application regression this test exists to catch, since a real
regression during those hours would surface as the same, seemingly
"expected", mismatch).
"""
from __future__ import annotations

import time
from types import SimpleNamespace

from agents.autonomous_engine import ActionType, AutonomousDecisionEngine

_DAYTIME_UTC = time.struct_time((2026, 1, 1, 12, 0, 0, 3, 1, 0))


def _clean_signal(edge: float = 0.15, direction: str = "YES") -> SimpleNamespace:
    """A signal with no other risk factors present, so any size_multiplier
    below 1.0 must come from the verdict handling under test."""
    return SimpleNamespace(
        edge=edge,
        direction=direction,
        risk_flags=[],
        confluence_score=0.9,
        regime_strength=0.0,
    )


def _reduce_decision(suggested_size_pct: float) -> SimpleNamespace:
    return SimpleNamespace(verdict="REDUCE", suggested_size_pct=suggested_size_pct)


def test_reduce_verdict_does_not_shrink_size_multiplier_below_suggested(monkeypatch):
    monkeypatch.setattr(time, "gmtime", lambda *a: _DAYTIME_UTC)
    engine = AutonomousDecisionEngine()
    decision = engine.evaluate(
        signal=_clean_signal(),
        review_decision=_reduce_decision(0.6),
        capital=1000.0,
        open_count=0,
        max_positions=5,
        closed_trades=[],
    )
    # Coordinator already applied ×0.6 to signal.size upstream. evaluate()
    # must not clamp size_multiplier below that same factor again — with no
    # other risk factors active, it should stay at full size (1.0), leaving
    # the coordinator's single ×0.6 as the only reduction bet_size will see.
    assert decision.size_multiplier == 1.0, (
        f"expected no additional shrink beyond coordinator's own ×0.6, "
        f"got size_multiplier={decision.size_multiplier} "
        f"(would compound to ×{0.6 * decision.size_multiplier:.3f} total)"
    )
    assert decision.action == ActionType.EXECUTE_REDUCED
    assert decision.should_execute


def test_reduce_verdict_still_composes_with_independent_risk_factors(monkeypatch):
    monkeypatch.setattr(time, "gmtime", lambda *a: _DAYTIME_UTC)
    engine = AutonomousDecisionEngine()
    # regime_strength > 0.80 independently caps size_multiplier at 0.5 —
    # that cap must still apply; it just shouldn't be *additionally*
    # multiplied by the REDUCE suggested_size_pct on top.
    signal = _clean_signal()
    signal.regime_strength = 0.9
    decision = engine.evaluate(
        signal=signal,
        review_decision=_reduce_decision(0.6),
        capital=1000.0,
        open_count=0,
        max_positions=5,
        closed_trades=[],
    )
    assert decision.size_multiplier == 0.5
