"""
Regression test (29th daily review): AutonomousDecisionEngine.evaluate()'s
LOSS_STREAK "STREAK_FILTER" gate sets `action = ActionType.SKIP` when 4+
consecutive losses coincide with a weak edge (< 0.08) — the engine's own
explicit protection against continuing to trade through a bad streak on
marginal signals.

The 28th daily review (commit b549aa4) fixed the HIGH/CRITICAL risk-level
branches silently overwriting that SKIP, by guarding them with
`action = ActionType.EXECUTE_REDUCED if action != ActionType.SKIP else action`.

But the "REVIEWER VERDICT ENTEGRASYONU" block a few lines further down —
which runs after the risk-level branches in the same function — never got
the same guard:

    if verdict_str == "VETO":
        size_mult = min(size_mult, 0.25)
        action = ActionType.EXECUTE_REDUCED          # <-- wipes out SKIP
        reasons.append("REVIEWER_VETO: severely reduced but not skipped")
    elif verdict_str == "REDUCE":
        ...
        action = ActionType.EXECUTE_REDUCED          # <-- wipes out SKIP
        reasons.append(...)

A signal that trips STREAK_FILTER (weak edge during an active loss streak)
is exactly the kind of signal ReviewerAgent tends to VETO or REDUCE. When
that happens, the reviewer block silently discarded the SKIP and forced the
trade through anyway — defeating the loss-streak protection at the worst
possible time.

Pre-fix: both tests below fail (action is EXECUTE_REDUCED instead of SKIP).
Post-fix: SKIP survives the REVIEWER VERDICT block, matching the HIGH/
CRITICAL risk branches' existing behavior.
"""
from __future__ import annotations

from agents.autonomous_engine import ActionType, AutonomousDecisionEngine, RiskLevel


class _Signal:
    edge = 0.06  # < STREAK_FILTER's 0.08 threshold, but not < EDGE_TOO_LOW's 0.05
    direction = "YES"
    risk_flags: list = []
    confluence_score = 0.9
    regime_strength = 0.3


class _VetoReview:
    verdict = "VETO"


class _ReduceReview:
    verdict = "REDUCE"
    suggested_size_pct = 0.6


def _four_losses() -> list[dict]:
    return [{"result": "LOSS", "pnl": -1.0, "edge": 0.1} for _ in range(4)]


def test_streak_filter_scenario_is_classified_low_risk():
    """Sanity check on the setup: this signal lands in LOW/MEDIUM risk, so
    the HIGH/CRITICAL guards from the 28th review don't mask this bug —
    only the REVIEWER VERDICT block's own (missing) guard is exercised."""
    engine = AutonomousDecisionEngine()
    risk_level = engine._assess_risk(
        _Signal(), _VetoReview(), capital=200.0, open_count=0, max_positions=5
    )
    assert risk_level in (RiskLevel.LOW, RiskLevel.MEDIUM)


def test_streak_filter_skip_survives_reviewer_veto():
    engine = AutonomousDecisionEngine()
    decision = engine.evaluate(
        signal=_Signal(),
        review_decision=_VetoReview(),
        capital=200.0,
        open_count=0,
        max_positions=5,
        closed_trades=_four_losses(),
    )

    assert engine._performance.consecutive_losses == 4
    assert any("STREAK_FILTER" in r for r in decision.reasoning), (
        "STREAK_FILTER gate should have fired for a weak-edge signal after "
        "a 4-loss streak"
    )
    assert decision.action == ActionType.SKIP, (
        f"STREAK_FILTER's SKIP must survive a REVIEWER_VETO verdict, got "
        f"{decision.action} — {decision.summary()}"
    )
    assert decision.should_execute is False, (
        "a signal the engine itself flagged via STREAK_FILTER must not "
        "still be executed just because the reviewer also vetoed it"
    )


def test_streak_filter_skip_survives_reviewer_reduce():
    engine = AutonomousDecisionEngine()
    decision = engine.evaluate(
        signal=_Signal(),
        review_decision=_ReduceReview(),
        capital=200.0,
        open_count=0,
        max_positions=5,
        closed_trades=_four_losses(),
    )

    assert decision.action == ActionType.SKIP, (
        f"STREAK_FILTER's SKIP must survive a REVIEWER_REDUCE verdict, got "
        f"{decision.action} — {decision.summary()}"
    )
    assert decision.should_execute is False
