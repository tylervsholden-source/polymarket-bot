"""
Regression test: ReviewerAgent._parse_claude_response() trusted Claude's
"suggested_size_pct" JSON field without validating it against the documented
0.0-1.0 range (ReviewDecision.suggested_size_pct docstring: "0.0 - 1.0 (REDUCE
icin)"; REVIEWER_SYSTEM_PROMPT also asks for 0.0-1.0).

Bug: agents/orchestrator.py applies a REDUCE verdict's suggested_size_pct to
bet_size via apply_risk_size_multiplier(), which multiplies with NO re-clamp
by design (52nd daily review — the no-reclamp is what lets a REDUCE shrink
bet_size below compute_bet_size()'s capital-scaled floor, instead of the
floor silently re-inflating the cut back up). That design is only safe if
suggested_size_pct <= 1.0. Claude's JSON response is untrusted external
input and was never clamped, so a value > 1.0 (e.g. 1.5 — a plausible LLM
slip, or literally any unvalidated number) silently turns a "REDUCE" verdict
into an size INCREASE.

Concrete consequence: for capital < $20 (survival-mode band), compute_bet_size()'s
20%-of-capital position cap binds tighter than the flat $4.00 HARD_MAX_BET
re-clamp that runs after all size adjustments in orchestrator.py. An
out-of-range suggested_size_pct can push the final, live bet_size past
CLAUDE.md's non-negotiable "Max tek pozisyon: portföyün %20'si (Kelly
override yapmaz)" cap even after that final $4.00 clamp:

    capital=$15, 20% cap=$3.00, compute_bet_size() -> bet_size=$3.00
    suggested_size_pct=1.5 (REDUCE, unclamped) -> bet_size=$4.50
    final HARD_MAX_BET=$4.00 clamp -> bet_size=$4.00  (still > $3.00 cap!)

Fix: clamp suggested_size_pct to [0.0, 1.0] where it is parsed out of
Claude's JSON response, before it ever reaches ReviewDecision.
"""
from __future__ import annotations

import json

from agents.orchestrator import apply_risk_size_multiplier, compute_bet_size
from agents.subagents.reviewer_agent import ReviewerAgent, ReviewVerdict
from agents.subagents.signal_agent_v2 import EnrichedSignal


def _signal(cid: str) -> EnrichedSignal:
    return EnrichedSignal(condition_id=cid, direction="YES", edge=0.15)


def test_out_of_range_suggested_size_pct_is_clamped():
    """Claude returns suggested_size_pct=1.5 on a REDUCE verdict — the parsed
    ReviewDecision must clamp it to 1.0, not pass the raw value through."""
    ra = ReviewerAgent()
    sig = _signal("COND_1")

    claude_response = json.dumps([
        {"trade_number": 1, "verdict": "REDUCE", "confidence": 0.6,
         "suggested_size_pct": 1.5, "reasoning": "oversized slip",
         "risk_assessment": "medium"},
    ])

    decisions = ra._parse_claude_response(claude_response, [sig])
    assert len(decisions) == 1
    assert decisions[0].verdict == ReviewVerdict.REDUCE
    assert decisions[0].suggested_size_pct == 1.0, (
        f"suggested_size_pct must be clamped to <= 1.0, got "
        f"{decisions[0].suggested_size_pct} — an unclamped value lets a "
        f"REDUCE verdict silently INCREASE bet_size downstream."
    )


def test_negative_suggested_size_pct_is_clamped():
    """A negative value (malformed/adversarial response) must not flip the
    multiplier's sign and produce a negative bet_size."""
    ra = ReviewerAgent()
    sig = _signal("COND_1")

    claude_response = json.dumps([
        {"trade_number": 1, "verdict": "REDUCE", "confidence": 0.6,
         "suggested_size_pct": -0.5, "reasoning": "malformed",
         "risk_assessment": "medium"},
    ])

    decisions = ra._parse_claude_response(claude_response, [sig])
    assert decisions[0].suggested_size_pct == 0.0


def test_unclamped_reduce_would_breach_position_cap_for_small_capital():
    """End-to-end demonstration of the live consequence: an unclamped
    suggested_size_pct > 1.0 pushes bet_size past compute_bet_size()'s
    20%-of-capital cap even after the orchestrator's final $4.00
    HARD_MAX_BET clamp, for a small-capital (<$20) account."""
    capital = 15.0
    kelly_size = 3.00  # Kelly already at (or above) the 20% cap
    position_cap = capital * 0.20  # CLAUDE.md: max 20% of capital per position
    HARD_MAX_BET = 4.0

    bet_size, _ = compute_bet_size(
        capital=capital,
        signal_size=kelly_size,
        min_bet=1.0,
        max_bet=8.0,
        max_position_pct=0.20,
        hard_max_bet=HARD_MAX_BET,
    )
    assert bet_size == position_cap  # $3.00 — correctly capped pre-REDUCE

    # Unclamped (buggy) REDUCE application:
    unclamped_pct = 1.5
    inflated = apply_risk_size_multiplier(bet_size, unclamped_pct)
    inflated = min(inflated, HARD_MAX_BET)  # orchestrator's final hard cap
    assert inflated > position_cap, (
        "sanity check on the bug: even the final $4.00 HARD_MAX_BET clamp "
        "does not protect the 20%-of-capital cap for capital < $20"
    )

    # Fixed (clamped) REDUCE application must never exceed the cap that was
    # already enforced by compute_bet_size().
    clamped_pct = max(0.0, min(1.0, unclamped_pct))
    fixed = apply_risk_size_multiplier(bet_size, clamped_pct)
    fixed = min(fixed, HARD_MAX_BET)
    assert fixed <= position_cap
