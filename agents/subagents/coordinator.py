"""
AgentCoordinator — Multi-agent orchestration hub.

Hybrid communication pattern:
1. ResearchAgent + SignalAgent PARALEL calisir (async gather)
2. Signal sonuclari research context ile zenginlestirilir (merge)
3. ReviewerAgent SEQUENTIAL calisir (merge sonucunu alir)
4. Final: onaylanan sinyaller doner

Veri akisi:
    ┌─────────────────┐     ┌─────────────────┐
    │  ResearchAgent   │     │   SignalAgent    │
    │  (whale+smart+   │     │  (6-model arb    │
    │   regime+onchain)│     │   engine)        │
    └────────┬────────┘     └────────┬────────┘
             │    PARALLEL            │
             └──────────┬─────────────┘
                        │ MERGE
                        ▼
              ┌──────────────────┐
              │  Enriched Signals │
              │  (signal+context) │
              └────────┬─────────┘
                       │ SEQUENTIAL
                       ▼
              ┌──────────────────┐
              │  ReviewerAgent   │
              │  (Claude API     │
              │   veto/approve)  │
              └────────┬─────────┘
                       │
                       ▼
              ┌──────────────────┐
              │ Approved Signals  │
              │ (ready to execute)│
              └──────────────────┘
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from agents.subagents.base_agent import AgentResult, AgentStatus
from agents.subagents.research_agent import ResearchAgent, ResearchResult
from agents.subagents.signal_agent_v2 import SignalAgentV2, SignalResult, EnrichedSignal
from agents.subagents.reviewer_agent import (
    ReviewerAgent,
    ReviewBatchResult,
    ReviewDecision,
    ReviewVerdict,
)
from agents.subagents.orderflow_agent import OrderFlowAgent, OrderFlowData


@dataclass
class CoordinatorResult:
    """Coordinator'un final ciktisi."""
    # Approved signals (ready to execute)
    approved_signals: list[tuple[EnrichedSignal, ReviewDecision]] = field(default_factory=list)

    # Full pipeline results
    research_result: ResearchResult | None = None
    signal_result: SignalResult | None = None
    review_result: ReviewBatchResult | None = None

    # Pipeline metadata
    total_candidates: int = 0
    total_signals: int = 0
    total_approved: int = 0
    total_vetoed: int = 0
    pipeline_duration_ms: float = 0.0
    agent_durations: dict[str, float] = field(default_factory=dict)

    # Per-agent status
    research_status: str = "SKIPPED"
    signal_status: str = "SKIPPED"
    review_status: str = "SKIPPED"

    @property
    def has_trades(self) -> bool:
        return len(self.approved_signals) > 0

    def summary(self) -> str:
        """Human-readable pipeline summary."""
        lines = [
            f"Pipeline: {self.pipeline_duration_ms:.0f}ms total",
            f"  Research: {self.research_status} ({self.agent_durations.get('ResearchAgent', 0):.0f}ms)",
            f"  Signal:   {self.signal_status} ({self.agent_durations.get('SignalAgent', 0):.0f}ms)",
            f"  Review:   {self.review_status} ({self.agent_durations.get('ReviewerAgent', 0):.0f}ms)",
            f"  Candidates: {self.total_candidates} → Signals: {self.total_signals} → Approved: {self.total_approved}",
        ]
        if self.approved_signals:
            for sig, dec in self.approved_signals:
                lines.append(
                    f"    ✓ {sig.direction} {sig.market.get('question', '')[:50]} "
                    f"edge={sig.edge:.3f} size=${sig.size:.2f} "
                    f"verdict={dec.verdict.value}"
                )
        return "\n".join(lines)


class AgentCoordinator:
    """
    Multi-agent coordinator with hybrid communication.

    Usage:
        coordinator = AgentCoordinator(
            binance_feed=feed,
            arb_engine=engine,
            smart_tracker=tracker,
        )
        result = await coordinator.run_cycle(candidates, capital)
        for signal, decision in result.approved_signals:
            # execute trade...
    """

    def __init__(
        self,
        binance_feed=None,
        arb_engine=None,
        smart_tracker=None,
        whale_tracker_cls=None,
        enhanced_signals=None,
        reviewer_model: str = "claude-sonnet-4-20250514",
        enable_research: bool = True,
        enable_review: bool = True,
        enable_orderflow: bool = True,
    ):
        # ── Initialize subagents ──
        self.research_agent = ResearchAgent(
            binance_feed=binance_feed,
            smart_tracker=smart_tracker,
            whale_tracker_cls=whale_tracker_cls,
            enhanced_signals=enhanced_signals,
            timeout_seconds=25.0,
        )

        self.signal_agent = SignalAgentV2(
            arb_engine=arb_engine,
            timeout_seconds=45.0,
        )

        self.reviewer_agent = ReviewerAgent(
            model=reviewer_model,
            timeout_seconds=30.0,
        )

        self.orderflow_agent = OrderFlowAgent(
            timeout_seconds=15.0,
        )

        self.enable_research = enable_research
        self.enable_review = enable_review
        self.enable_orderflow = enable_orderflow

        # Pipeline stats
        self._cycle_count = 0
        self._total_approved = 0
        self._total_vetoed = 0
        self._avg_pipeline_ms = 0.0

    async def run_cycle(
        self,
        candidates: list[dict],
        capital: float,
        symbols: set[str] | None = None,
    ) -> CoordinatorResult:
        """
        Full multi-agent pipeline for one orchestrator cycle.

        Args:
            candidates: Pre-filtered market candidates
            capital: Available trading capital
            symbols: Unique Binance symbols to fetch (optional)

        Returns:
            CoordinatorResult with approved signals
        """
        pipeline_start = time.monotonic()
        self._cycle_count += 1
        result = CoordinatorResult(total_candidates=len(candidates))

        if not candidates:
            logger.info("[Coordinator] No candidates, skipping pipeline.")
            return result

        if symbols is None:
            symbols = self._extract_symbols(candidates)

        # ═══════════════════════════════════════════════════════════════
        # PHASE 1: PARALLEL — Research + Signal (wait_for both)
        # ═══════════════════════════════════════════════════════════════
        logger.info(
            f"[Coordinator] Cycle #{self._cycle_count}: "
            f"{len(candidates)} candidates, ${capital:.2f} capital"
        )

        research_result: ResearchResult | None = None
        signal_result: SignalResult | None = None

        if self.enable_research:
            # Hybrid: Research, Signal, and OrderFlow run in parallel
            research_task = self.research_agent.execute(
                candidates=candidates, symbols=symbols
            )
            signal_task = self.signal_agent.execute(
                candidates=candidates, capital=capital
            )

            # OrderFlow runs in parallel too (independent Binance data)
            parallel_tasks = [research_task, signal_task]
            if self.enable_orderflow:
                orderflow_task = self.orderflow_agent.execute(symbols=symbols)
                parallel_tasks.append(orderflow_task)

            gathered = await asyncio.gather(*parallel_tasks)

            research_out = gathered[0]
            signal_out = gathered[1]
            orderflow_out = gathered[2] if self.enable_orderflow else None

            # Process research result
            if research_out.ok:
                research_result = research_out.data
                result.research_status = "OK"
            else:
                result.research_status = research_out.status.value
                logger.warning(f"[Coordinator] Research failed: {research_out.error}")

            result.agent_durations["ResearchAgent"] = research_out.duration_ms

            # Process signal result
            if signal_out.ok:
                signal_result = signal_out.data
                result.signal_status = "OK"
            else:
                result.signal_status = signal_out.status.value
                logger.warning(f"[Coordinator] Signal failed: {signal_out.error}")

            result.agent_durations["SignalAgent"] = signal_out.duration_ms

            # Merge OrderFlow into ResearchResult
            if orderflow_out and orderflow_out.ok and research_result:
                research_result.orderflow_data = orderflow_out.data or {}
                result.agent_durations["OrderFlowAgent"] = orderflow_out.duration_ms
                logger.info(
                    f"[Coordinator] OrderFlow merged: {len(research_result.orderflow_data)} symbols"
                )
            elif orderflow_out and not orderflow_out.ok:
                logger.warning(f"[Coordinator] OrderFlow failed: {orderflow_out.error}")
                result.agent_durations["OrderFlowAgent"] = orderflow_out.duration_ms

        else:
            # Research disabled — only run signal agent
            signal_out = await self.signal_agent.execute(
                candidates=candidates, capital=capital
            )
            if signal_out.ok:
                signal_result = signal_out.data
                result.signal_status = "OK"
            else:
                result.signal_status = signal_out.status.value

            result.research_status = "DISABLED"
            result.agent_durations["SignalAgent"] = signal_out.duration_ms

        # No signals? Return early
        if not signal_result or not signal_result.has_signals:
            result.pipeline_duration_ms = (time.monotonic() - pipeline_start) * 1000
            logger.info("[Coordinator] No signals produced, pipeline done.")
            return result

        # ═══════════════════════════════════════════════════════════════
        # PHASE 2: MERGE — Enrich signals with research context
        # ═══════════════════════════════════════════════════════════════
        if research_result and signal_result:
            # Re-enrich with fresh research data (signal agent may have
            # used stale data if research was still running)
            signal_result = self._re_enrich_signals(signal_result, research_result)

        result.signal_result = signal_result
        result.research_result = research_result
        result.total_signals = signal_result.total_signals

        # ═══════════════════════════════════════════════════════════════
        # PHASE 3: SEQUENTIAL — Reviewer Agent
        # ═══════════════════════════════════════════════════════════════
        if self.enable_review and signal_result.has_signals:
            review_out = await self.reviewer_agent.execute(
                signal_result=signal_result,
                research=research_result,
                capital=capital,
            )

            if review_out.ok:
                review_result: ReviewBatchResult = review_out.data
                result.review_result = review_result
                result.review_status = "OK"

                # Extract approved signals
                result.approved_signals = review_result.get_approved_signals(
                    signal_result.signals
                )

                # Apply size reduction for REDUCE verdicts
                for i, (sig, dec) in enumerate(result.approved_signals):
                    if dec.verdict == ReviewVerdict.REDUCE:
                        original = sig.size
                        sig.size = round(sig.size * dec.suggested_size_pct, 2)
                        logger.info(
                            f"[Coordinator] REDUCE: {sig.condition_id[:20]} "
                            f"${original:.2f} → ${sig.size:.2f} ({dec.suggested_size_pct:.0%})"
                        )

                result.total_approved = len(result.approved_signals)
                result.total_vetoed = review_result.vetoed_count

            else:
                result.review_status = review_out.status.value
                logger.warning(
                    f"[Coordinator] Review failed: {review_out.error}, "
                    "applying stricter rules due to API failure"
                )
                # FIX-B: When reviewer API fails, apply stricter filtering
                # - Block ALL NO trades (33% WR — not worth risk without review)
                # - Block YES trades with edge < 0.08 (aligned with engine min_edge_yes)
                # - Auto-approve YES with edge >= 0.08
                filtered_signals = []
                for sig in signal_result.signals:
                    if sig.direction == "NO":
                        logger.warning(
                            f"[Coordinator] FIX-B NO block (API down): "
                            f"{sig.condition_id[:20]} edge={sig.edge:.3f}"
                        )
                        continue
                    if sig.edge < 0.08:
                        logger.warning(
                            f"[Coordinator] FIX-B weak edge block (API down): "
                            f"{sig.condition_id[:20]} edge={sig.edge:.3f} < 0.08"
                        )
                        continue
                    # YES with edge >= 0.08 — auto-approve with warning
                    filtered_signals.append((sig, ReviewDecision(
                        condition_id=sig.condition_id,
                        verdict=ReviewVerdict.APPROVE_WITH_WARNING,
                        reasoning="Reviewer API failed; auto-approved only strong YES trades (FIX-B)",
                    )))

                result.approved_signals = filtered_signals
                result.total_approved = len(result.approved_signals)

            result.agent_durations["ReviewerAgent"] = review_out.duration_ms

        else:
            # Review disabled — approve all
            result.review_status = "DISABLED"
            result.approved_signals = [
                (sig, ReviewDecision(
                    condition_id=sig.condition_id,
                    verdict=ReviewVerdict.APPROVE,
                    reasoning="Review disabled",
                ))
                for sig in signal_result.signals
            ]
            result.total_approved = len(result.approved_signals)

        # ═══════════════════════════════════════════════════════════════
        # FINALIZE
        # ═══════════════════════════════════════════════════════════════
        result.pipeline_duration_ms = (time.monotonic() - pipeline_start) * 1000
        self._total_approved += result.total_approved
        self._total_vetoed += result.total_vetoed

        logger.info(f"[Coordinator] Pipeline complete:\n{result.summary()}")

        return result

    # ── HELPER METHODS ──────────────────────────────────────────────────

    def _re_enrich_signals(
        self, signal_result: SignalResult, research: ResearchResult
    ) -> SignalResult:
        """Signal'leri taze research data ile yeniden zenginlestir."""
        for sig in signal_result.signals:
            symbol = SignalAgentV2._detect_symbol(
                sig.market.get("question", "")
            )
            ctx = research.get_market_context(sig.condition_id, symbol)

            sig.whale_direction = ctx.get("whale_direction", sig.whale_direction)
            sig.whale_volume_surge = ctx.get("whale_volume_surge", sig.whale_volume_surge)
            sig.smart_money_signal = ctx.get("smart_money_signal", sig.smart_money_signal)
            sig.regime_direction = ctx.get("regime_direction", sig.regime_direction)
            sig.regime_strength = ctx.get("regime_strength", sig.regime_strength)
            sig.fear_greed = ctx.get("fear_greed", sig.fear_greed)
            sig.social_sentiment = ctx.get("social_sentiment", sig.social_sentiment)
            sig.spot_rsi = ctx.get("spot_rsi", sig.spot_rsi)

            # Order flow enrichment
            sig.orderflow_bias = ctx.get("orderflow_bias", sig.orderflow_bias)
            sig.orderflow_label = ctx.get("orderflow_label", sig.orderflow_label)
            sig.orderflow_obi = ctx.get("orderflow_obi", sig.orderflow_obi)
            sig.orderflow_cvd = ctx.get("orderflow_cvd", sig.orderflow_cvd)
            sig.orderflow_confidence = ctx.get("orderflow_confidence", sig.orderflow_confidence)

            # Recompute confluence with fresh data (now includes order flow)
            sig.confluence_score = self.signal_agent._compute_confluence(sig)
            sig.risk_flags = self.signal_agent._detect_risk_flags(sig)

        return signal_result

    def _extract_symbols(self, candidates: list[dict]) -> set[str]:
        """Candidate'lardan unique Binance sembollerini cikar."""
        symbols = set()
        for m in candidates:
            sym = SignalAgentV2._detect_symbol(m.get("question", ""))
            if sym:
                symbols.add(sym)
        return symbols

    def get_stats(self) -> dict:
        """Coordinator istatistikleri."""
        return {
            "cycles": self._cycle_count,
            "total_approved": self._total_approved,
            "total_vetoed": self._total_vetoed,
            "approval_rate": (
                self._total_approved / max(self._total_approved + self._total_vetoed, 1)
            ),
            "research_avg_ms": self.research_agent.avg_duration_ms,
            "signal_avg_ms": self.signal_agent.avg_duration_ms,
            "reviewer_avg_ms": self.reviewer_agent.avg_duration_ms,
            "reviewer_api_calls": self.reviewer_agent._total_calls,
            "reviewer_total_tokens": self.reviewer_agent._total_tokens,
        }
