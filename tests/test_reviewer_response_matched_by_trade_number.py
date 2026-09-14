"""
Regression test: ReviewerAgent must match Claude's JSON review decisions
back to trades by an explicit trade identifier, not by raw array position.

Bug: ReviewerAgent._parse_claude_response() paired Claude's response array
with `signals` purely positionally:

    for i, (item, sig) in enumerate(zip(parsed, signals)):
        ...
        decisions.append(ReviewDecision(condition_id=sig.condition_id, ...))

The REVIEWER_SYSTEM_PROMPT never asked Claude to echo which trade a given
JSON object is FOR — it only numbered them in the *prompt* ("--- Trade #1
---", "--- Trade #2 ---", ...) and trusted the response array to preserve
that order and count 1:1. If Claude's response ever drops a trade, reorders
them, or the array length otherwise doesn't match `len(signals)` — all
observed LLM behaviors — zip() silently pairs a decision meant for one
trade with a completely different trade's condition_id. In the live path
this means:

  - A weak/risky trade Claude never actually reviewed can inherit another
    trade's APPROVE verdict and reasoning, and get executed with real
    capital — the exact thing ReviewerAgent exists to prevent.
  - A trade Claude genuinely approved can end up wrongly VETOed (falls
    into the "missing from response" bucket) because its slot got
    consumed by another trade's decision.

Fix: the JSON schema now requires a "trade_number" field (1-based, matching
"--- Trade #N ---"), and _parse_claude_response() matches each response
item to `signals[trade_number - 1]` when that index is valid and not
already claimed, falling back to positional matching only for items that
omit trade_number (e.g. a degraded/older-style response) so behavior for
a well-formed, in-order response is unchanged.
"""
import json

from agents.subagents.reviewer_agent import ReviewerAgent, ReviewVerdict
from agents.subagents.signal_agent_v2 import EnrichedSignal


def _signal(cid: str, direction: str = "YES", edge: float = 0.15) -> EnrichedSignal:
    return EnrichedSignal(condition_id=cid, direction=direction, edge=edge)


def test_dropped_middle_trade_does_not_leak_verdict_to_wrong_signal():
    """
    Claude reviews trade 1 and trade 3 but drops trade 2 entirely (thin-edge
    NO trade). Pre-fix, positional zip() would hand signal #2 the verdict
    that was actually written for signal #3 (an APPROVE), silently
    green-lighting a trade Claude never reviewed. Post-fix, signal #2 must
    fall back to the safe "not reviewed" VETO default, and signal #3 must
    get its own, correctly-addressed decision.
    """
    ra = ReviewerAgent()
    sig1 = _signal("COND_1", direction="YES", edge=0.20)
    sig2 = _signal("COND_2", direction="NO", edge=0.02)   # never reviewed by Claude
    sig3 = _signal("COND_3", direction="YES", edge=0.15)
    signals = [sig1, sig2, sig3]

    claude_response = json.dumps([
        {"trade_number": 1, "verdict": "APPROVE", "confidence": 0.9,
         "suggested_size_pct": 1.0, "reasoning": "trade1 solid edge", "risk_assessment": "low"},
        {"trade_number": 3, "verdict": "APPROVE", "confidence": 0.85,
         "suggested_size_pct": 1.0, "reasoning": "trade3 strong YES edge", "risk_assessment": "low"},
    ])

    decisions = {d.condition_id: d for d in ra._parse_claude_response(claude_response, signals)}

    assert decisions["COND_1"].verdict == ReviewVerdict.APPROVE
    assert decisions["COND_1"].reasoning == "trade1 solid edge"

    # The never-reviewed thin-edge NO trade must NOT inherit trade 3's APPROVE.
    assert decisions["COND_2"].verdict == ReviewVerdict.VETO
    assert "trade3" not in decisions["COND_2"].reasoning

    # Trade 3's own APPROVE must reach COND_3, not get eaten by the gap.
    assert decisions["COND_3"].verdict == ReviewVerdict.APPROVE
    assert decisions["COND_3"].reasoning == "trade3 strong YES edge"


def test_reordered_response_still_matches_correct_trade():
    """Claude returns decisions out of order (3, 1, 2) — each must still
    land on its own condition_id via trade_number, not the array position."""
    ra = ReviewerAgent()
    sig1 = _signal("COND_1", direction="YES", edge=0.20)
    sig2 = _signal("COND_2", direction="NO", edge=0.02)
    sig3 = _signal("COND_3", direction="YES", edge=0.15)
    signals = [sig1, sig2, sig3]

    claude_response = json.dumps([
        {"trade_number": 3, "verdict": "APPROVE", "confidence": 0.85,
         "suggested_size_pct": 1.0, "reasoning": "trade3 approve", "risk_assessment": "low"},
        {"trade_number": 1, "verdict": "APPROVE", "confidence": 0.9,
         "suggested_size_pct": 1.0, "reasoning": "trade1 approve", "risk_assessment": "low"},
        {"trade_number": 2, "verdict": "VETO", "confidence": 0.8,
         "suggested_size_pct": 0.0, "reasoning": "trade2 veto", "risk_assessment": "high"},
    ])

    decisions = {d.condition_id: d for d in ra._parse_claude_response(claude_response, signals)}

    assert decisions["COND_1"].verdict == ReviewVerdict.APPROVE
    assert decisions["COND_1"].reasoning == "trade1 approve"
    assert decisions["COND_2"].verdict == ReviewVerdict.VETO
    assert decisions["COND_2"].reasoning == "trade2 veto"
    assert decisions["COND_3"].verdict == ReviewVerdict.APPROVE
    assert decisions["COND_3"].reasoning == "trade3 approve"


def test_well_formed_in_order_response_unchanged():
    """Backward compatibility: a normal, in-order, 1:1 response (the common
    case) must still work exactly as before."""
    ra = ReviewerAgent()
    sig1 = _signal("COND_1")
    sig2 = _signal("COND_2")
    signals = [sig1, sig2]

    claude_response = json.dumps([
        {"trade_number": 1, "verdict": "APPROVE", "confidence": 0.9,
         "suggested_size_pct": 1.0, "reasoning": "ok1", "risk_assessment": "low"},
        {"trade_number": 2, "verdict": "REDUCE", "confidence": 0.6,
         "suggested_size_pct": 0.5, "reasoning": "ok2", "risk_assessment": "med"},
    ])

    decisions = {d.condition_id: d for d in ra._parse_claude_response(claude_response, signals)}
    assert decisions["COND_1"].verdict == ReviewVerdict.APPROVE
    assert decisions["COND_2"].verdict == ReviewVerdict.REDUCE
    assert decisions["COND_2"].suggested_size_pct == 0.5


def test_missing_trade_number_falls_back_to_positional_matching():
    """If Claude's response omits trade_number entirely (degraded/older
    response), matching must still fall back to positional order rather
    than dropping every trade — no worse than pre-fix behavior for the
    well-formed 1:1 case."""
    ra = ReviewerAgent()
    sig1 = _signal("COND_1")
    sig2 = _signal("COND_2")
    signals = [sig1, sig2]

    claude_response = json.dumps([
        {"verdict": "APPROVE", "confidence": 0.9, "suggested_size_pct": 1.0,
         "reasoning": "ok1", "risk_assessment": "low"},
        {"verdict": "VETO", "confidence": 0.6, "suggested_size_pct": 0.0,
         "reasoning": "ok2", "risk_assessment": "high"},
    ])

    decisions = {d.condition_id: d for d in ra._parse_claude_response(claude_response, signals)}
    assert decisions["COND_1"].verdict == ReviewVerdict.APPROVE
    assert decisions["COND_2"].verdict == ReviewVerdict.VETO
