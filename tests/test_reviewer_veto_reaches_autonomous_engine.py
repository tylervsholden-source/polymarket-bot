"""
Regression test (29th daily review): a VETO'd signal must still reach
AutonomousDecisionEngine.evaluate() so it can be severely-reduced-and-executed,
instead of being silently dropped before evaluate() is ever called.

Bug: ReviewDecision.approved excluded ReviewVerdict.VETO:

    @property
    def approved(self) -> bool:
        return self.verdict in (APPROVE, APPROVE_WITH_WARNING, REDUCE)

ReviewBatchResult.get_approved_signals() (called by
agents/subagents/coordinator.py's run_cycle()) only keeps (signal, decision)
pairs where `decision.approved` is True, and builds
`coord_result.approved_signals` from that. agents/orchestrator.py's
execution loop iterates only `coord_result.approved_signals`, and only for
pairs inside that loop does it call
`self.autonomous_engine.evaluate(signal=signal, review_decision=review_decision, ...)`.

But orchestrator.py documents (compute_bet_size/apply_risk_size_multiplier's
own comment, line ~112) that "REVIEWER_VETO x0.25" is one of the risk-based
size reductions the autonomous engine applies — and autonomous_engine.py's
evaluate() has a dedicated branch for exactly this:

    if verdict_str == "VETO":
        size_mult = min(size_mult, 0.25)
        action = ActionType.EXECUTE_REDUCED
        reasons.append("REVIEWER_VETO: severely reduced but not skipped")

Because `approved` excluded VETO, that branch could never run in the live
pipeline: every VETO'd signal was filtered out of `approved_signals` before
evaluate() ever saw it, turning "severely reduced but not skipped" into an
unconditional, 100% skip -- the opposite of "Bot ASLA durmaz" (CLAUDE.md).

Fix: `approved` now also returns True for VETO, so a VETO'd signal survives
into `approved_signals` and reaches evaluate(), which then correctly shrinks
it to size_multiplier=0.25 / EXECUTE_REDUCED rather than dropping it.
vetoed_count/approved_count/reduced_count stats are computed independently
by counting `verdict == X` (reviewer_agent.py's execute()), so they are
unaffected by this change.
"""
from __future__ import annotations

from agents.autonomous_engine import ActionType, AutonomousDecisionEngine
from agents.subagents.reviewer_agent import ReviewBatchResult, ReviewDecision, ReviewVerdict
from agents.subagents.signal_agent_v2 import EnrichedSignal


def _signal(condition_id: str = "mkt-1", edge: float = 0.15) -> EnrichedSignal:
    return EnrichedSignal(
        market={"condition_id": condition_id, "question": "Test market"},
        condition_id=condition_id,
        direction="YES",
        edge=edge,
        size=10.0,
    )


def test_veto_decision_is_approved_for_pipeline_purposes():
    decision = ReviewDecision(condition_id="mkt-1", verdict=ReviewVerdict.VETO)
    assert decision.approved is True


def test_get_approved_signals_includes_vetoed_signal():
    sig = _signal()
    decision = ReviewDecision(condition_id=sig.condition_id, verdict=ReviewVerdict.VETO)
    batch = ReviewBatchResult(decisions=[decision], vetoed_count=1, total_reviewed=1)

    approved = batch.get_approved_signals([sig])

    assert len(approved) == 1, (
        "a VETO'd signal must still be handed to the execution pipeline so "
        "AutonomousDecisionEngine can apply its own REVIEWER_VETO x0.25 "
        "reduction, instead of being dropped before evaluate() runs"
    )
    out_sig, out_dec = approved[0]
    assert out_sig is sig
    assert out_dec.verdict == ReviewVerdict.VETO


def test_autonomous_engine_reduces_rather_than_never_seeing_veto():
    """End-to-end: once a VETO'd pair survives get_approved_signals(), the
    engine's own documented behavior (severely reduce, don't skip) fires."""
    sig = _signal()
    decision = ReviewDecision(condition_id=sig.condition_id, verdict=ReviewVerdict.VETO)
    batch = ReviewBatchResult(decisions=[decision])
    approved = batch.get_approved_signals([sig])
    assert approved, "precondition: VETO survives into approved_signals"

    engine = AutonomousDecisionEngine()
    out_sig, out_dec = approved[0]
    result = engine.evaluate(
        signal=out_sig,
        review_decision=out_dec,
        capital=200.0,
        open_count=0,
        max_positions=5,
    )

    assert result.action == ActionType.EXECUTE_REDUCED
    assert result.should_execute is True
    assert result.size_multiplier <= 0.25
    assert any("REVIEWER_VETO" in r for r in result.reasoning)
