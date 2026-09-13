"""
Regression test: the rule-based reviewer's VETO for a counter-trend trade in an
overextended regime (CLAUDE.md "Kritik Kesifler": regime_strength > 0.70 is the
bot's worst historical loss pattern) must actually fire.

Bug: signal_agent_v2._detect_risk_flags only ever appends "COUNTER_REGIME_NO" or
"COUNTER_REGIME_YES", never bare "COUNTER_REGIME". reviewer_agent._rule_based_review
checked `any(f == "COUNTER_REGIME" ...)`, an exact match that can never be true, so
the VETO was permanently dead code and these trades fell through to REDUCE/APPROVE.
"""
from agents.subagents.reviewer_agent import ReviewerAgent, ReviewVerdict
from agents.subagents.signal_agent_v2 import EnrichedSignal


def _make_signal(risk_flags, direction="NO", edge=0.20, confluence_score=0.55, regime_strength=0.82):
    return EnrichedSignal(
        condition_id="test-cond-1",
        direction=direction,
        edge=edge,
        confluence_score=confluence_score,
        regime_strength=regime_strength,
        risk_flags=risk_flags,
    )


def test_counter_regime_no_flag_triggers_veto_in_overextended_regime():
    agent = ReviewerAgent()
    sig = _make_signal(["REGIME_OVEREXTENDED", "COUNTER_REGIME_NO"])
    decisions = agent._rule_based_review([sig], research=None, capital=100.0)
    assert decisions[0].verdict == ReviewVerdict.VETO
    assert "Counter-regime" in decisions[0].reasoning


def test_counter_regime_yes_flag_triggers_veto_in_overextended_regime():
    agent = ReviewerAgent()
    sig = _make_signal(["REGIME_OVEREXTENDED", "COUNTER_REGIME_YES"], direction="YES")
    decisions = agent._rule_based_review([sig], research=None, capital=100.0)
    assert decisions[0].verdict == ReviewVerdict.VETO


def test_overextended_without_counter_regime_flag_does_not_veto_on_this_rule():
    agent = ReviewerAgent()
    sig = _make_signal(["REGIME_OVEREXTENDED"], direction="NO", edge=0.20)
    decisions = agent._rule_based_review([sig], research=None, capital=100.0)
    assert decisions[0].verdict != ReviewVerdict.VETO
