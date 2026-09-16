"""
Regression test (55th daily review): the ReviewerAgent risk layer was
silently disabled by default.

agents/orchestrator.py wired the coordinator's enable_review flag as:

    enable_review=os.getenv("ENABLE_REVIEWER_AGENT", "false").lower() == "true"

but:
  - CLAUDE.md documents ENABLE_REVIEWER_AGENT=true as the setting to use
  - AgentCoordinator.__init__ itself defaults enable_review: bool = True
  - .env.example never mentioned the variable at all

So any deployment that didn't explicitly export ENABLE_REVIEWER_AGENT ran
with review completely off. AgentCoordinator.run_cycle()'s `else` branch
for a disabled reviewer auto-approves every signal with
ReviewDecision(verdict=APPROVE, reasoning="Review disabled") and never
runs even the rule-based FIX-B fallback that normally applies when the
Claude API itself fails — the entire risk-review layer (VETO, REDUCE,
whale/regime/RSI checks) that at least 5 prior daily reviews hardened
never executes at all.

Fix: default the env var to "true", matching the documented behavior and
the coordinator's own class default.
"""
import os

import pytest


@pytest.fixture(autouse=True)
def _clear_agent_env(monkeypatch):
    monkeypatch.delenv("ENABLE_REVIEWER_AGENT", raising=False)
    for key in list(os.environ):
        if key.startswith("POLYMARKET_") or key == "ANTHROPIC_API_KEY":
            monkeypatch.delenv(key, raising=False)


def test_orchestrator_defaults_reviewer_agent_enabled():
    from agents.orchestrator import Orchestrator

    orch = Orchestrator()
    assert orch.coordinator.enable_review is True


def test_orchestrator_respects_explicit_disable(monkeypatch):
    from agents.orchestrator import Orchestrator

    monkeypatch.setenv("ENABLE_REVIEWER_AGENT", "false")
    orch = Orchestrator()
    assert orch.coordinator.enable_review is False
