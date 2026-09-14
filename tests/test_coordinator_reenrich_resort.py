"""
Regression test: `Coordinator._re_enrich_signals()` must re-sort
`signal_result.signals` by the freshly recomputed `confluence_score`, not
leave the list in its pre-research, edge-only order.

Bug: `SignalAgentV2.run()` sorts `enriched` by `confluence_score` (line ~166)
*before* any research data exists — PHASE 1 runs the signal agent and the
research agent in parallel, so every whale/smart-money/regime/orderflow
component of the confluence formula is still at its neutral default at that
point. PHASE 2's `_re_enrich_signals()` then overwrites each signal's real
research fields and recomputes `sig.confluence_score` in place — a
materially different, now fully-informed number — but never re-sorts the
list before returning it.

`ReviewerAgent.get_approved_signals()` iterates `signals` in that same
(stale) order, and the orchestrator's execution loop
(`agents/orchestrator.py`, `MAX_DIRECTIONAL` / cycle risk budget) walks
`coord_result.approved_signals` in order and `break`s once a cap is hit. So
when multiple signals compete for the same limited slots, whichever one
happened to score highest before research existed gets traded — even when a
later signal in the list is the one every real signal (whale, smart money,
regime, order flow) actually confirms.

Fix: sort `signal_result.signals` by `confluence_score` again at the end of
`_re_enrich_signals()`.
"""
from __future__ import annotations

from types import SimpleNamespace

from agents.subagents.coordinator import AgentCoordinator
from agents.subagents.signal_agent_v2 import EnrichedSignal, SignalResult


def _signal(condition_id: str, pre_confluence: float) -> EnrichedSignal:
    return EnrichedSignal(
        market={"condition_id": condition_id, "question": "Bitcoin Up or Down"},
        condition_id=condition_id,
        direction="YES",
        confluence_score=pre_confluence,
    )


def test_re_enrich_signals_resorts_by_post_enrichment_confluence():
    coord = AgentCoordinator.__new__(AgentCoordinator)

    # Pre-enrichment (edge-only) order: A ahead of B, matching how
    # SignalAgentV2.run() would have sorted them before research existed.
    sig_a = _signal("A", pre_confluence=0.61)
    sig_b = _signal("B", pre_confluence=0.56)
    signal_result = SignalResult(signals=[sig_a, sig_b], total_candidates=2, total_signals=2)

    # Stub signal_agent: recomputation reverses the ranking, mirroring the
    # concrete scenario from the audit (whale/smart-money/regime/orderflow
    # all confirm B once real research data is folded in, none confirm A).
    post_confluence = {"A": 0.51, "B": 0.74}
    coord.signal_agent = SimpleNamespace(
        _compute_confluence=lambda sig: post_confluence[sig.condition_id],
        _detect_risk_flags=lambda sig: [],
    )

    research = SimpleNamespace(get_market_context=lambda condition_id, symbol="": {})

    result = coord._re_enrich_signals(signal_result, research)

    assert [s.condition_id for s in result.signals] == ["B", "A"], (
        "signals must be re-sorted by post-enrichment confluence_score "
        "(B=0.74 > A=0.51), not left in the pre-research edge-only order "
        "(A=0.61 > B=0.56) — otherwise MAX_DIRECTIONAL/cycle-budget caps in "
        "the orchestrator pick the wrong trade when signals compete for the "
        "same slot"
    )
    assert result.signals[0].confluence_score == 0.74
    assert result.signals[1].confluence_score == 0.51
