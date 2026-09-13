"""
Regression test: AutonomousDecisionEngine's drawdown_pct must be measured
from the true session high-water mark, not just initial_capital vs the
current call's capital.

Bug: agents/autonomous_engine.py::_update_performance() computed
    peak = max(perf.initial_capital, capital)
    perf.drawdown_pct = max(0, (peak - capital) / peak)
on every call, with no state remembering any actual historical peak. Once
capital grows above initial_capital, "peak" always just equals the *current*
capital passed to this call, so (peak - capital) is always 0 by construction.
Concretely: capital goes $1000 (initial) -> $3000 (session high) -> $1500 —
a real 50% drawdown off the $3000 peak — but since $1500 > $1000
(initial_capital), the old code computed peak=max(1000, 1500)=1500 and
reported drawdown_pct=0.0. The CRITICAL drawdown protection in evaluate()
(DRAWDOWN_DANGER=0.90) can therefore never fire for any drawdown that
happens after the bot has ever been profitable, exactly the scenario this
bot's $1000->$3000 target makes routine.

Fix: track a running perf.peak_capital = max(peak_capital, initial_capital,
capital) across calls, and compute drawdown_pct from that real peak.
"""
from __future__ import annotations

import pytest

from agents.autonomous_engine import AutonomousDecisionEngine


def test_drawdown_measures_from_true_session_peak_not_initial_capital(monkeypatch):
    monkeypatch.setenv("INITIAL_CAPITAL", "1000")
    engine = AutonomousDecisionEngine()

    # Capital grows to 3x initial — new session peak, no drawdown yet.
    engine._update_performance(closed_trades=[{"pnl": 50, "edge": 0.1}], capital=3000)
    assert engine._performance.drawdown_pct == 0.0
    assert engine._performance.peak_capital == 3000

    # Capital drops to $1500 — still above initial_capital ($1000), but a
    # real 50% drawdown from the $3000 session peak.
    engine._update_performance(closed_trades=[{"pnl": -1500, "edge": 0.1}], capital=1500)
    assert engine._performance.drawdown_pct == pytest.approx(0.5), (
        "drawdown must be measured from the true session peak ($3000), not "
        f"just initial_capital vs current capital; got "
        f"{engine._performance.drawdown_pct:.3f}"
    )


def test_critical_drawdown_protection_fires_after_a_post_growth_drop(monkeypatch):
    """Same scenario, but through the public evaluate() path: a >90% drop
    from a post-growth session peak must classify as CRITICAL risk, even
    though current capital never falls below initial_capital."""
    from types import SimpleNamespace

    monkeypatch.setenv("INITIAL_CAPITAL", "1000")
    engine = AutonomousDecisionEngine()

    signal = SimpleNamespace(
        edge=0.15, direction="YES", risk_flags=[], confluence_score=0.9,
        regime_strength=0.0,
    )
    decision = SimpleNamespace(verdict="APPROVE", suggested_size_pct=1.0)

    # Reach a $10,000 session peak (10x initial), then crash to $900. The
    # real drawdown is >90% off the $10,000 peak; the old buggy calculation
    # would instead compare only to initial_capital ($1000 vs $900 = 10%
    # "drawdown"), nowhere near the CRITICAL threshold.
    engine.evaluate(
        signal=signal, review_decision=decision, capital=10000,
        open_count=0, max_positions=5,
        closed_trades=[{"pnl": 9000, "edge": 0.1}],
    )
    result = engine.evaluate(
        signal=signal, review_decision=decision, capital=900,
        open_count=0, max_positions=5,
        closed_trades=[{"pnl": -9100, "edge": 0.1}],
    )

    assert result.risk_level.value == "CRITICAL", (
        f"a >90% drop from the real session peak must trigger CRITICAL risk; "
        f"got {result.risk_level.value} (drawdown={engine._performance.drawdown_pct:.1%})"
    )
