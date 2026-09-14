"""
Regression test: AutonomousDecisionEngine.evaluate()'s RiskLevel.HIGH branch
must not silently overwrite a SKIP that STREAK_FILTER already set.

STREAK_FILTER sets action=SKIP after 4+ consecutive losses on a weak-edge
signal (edge < 0.08) — the engine's own loss-streak circuit breaker. Because
a weak edge itself contributes to _assess_risk()'s risk score, a signal that
trips STREAK_FILTER very commonly also lands in the HIGH risk band. Before
the fix, the HIGH branch unconditionally set action=EXECUTE_REDUCED,
discarding the SKIP and letting the bot trade anyway (at half size) in
exactly the scenario the circuit breaker exists to stop.
"""
from types import SimpleNamespace

from agents.autonomous_engine import ActionType, AutonomousDecisionEngine, RiskLevel


def _make_signal(edge: float, direction: str = "NO", risk_flags=None) -> SimpleNamespace:
    return SimpleNamespace(
        edge=edge,
        direction=direction,
        risk_flags=risk_flags or ["x"],
        confluence_score=0.5,
        regime_strength=0.0,
    )


def _streak_losses(n: int) -> list[dict]:
    return [{"pnl": -1.0, "edge": 0.06} for _ in range(n)]


def _new_engine() -> AutonomousDecisionEngine:
    engine = AutonomousDecisionEngine()
    engine._state = {}
    return engine


def test_streak_filter_skip_survives_high_risk():
    engine = _new_engine()
    signal = _make_signal(edge=0.06)  # < 0.08 → STREAK_FILTER eligible, >= 0.05 → not EDGE_TOO_LOW

    decision = engine.evaluate(
        signal=signal,
        review_decision=None,
        capital=100.0,
        open_count=0,
        max_positions=5,
        closed_trades=_streak_losses(4),
    )

    assert decision.risk_level == RiskLevel.HIGH
    assert decision.action == ActionType.SKIP
    assert not decision.should_execute


def test_high_risk_without_prior_skip_still_reduces():
    """Guard must not change the normal HIGH-risk path when nothing set SKIP."""
    engine = _new_engine()
    signal = _make_signal(edge=0.06)

    decision = engine.evaluate(
        signal=signal,
        review_decision=None,
        capital=100.0,
        open_count=0,
        max_positions=5,
        closed_trades=None,  # no loss streak → STREAK_FILTER never fires
    )

    assert decision.risk_level == RiskLevel.HIGH
    assert decision.action == ActionType.EXECUTE_REDUCED
    assert decision.should_execute


def test_streak_filter_skip_survives_critical_risk_unchanged():
    """Sanity check that the pre-existing CRITICAL guard still behaves the same."""
    engine = _new_engine()
    signal = _make_signal(edge=0.03, risk_flags=["a", "b", "c"])  # pushes risk_score into CRITICAL

    decision = engine.evaluate(
        signal=signal,
        review_decision=None,
        capital=100.0,
        open_count=0,
        max_positions=5,
        closed_trades=_streak_losses(4),
    )

    assert decision.risk_level == RiskLevel.CRITICAL
    assert decision.action == ActionType.SKIP
    assert not decision.should_execute
