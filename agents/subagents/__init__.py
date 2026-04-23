"""
Subagent Orchestration — Multi-agent trading architecture.

ResearchAgent    : Piyasa durumu, whale, on-chain, sentiment
SignalAgent      : Bayesian + teknik sinyal uretimi (ArbitrageEngine wrapper)
ReviewerAgent    : Claude API ile trade karari dogrulama/veto
OrderFlowAgent   : Binance order book + trade flow analizi (11 indikatör)
AgentCoordinator : Spawn + merge + hybrid communication
"""
from agents.subagents.base_agent import BaseAgent, AgentResult
from agents.subagents.research_agent import ResearchAgent, ResearchResult
from agents.subagents.signal_agent_v2 import SignalAgentV2, SignalResult
from agents.subagents.reviewer_agent import ReviewerAgent, ReviewDecision, ReviewVerdict
from agents.subagents.orderflow_agent import OrderFlowAgent, OrderFlowData
from agents.subagents.coordinator import AgentCoordinator

__all__ = [
    "BaseAgent", "AgentResult",
    "ResearchAgent", "ResearchResult",
    "SignalAgentV2", "SignalResult",
    "ReviewerAgent", "ReviewDecision", "ReviewVerdict",
    "OrderFlowAgent", "OrderFlowData",
    "AgentCoordinator",
]
