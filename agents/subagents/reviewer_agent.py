"""
ReviewerAgent — Claude API ile trade kararini dogrulama/veto.

Her trade sinyalini Claude'a gonderir ve su sorulari sorar:
1. Edge gercek mi yoksa yaniltici mi?
2. Risk flag'leri ciddiye alinmali mi?
3. Pozisyon buyuklugu mantikli mi?
4. Timing uygun mu?

Cikti: ReviewDecision — APPROVE / VETO / REDUCE + reasoning.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from loguru import logger

from agents.subagents.base_agent import BaseAgent
from agents.subagents.signal_agent_v2 import EnrichedSignal, SignalResult
from agents.subagents.research_agent import ResearchResult

# Anthropic API
try:
    import anthropic
    _HAS_ANTHROPIC = True
except ImportError:
    _HAS_ANTHROPIC = False


class ReviewVerdict(str, Enum):
    APPROVE = "APPROVE"           # Trade'i onayla
    VETO = "VETO"                 # Trade'i reddet
    REDUCE = "REDUCE"             # Pozisyon kucult
    APPROVE_WITH_WARNING = "APPROVE_WITH_WARNING"  # Onayla ama dikkatli ol


@dataclass
class ReviewDecision:
    """Reviewer agent'in tek bir sinyal icin verdigi karar."""
    condition_id: str = ""
    verdict: ReviewVerdict = ReviewVerdict.VETO
    confidence: float = 0.5       # 0.0 - 1.0
    reasoning: str = ""
    suggested_size_pct: float = 1.0  # 0.0 - 1.0 (REDUCE icin)
    risk_assessment: str = ""
    timestamp: float = field(default_factory=time.time)

    @property
    def approved(self) -> bool:
        return self.verdict in (ReviewVerdict.APPROVE, ReviewVerdict.APPROVE_WITH_WARNING, ReviewVerdict.REDUCE)


@dataclass
class ReviewBatchResult:
    """Tum sinyallerin review sonuclari."""
    decisions: list[ReviewDecision] = field(default_factory=list)
    approved_count: int = 0
    vetoed_count: int = 0
    reduced_count: int = 0
    total_reviewed: int = 0
    api_calls: int = 0
    total_tokens: int = 0
    timestamp: float = field(default_factory=time.time)

    def get_approved_signals(
        self, signals: list[EnrichedSignal]
    ) -> list[tuple[EnrichedSignal, ReviewDecision]]:
        """Onaylanan sinyalleri review karariyla birlikte doner."""
        approved = []
        decision_map = {d.condition_id: d for d in self.decisions}
        for sig in signals:
            dec = decision_map.get(sig.condition_id)
            if dec and dec.approved:
                approved.append((sig, dec))
        return approved


# ── SYSTEM PROMPT ───────────────────────────────────────────────────────

REVIEWER_SYSTEM_PROMPT = """You are a senior quantitative trader reviewing proposed trades for a Polymarket prediction market bot.

Your job is to critically evaluate each trade proposal and decide: APPROVE, VETO, or REDUCE.

## Your Decision Framework

### APPROVE when:
- Edge is genuine (>0.08 for YES, >0.15 for NO)
- Multiple independent signals align (whale + smart money + technical)
- Risk flags are manageable
- Position size is appropriate for the edge level

### VETO when:
- Edge appears artificial or based on stale data
- Critical risk flags: REGIME_OVEREXTENDED + COUNTER_REGIME, WHALE_OPPOSITION + thin edge
- The trade contradicts strong on-chain evidence
- RSI extreme + counter-regime direction
- NO direction with <0.10 edge (historically 33% WR below 0.10)

### REDUCE when:
- Edge is real but risk flags suggest caution
- Trade is directionally correct but sizing is aggressive
- Moderate confluence but some contradicting signals

## Historical Context
- YES trades: 72% WR historically, reliable
- NO trades: 33% WR historically, very risky — need extra conviction
- 5-minute YES: 79% WR (gold standard)
- Regime strength >0.70 correlates with losses
- Bayesian overconfidence at prob >0.80 is documented

## Response Format
For EACH trade, respond with EXACTLY this JSON format:
{
  "verdict": "APPROVE" | "VETO" | "REDUCE",
  "confidence": 0.0-1.0,
  "suggested_size_pct": 0.0-1.0,
  "reasoning": "one paragraph explaining your decision",
  "risk_assessment": "brief risk summary"
}

If multiple trades, return a JSON array of these objects.
Be concise. No explanations outside the JSON."""


class ReviewerAgent(BaseAgent):
    """Claude API powered trade reviewer."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        max_tokens: int = 1500,
        timeout_seconds: float = 30.0,
        max_concurrent_reviews: int = 3,
    ):
        super().__init__(name="ReviewerAgent", timeout_seconds=timeout_seconds)
        self.model = model
        self.max_tokens = max_tokens
        self.max_concurrent = max_concurrent_reviews
        self._client: anthropic.AsyncAnthropic | None = None
        self._total_tokens = 0
        self._total_calls = 0

    def _ensure_client(self) -> bool:
        """Anthropic client'i lazy initialize et."""
        if not _HAS_ANTHROPIC:
            logger.warning("[ReviewerAgent] anthropic package not installed")
            return False
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            logger.warning("[ReviewerAgent] ANTHROPIC_API_KEY not set")
            return False
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(api_key=api_key)
        return True

    async def run(self, **kwargs) -> ReviewBatchResult:
        """
        kwargs:
            signal_result: SignalResult  — signal agent ciktisi
            research: ResearchResult     — research agent ciktisi
            capital: float               — mevcut sermaye
        """
        signal_result: SignalResult = kwargs.get("signal_result", SignalResult())
        research: ResearchResult | None = kwargs.get("research", None)
        capital: float = kwargs.get("capital", 100.0)

        signals = signal_result.signals
        if not signals:
            return ReviewBatchResult()

        has_api = self._ensure_client()

        batch_result = ReviewBatchResult(total_reviewed=len(signals))

        if has_api:
            # Claude API review
            decisions = await self._review_with_claude(signals, research, capital)
        else:
            # Fallback: rule-based review
            logger.info("[ReviewerAgent] No API — falling back to rule-based review")
            decisions = self._rule_based_review(signals, research, capital)

        batch_result.decisions = decisions
        batch_result.approved_count = sum(1 for d in decisions if d.verdict == ReviewVerdict.APPROVE)
        batch_result.vetoed_count = sum(1 for d in decisions if d.verdict == ReviewVerdict.VETO)
        batch_result.reduced_count = sum(1 for d in decisions if d.verdict == ReviewVerdict.REDUCE)
        batch_result.api_calls = self._total_calls
        batch_result.total_tokens = self._total_tokens

        logger.info(
            f"[ReviewerAgent] {batch_result.approved_count} approved, "
            f"{batch_result.vetoed_count} vetoed, {batch_result.reduced_count} reduced "
            f"(API calls={self._total_calls}, tokens={self._total_tokens})"
        )

        return batch_result

    # ── CLAUDE API REVIEW ───────────────────────────────────────────────

    async def _review_with_claude(
        self,
        signals: list[EnrichedSignal],
        research: ResearchResult | None,
        capital: float,
    ) -> list[ReviewDecision]:
        """Tum sinyalleri tek bir Claude cagrisinda review et."""
        # Build the review prompt
        aggregate = research.get_aggregate_sentiment() if research else {}
        trade_summaries = []
        for i, sig in enumerate(signals):
            trade_summaries.append(f"--- Trade #{i+1} ---\n{sig.to_review_summary()}")

        user_prompt = (
            f"## Portfolio Context\n"
            f"Capital: ${capital:.2f}\n"
            f"Aggregate Sentiment: {aggregate}\n\n"
            f"## Trades to Review ({len(signals)} total)\n\n"
            + "\n\n".join(trade_summaries)
            + "\n\nReview each trade and return your decisions as a JSON array."
        )

        try:
            response = await self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=REVIEWER_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )

            self._total_calls += 1
            self._total_tokens += response.usage.input_tokens + response.usage.output_tokens

            # Parse response
            text = response.content[0].text if response.content else ""
            return self._parse_claude_response(text, signals)

        except Exception as e:
            logger.error(f"[ReviewerAgent] Claude API error: {e}")
            # Fallback to rule-based
            return self._rule_based_review(signals, research, capital)

    def _parse_claude_response(
        self, text: str, signals: list[EnrichedSignal]
    ) -> list[ReviewDecision]:
        """Claude'un JSON cevabini parse et."""
        import json

        decisions = []

        try:
            # Try to extract JSON from response
            # Handle both single object and array
            text = text.strip()
            if text.startswith("```"):
                # Strip markdown code blocks
                lines = text.split("\n")
                text = "\n".join(
                    l for l in lines
                    if not l.strip().startswith("```")
                )

            parsed = json.loads(text)
            if isinstance(parsed, dict):
                parsed = [parsed]

            for i, (item, sig) in enumerate(zip(parsed, signals)):
                verdict_str = item.get("verdict", "VETO").upper()
                try:
                    verdict = ReviewVerdict(verdict_str)
                except ValueError:
                    verdict = ReviewVerdict.VETO

                decisions.append(ReviewDecision(
                    condition_id=sig.condition_id,
                    verdict=verdict,
                    confidence=float(item.get("confidence", 0.5)),
                    reasoning=item.get("reasoning", ""),
                    suggested_size_pct=float(item.get("suggested_size_pct", 1.0)),
                    risk_assessment=item.get("risk_assessment", ""),
                ))

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"[ReviewerAgent] JSON parse error: {e}, using rule-based fallback")
            # Fallback: parse as text
            for sig in signals:
                if any(
                    kw in text.upper()
                    for kw in ["APPROVE", "ACCEPT", "GO AHEAD"]
                ):
                    decisions.append(ReviewDecision(
                        condition_id=sig.condition_id,
                        verdict=ReviewVerdict.APPROVE_WITH_WARNING,
                        confidence=0.5,
                        reasoning=f"Parsed from text (JSON failed): {text[:200]}",
                    ))
                else:
                    decisions.append(ReviewDecision(
                        condition_id=sig.condition_id,
                        verdict=ReviewVerdict.VETO,
                        confidence=0.5,
                        reasoning=f"JSON parse failed, defaulting to VETO: {text[:200]}",
                    ))

        # Fill missing signals with VETO
        reviewed_cids = {d.condition_id for d in decisions}
        for sig in signals:
            if sig.condition_id not in reviewed_cids:
                decisions.append(ReviewDecision(
                    condition_id=sig.condition_id,
                    verdict=ReviewVerdict.VETO,
                    reasoning="Not reviewed by Claude (missing from response)",
                ))

        return decisions

    # ── RULE-BASED FALLBACK ─────────────────────────────────────────────

    def _rule_based_review(
        self,
        signals: list[EnrichedSignal],
        research: ResearchResult | None,
        capital: float,
    ) -> list[ReviewDecision]:
        """Anthropic API yoksa rule-based review."""
        decisions = []

        for sig in signals:
            verdict = ReviewVerdict.APPROVE
            reasons = []
            size_pct = 1.0

            # ── VETO CONDITIONS ──

            # NO with thin edge (33% historical WR)
            if sig.direction == "NO" and sig.edge < 0.15:
                verdict = ReviewVerdict.VETO
                reasons.append(f"NO edge {sig.edge:.3f} < 0.15 (33% WR history)")

            # Regime overextended + counter-trade
            # signal_agent_v2._detect_risk_flags only ever emits COUNTER_REGIME_NO /
            # COUNTER_REGIME_YES, never bare "COUNTER_REGIME" — match by prefix so
            # this VETO can actually fire (an exact match here is always false).
            if "REGIME_OVEREXTENDED" in sig.risk_flags and any(
                f.startswith("COUNTER_REGIME") for f in sig.risk_flags
            ):
                verdict = ReviewVerdict.VETO
                reasons.append("Counter-regime trade in overextended regime")

            # Whale opposition + thin edge
            # FIX-A: Use exact match for risk flag checking
            if any(f == "WHALE_OPPOSITION" for f in sig.risk_flags) and sig.edge < 0.12:
                verdict = ReviewVerdict.VETO
                reasons.append("Whale opposition with thin edge")

            # RSI extreme counter-trade
            # FIX-A: Use exact match for RSI risk flags
            if any(f == "RSI_OVERBOUGHT" for f in sig.risk_flags) or any(f == "RSI_OVERSOLD" for f in sig.risk_flags):
                if sig.confluence_score < 0.6:
                    verdict = ReviewVerdict.VETO
                    reasons.append("RSI extreme with low confluence")

            # ── REDUCE CONDITIONS (only if not already VETO) ──
            if verdict != ReviewVerdict.VETO:
                # Multiple risk flags
                if len(sig.risk_flags) >= 3:
                    verdict = ReviewVerdict.REDUCE
                    size_pct = 0.5
                    reasons.append(f"{len(sig.risk_flags)} risk flags, reducing 50%")

                # Low confluence
                elif sig.confluence_score < 0.4:
                    verdict = ReviewVerdict.REDUCE
                    size_pct = 0.7
                    reasons.append(f"Low confluence {sig.confluence_score:.2f}, reducing 30%")

                # Regime strength high but not overextended
                elif sig.regime_strength > 0.60 and sig.direction == "NO":
                    verdict = ReviewVerdict.REDUCE
                    size_pct = 0.6
                    reasons.append("High regime strength, reducing NO size 40%")

            # ── APPROVE CONDITIONS ──
            if verdict == ReviewVerdict.APPROVE:
                if sig.confluence_score > 0.7 and sig.edge > 0.12:
                    reasons.append("High confluence + strong edge")
                elif sig.direction == "YES" and sig.edge > 0.08:
                    reasons.append("Standard YES approval")
                else:
                    reasons.append("Passed all checks")

            decisions.append(ReviewDecision(
                condition_id=sig.condition_id,
                verdict=verdict,
                confidence=sig.confluence_score,
                reasoning=" | ".join(reasons) if reasons else "No specific reason",
                suggested_size_pct=size_pct,
                risk_assessment=", ".join(sig.risk_flags) if sig.risk_flags else "Clean",
            ))

        return decisions
