"""
Regression test: ResearchAgent must publish the REAL market regime.

Bug (agents/subagents/research_agent.py::_extract_regime):
    regime = getattr(self.binance_feed, "_regime", None)

BinanceFeed has no `_regime` attribute anywhere in the codebase — its real API
is the public `get_market_regime()` (agents/binance_feed.py:1585), which is what
ArbitrageEngine.analyze() uses. So getattr() always returned None and execution
fell through to the "latest spot data" fallback, which reads a `change_4h` key
that BinanceFeed.get_signal() never returns either. Net effect: the research
regime was ALWAYS RegimeData(direction="NEUTRAL", strength=0.0), no matter how
strongly bullish/bearish the real 4h+5m regime was.

Live impact — AgentCoordinator._re_enrich_signals() copies regime_direction /
regime_strength onto every EnrichedSignal before the orchestrator's execution
loop, so with the regime pinned at NEUTRAL/0.0:
  * signal_agent_v2._detect_risk_flags() could never emit REGIME_OVEREXTENDED
    (needs strength > 0.75 — CLAUDE.md v9 OPT-1 / "Kritik Kesifler": regime
    str > 0.70 is the bot's worst historical loss zone) nor COUNTER_REGIME_NO /
    COUNTER_REGIME_YES (need direction UP/DOWN),
  * so reviewer_agent._rule_based_review()'s counter-regime VETO and its
    "regime_strength > 0.60 + NO -> REDUCE" rule were unreachable,
  * and autonomous_engine.evaluate()'s "regime > 0.80 -> size x0.50" volatility
    brake never fired on a live bet.

Fix: read the regime through get_market_regime() and map its BULLISH/BEARISH
labels onto the UP/DOWN vocabulary RegimeData and every consumer expects.
"""
import pytest

from agents.autonomous_engine import AutonomousDecisionEngine
from agents.binance_feed import BinanceFeed
from agents.subagents.research_agent import ResearchAgent, ResearchResult
from agents.subagents.reviewer_agent import (
    ReviewDecision,
    ReviewerAgent,
    ReviewVerdict,
)
from agents.subagents.signal_agent_v2 import EnrichedSignal, SignalAgentV2

QUESTION = "Bitcoin Up or Down - March 16, 7:10PM-7:15PM ET"


def _feed_with_regime(pct: float) -> BinanceFeed:
    """Real BinanceFeed with a populated cache — no network calls involved.

    pct=+3.0 -> combined = 3.0*0.6 + 1.0*0.4 = 2.2 -> BULLISH, strength 0.88.
    """
    feed = BinanceFeed()
    leg = {
        "price": 100.0,
        "intervals": {
            "4h": {"change_pct": pct},
            "5m": {"change_pct": pct / 3.0},
        },
    }
    feed._cache = {"BTCUSDT": dict(leg), "ETHUSDT": dict(leg)}
    return feed


def test_extract_regime_reads_the_live_bullish_regime():
    feed = _feed_with_regime(3.0)
    raw = feed.get_market_regime()
    assert raw["regime"] == "BULLISH" and raw["strength"] == pytest.approx(0.88)

    regime = ResearchAgent(binance_feed=feed)._extract_regime()

    assert regime.direction == "UP"          # BULLISH -> UP (RegimeData vocabulary)
    assert regime.strength == pytest.approx(0.88)
    assert regime.btc_change_4h == pytest.approx(3.0)


def test_extract_regime_reads_the_live_bearish_regime():
    feed = _feed_with_regime(-3.0)
    assert feed.get_market_regime()["regime"] == "BEARISH"

    regime = ResearchAgent(binance_feed=feed)._extract_regime()

    assert regime.direction == "DOWN"        # BEARISH -> DOWN
    assert regime.strength == pytest.approx(0.88)


def _enriched_no_signal_in_bullish_regime() -> EnrichedSignal:
    """Mirror the live enrichment path: feed -> research -> EnrichedSignal."""
    agent = ResearchAgent(binance_feed=_feed_with_regime(3.0))
    research = ResearchResult()
    research.regime = agent._extract_regime()
    ctx = research.get_market_context("cond-1", "BTCUSDT")

    sig = EnrichedSignal(
        market={"question": QUESTION, "condition_id": "cond-1"},
        condition_id="cond-1",
        direction="NO",
        edge=0.25,
        entry_price=0.45,
        size=4.0,
    )
    # exactly what AgentCoordinator._re_enrich_signals does
    sig.regime_direction = ctx["regime_direction"]
    sig.regime_strength = ctx["regime_strength"]
    sig.risk_flags = SignalAgentV2()._detect_risk_flags(sig)
    return sig


def test_overextended_counter_regime_flags_reach_the_signal():
    sig = _enriched_no_signal_in_bullish_regime()

    assert sig.regime_strength == pytest.approx(0.88)
    assert "REGIME_OVEREXTENDED" in sig.risk_flags
    assert "COUNTER_REGIME_NO" in sig.risk_flags


def test_rule_based_reviewer_vetoes_counter_regime_trade():
    sig = _enriched_no_signal_in_bullish_regime()

    decision = ReviewerAgent()._rule_based_review([sig], research=None, capital=100.0)[0]

    assert decision.verdict == ReviewVerdict.VETO
    assert "Counter-regime" in decision.reasoning


def test_autonomous_engine_applies_high_volatility_brake(tmp_path):
    sig = _enriched_no_signal_in_bullish_regime()
    sig.direction = "YES"                 # avoid the NO-specific risk paths
    sig.confluence_score = 0.80
    sig.risk_flags = SignalAgentV2()._detect_risk_flags(sig)

    engine = AutonomousDecisionEngine()
    engine.PERSISTENCE_FILE = tmp_path / "autonomous_state.json"
    decision = engine.evaluate(
        signal=sig,
        review_decision=ReviewDecision(
            condition_id="cond-1", verdict=ReviewVerdict.APPROVE, reasoning="test"
        ),
        capital=100.0,
        open_count=0,
        max_positions=5,
        closed_trades=None,
    )

    assert any("HIGH_VOLATILITY" in r for r in decision.reasoning)
    # brake caps the multiplier at 0.50, the edge>0.20 bonus then lifts it x1.2
    assert decision.size_multiplier == pytest.approx(0.6)
    assert decision.size_multiplier < 1.0  # was full size while the regime read 0.0
