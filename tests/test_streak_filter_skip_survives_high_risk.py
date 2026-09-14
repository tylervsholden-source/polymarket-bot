"""
Regression test (28th daily review): AutonomousDecisionEngine.evaluate()'s
LOSS_STREAK "STREAK_FILTER" gate set `action = ActionType.SKIP` when 4+
consecutive losses coincide with a weak edge (< 0.08) — the engine's own
explicit protection against continuing to trade through a bad streak on
marginal signals.

But a few lines later, the "RISK SEVİYESİNE GÖRE BOYUT AYARI" block
unconditionally overwrote `action` for RiskLevel.HIGH:

    elif risk_level == RiskLevel.HIGH:
        size_mult = min(size_mult, 0.50)
        action = ActionType.EXECUTE_REDUCED          # <-- wipes out SKIP
        reasons.append("HIGH_RISK: size×0.50")

The CRITICAL branch right below it guards against exactly this
("action = ActionType.EXECUTE_REDUCED if action != ActionType.SKIP else
action"), but HIGH did not. Because a weak edge (< 0.08) itself already
contributes +2 to `_assess_risk()`'s risk_score, any signal that trips
STREAK_FILTER very commonly also lands in the HIGH risk band (score 5-7:
e.g. weak edge +2, NO direction +2, two risk flags +2 = 6). In that
overwhelmingly common case, the STREAK_FILTER SKIP was silently discarded
and the trade executed anyway (just at half size) — defeating the
loss-streak protection in the exact scenario it exists for.

Pre-fix: `test_streak_filter_skip_survives_high_risk_classification` fails
(`should_execute` is True, action is EXECUTE_REDUCED instead of SKIP).
Post-fix: SKIP survives the HIGH-risk sizing block, matching CRITICAL's
existing behavior.
"""
from __future__ import annotations

from agents.autonomous_engine import ActionType, AutonomousDecisionEngine, RiskLevel


class _Signal:
    edge = 0.06  # < STREAK_FILTER's 0.08 threshold, but not < EDGE_TOO_LOW's 0.05
    direction = "NO"
    risk_flags = ["flag_a", "flag_b"]
    confluence_score = 0.4
    regime_strength = 0.3


class _ApprovedReview:
    verdict = "APPROVE"  # no VETO/REDUCE path involved


def _four_losses() -> list[dict]:
    return [{"result": "LOSS", "pnl": -1.0, "edge": 0.1} for _ in range(4)]


def test_streak_filter_scenario_is_classified_high_risk():
    """Sanity check on the setup: this signal really does land in HIGH,
    the risk band that lacked the SKIP-preserving guard."""
    engine = AutonomousDecisionEngine()
    risk_level = engine._assess_risk(
        _Signal(), _ApprovedReview(), capital=200.0, open_count=0, max_positions=5
    )
    assert risk_level == RiskLevel.HIGH


def test_streak_filter_skip_survives_high_risk_classification():
    engine = AutonomousDecisionEngine()
    decision = engine.evaluate(
        signal=_Signal(),
        review_decision=_ApprovedReview(),
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
        f"STREAK_FILTER's SKIP must survive HIGH-risk sizing, got "
        f"{decision.action} — {decision.summary()}"
    )
    assert decision.should_execute is False, (
        "a signal the engine itself flagged via STREAK_FILTER must not "
        "still be executed just because it also scored HIGH risk"
    )


def test_critical_risk_still_preserves_streak_filter_skip():
    """Same scenario but pushed into CRITICAL (very low capital) — this
    branch already had the guard and must keep working post-fix."""
    engine = AutonomousDecisionEngine()
    decision = engine.evaluate(
        signal=_Signal(),
        review_decision=_ApprovedReview(),
        capital=5.0,  # < LOW_CAPITAL_THRESHOLD -> forces CRITICAL/SURVIVAL
        open_count=0,
        max_positions=5,
        closed_trades=_four_losses(),
    )
    assert decision.action == ActionType.SKIP
    assert decision.should_execute is False
