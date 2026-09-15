"""
Regression test: ReviewerAgent's REDUCE verdict (suggested_size_pct) was
being applied to signal.size BEFORE compute_bet_size()'s capital-scaled
floor, letting the floor silently re-inflate the reviewer's risk-based
reduction back up — sometimes to several times what the reviewer intended.

Bug: agents/subagents/coordinator.py::AgentCoordinator.run_cycle() used to
shrink `signal.size` directly for every REDUCE-verdict signal:

    sig.size = round(sig.size * dec.suggested_size_pct, 2)

before that value ever reached agents/orchestrator.py's

    bet_size, _effective_min = compute_bet_size(
        capital=capital, signal_size=signal.size, ...
    )

compute_bet_size()'s `effective_min` exists to keep a *Kelly-derived*
signal_size inside a tradeable capital-scaled band (e.g. for capital<$20,
`effective_min = min(min_bet, capital*0.40)`, further capped to
`capital*max_position_pct`) — it floors WHATEVER value it is given up to
that band. It cannot distinguish a signal that is small because Kelly's own
edge is thin from one that was deliberately shrunk moments earlier by the
reviewer's risk assessment. So a REDUCE'd signal.size that landed below
effective_min got silently re-inflated back up to the floor, discarding the
reviewer's protective reduction entirely — the same bug class already fixed
for AutonomousDecisionEngine's size_multiplier (22nd daily review,
apply_risk_size_multiplier()), but reached via a different, previously
untouched code path (coordinator.py mutating signal.size pre-floor, instead
of a post-floor re-clamp).

Concrete scenario: capital=$15 (survival-mode band), Kelly recommends
signal.size=$2.00 (already comfortably under the 20% position cap of
$3.00). The reviewer sees serious risk flags and issues REDUCE with
suggested_size_pct=0.3 (wants only $0.60 risked). Pre-fix, coordinator.py
pre-multiplies signal.size to $0.60, then compute_bet_size(capital=15,
signal_size=0.60, ...) floors it BACK UP to $3.00 — five times what the
reviewer intended, and even larger than Kelly's own $2.00 recommendation.

Fix:
  1. coordinator.py no longer mutates signal.size for REDUCE.
  2. agents/orchestrator.py applies review_decision.suggested_size_pct to
     bet_size via apply_risk_size_multiplier() (no reclamp) AFTER
     compute_bet_size(), the same uniform pattern already used for
     REVIEWER_VETO / walk-forward / adaptive-bet multipliers.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agents.orchestrator import apply_risk_size_multiplier, compute_bet_size
from agents.subagents.base_agent import AgentResult, AgentStatus
from agents.subagents.coordinator import AgentCoordinator
from agents.subagents.reviewer_agent import ReviewBatchResult, ReviewDecision, ReviewVerdict
from agents.subagents.signal_agent_v2 import EnrichedSignal, SignalResult


def _market(cid: str) -> dict:
    return {"condition_id": cid, "question": "Bitcoin Up or Down - March 16, 7:10PM-7:15PM ET"}


def _signal(cid: str, size: float) -> EnrichedSignal:
    return EnrichedSignal(market=_market(cid), condition_id=cid, direction="YES", size=size)


@pytest.mark.asyncio
async def test_coordinator_does_not_pre_shrink_signal_size_for_reduce():
    """The reviewer's REDUCE must not mutate signal.size in the coordinator —
    that value still needs to reach compute_bet_size() as Kelly's raw
    recommendation, or the capital floor can silently swallow the cut."""
    coord = AgentCoordinator.__new__(AgentCoordinator)
    coord._cycle_count = 0
    coord._total_approved = 0
    coord._total_vetoed = 0
    coord.enable_research = False
    coord.enable_review = True
    coord.enable_orderflow = False

    kelly_size = 2.00
    sig = _signal("mkt-1", size=kelly_size)

    coord.signal_agent = SimpleNamespace(
        execute=AsyncMock(
            return_value=AgentResult(
                agent_name="SignalAgent",
                status=AgentStatus.COMPLETED,
                data=SignalResult(signals=[sig], total_candidates=1, total_signals=1),
                duration_ms=1.0,
            )
        )
    )
    coord.reviewer_agent = SimpleNamespace(
        execute=AsyncMock(
            return_value=AgentResult(
                agent_name="ReviewerAgent",
                status=AgentStatus.COMPLETED,
                data=ReviewBatchResult(
                    decisions=[
                        ReviewDecision(
                            condition_id="mkt-1",
                            verdict=ReviewVerdict.REDUCE,
                            suggested_size_pct=0.3,
                        )
                    ],
                    approved_count=0,
                    vetoed_count=0,
                    reduced_count=1,
                    total_reviewed=1,
                ),
                duration_ms=1.0,
            )
        )
    )

    result = await coord.run_cycle(candidates=[_market("mkt-1")], capital=15.0)

    assert len(result.approved_signals) == 1
    out_sig, out_dec = result.approved_signals[0]
    assert out_sig.size == kelly_size, (
        f"coordinator must leave signal.size at Kelly's raw ${kelly_size:.2f} — "
        f"got ${out_sig.size:.2f}. Pre-shrinking it here lets "
        f"compute_bet_size()'s capital floor re-inflate the reviewer's cut."
    )
    assert out_dec.verdict == ReviewVerdict.REDUCE
    assert out_dec.suggested_size_pct == 0.3


def test_pre_floor_reduce_application_defeats_reviewer_intent():
    """Demonstrates the exact mechanism of the bug: feeding an
    already-REDUCE'd signal_size into compute_bet_size()'s survival-mode
    floor silently re-inflates it far past what the reviewer intended —
    even past Kelly's own original recommendation."""
    capital = 15.0
    kelly_size = 2.00
    suggested_size_pct = 0.3
    reviewer_intended = round(kelly_size * suggested_size_pct, 2)  # $0.60

    # Old (buggy) call convention: REDUCE already baked into signal_size
    # before compute_bet_size() ever sees it.
    old_bet_size, _ = compute_bet_size(
        capital=capital,
        signal_size=reviewer_intended,
        min_bet=3.0,
        max_bet=8.0,
        max_position_pct=0.20,
    )
    assert old_bet_size > kelly_size, (
        "sanity check on the bug: the capital floor pushes the REDUCE'd size "
        "back up past Kelly's own original (pre-reduction) recommendation"
    )
    assert old_bet_size > reviewer_intended * 4


def test_post_floor_reduce_application_preserves_reviewer_intent():
    """The fix: compute_bet_size() runs on Kelly's ORIGINAL size, then
    REDUCE's suggested_size_pct is applied afterwards via
    apply_risk_size_multiplier() (no reclamp) — same as REVIEWER_VETO,
    walk-forward and adaptive-bet multipliers."""
    capital = 15.0
    kelly_size = 2.00
    suggested_size_pct = 0.3

    bet_size, _ = compute_bet_size(
        capital=capital,
        signal_size=kelly_size,
        min_bet=3.0,
        max_bet=8.0,
        max_position_pct=0.20,
    )
    # compute_bet_size floors Kelly's raw $2.00 up to the $3.00 survival band
    # — that part is by design (keeps tiny accounts tradeable).
    assert bet_size == 3.00

    final = apply_risk_size_multiplier(bet_size, suggested_size_pct)

    assert final == pytest.approx(0.9)  # 3.00 * 0.3 — reviewer's cut fully honored
    assert final < bet_size
