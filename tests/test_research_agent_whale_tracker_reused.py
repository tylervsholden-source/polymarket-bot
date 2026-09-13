"""
Regression test: ResearchAgent must reuse a single WhaleTracker instance
across cycles instead of constructing a fresh one on every call.

Bug: agents/subagents/research_agent.py::_fetch_whale_data() did
    tracker = self._whale_tracker_cls()
on every call. ResearchAgent.run() calls _fetch_whale_data() whenever there
are any candidate markets — which is true on essentially every orchestrator
cycle (60-120s per CLAUDE.md) while crypto up/down markets are being scanned.
agents/whale_tracker.py::WhaleTracker.__init__() opens its own
httpx.AsyncClient(timeout=15) and never closes it (no close()/aclose()
method exists on the class at all). Every other long-lived HTTP-backed
component in this codebase (BinanceFeed, SmartTraderTracker, ArbitrageEngine,
PolymarketClient) is instead constructed once at orchestrator startup and
its session reused for the process lifetime — WhaleTracker was the only
one re-created per call, silently leaking one open httpx client (and its
underlying connection pool / sockets) per cycle for the entire 20-day run
this bot is meant to survive unattended.

Fix: cache the tracker instance on self._whale_tracker and only build it
once (lazily, on first use), matching the reuse pattern used everywhere
else in the codebase.
"""
from __future__ import annotations

import asyncio

import pytest

from agents.subagents.research_agent import ResearchAgent


class _CountingWhaleTracker:
    """Fake WhaleTracker that counts how many instances get constructed."""

    instances_created = 0

    def __init__(self):
        _CountingWhaleTracker.instances_created += 1

    async def get_activity(self, condition_id: str) -> dict:
        return {
            "direction": "NEUTRAL",
            "large_buys": 0,
            "large_sells": 0,
            "smart_money_buys": 0,
            "smart_money_sells": 0,
            "volume_surge": 1.0,
            "whale_alignment": "NEUTRAL",
        }


def test_whale_tracker_constructed_once_across_multiple_research_cycles():
    _CountingWhaleTracker.instances_created = 0
    agent = ResearchAgent(whale_tracker_cls=_CountingWhaleTracker)

    candidates = [{"condition_id": "0xabc"}, {"condition_id": "0xdef"}]

    # Simulate several orchestrator cycles worth of ResearchAgent.run() calls.
    for _ in range(5):
        asyncio.run(agent.run(candidates=candidates, symbols=set()))

    assert _CountingWhaleTracker.instances_created == 1, (
        "WhaleTracker must be constructed once and reused across cycles — "
        f"got {_CountingWhaleTracker.instances_created} instances for 5 "
        "cycles, meaning each cycle leaks a fresh unclosed httpx.AsyncClient"
    )
