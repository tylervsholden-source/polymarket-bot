"""
SignalAgent V2 — ArbitrageEngine wrapper as a subagent.

Mevcut 6-model pipeline'i (Bayesian + Edge + Spread + Stoikov + Kelly + Monte Carlo)
subagent formatinda sarar. ResearchAgent'tan gelen context ile zenginlestirir.

Cikti: SignalResult — trade sinyalleri + metadata.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from agents.subagents.base_agent import BaseAgent
from agents.subagents.research_agent import ResearchResult


@dataclass
class EnrichedSignal:
    """Trade sinyali + research context birlesimi."""
    # Original signal fields
    market: dict = field(default_factory=dict)
    condition_id: str = ""
    direction: str = ""          # YES / NO
    bayesian_prob: float = 0.0
    market_price: float = 0.0
    edge: float = 0.0
    entry_price: float = 0.0
    size: float = 0.0
    z_score: float = 0.0
    signal_type: str = ""
    reasoning: str = ""
    token_id: str = ""
    side_diagnostics: Any = None

    # Research enrichment
    whale_direction: str = "NEUTRAL"
    whale_volume_surge: float = 1.0
    smart_money_signal: float = 0.0
    regime_direction: str = "NEUTRAL"
    regime_strength: float = 0.0
    fear_greed: int | None = None
    social_sentiment: float = 0.0
    spot_rsi: float | None = None

    # Order flow enrichment
    orderflow_bias: float = 0.0       # -100 to +100
    orderflow_label: str = "NEUTRAL"  # BULLISH / BEARISH / NEUTRAL
    orderflow_obi: float = 0.0        # order book imbalance
    orderflow_cvd: float = 0.0        # cumulative volume delta
    orderflow_confidence: float = 0.0  # data quality confidence

    # Composite scores
    confluence_score: float = 0.0    # 0.0 - 1.0 (ne kadar cok sinyal ayni yonde)
    risk_flags: list[str] = field(default_factory=list)

    def to_review_summary(self) -> str:
        """Reviewer agent icin okunabilir ozet."""
        flags = ", ".join(self.risk_flags) if self.risk_flags else "none"
        return (
            f"Market: {self.market.get('question', 'N/A')[:80]}\n"
            f"Direction: {self.direction} @ {self.entry_price:.4f}\n"
            f"Edge: {self.edge:.4f} | Bayesian: {self.bayesian_prob:.3f} | Mkt: {self.market_price:.3f}\n"
            f"Size: ${self.size:.2f} | Z-score: {self.z_score:.1f}\n"
            f"Whale: {self.whale_direction} (surge={self.whale_volume_surge:.1f}x)\n"
            f"Smart Money: {self.smart_money_signal:+.3f}\n"
            f"OrderFlow: {self.orderflow_label} (bias={self.orderflow_bias:+.0f}, "
            f"OBI={self.orderflow_obi:+.3f}, CVD={self.orderflow_cvd:+.3f})\n"
            f"Regime: {self.regime_direction} (str={self.regime_strength:.2f})\n"
            f"RSI: {self.spot_rsi or 'N/A'} | Confluence: {self.confluence_score:.2f}\n"
            f"Risk flags: {flags}\n"
            f"Reasoning: {self.reasoning}"
        )


@dataclass
class SignalResult:
    """Signal agent output."""
    signals: list[EnrichedSignal] = field(default_factory=list)
    total_candidates: int = 0
    total_signals: int = 0
    timestamp: float = field(default_factory=time.time)

    @property
    def has_signals(self) -> bool:
        return len(self.signals) > 0


class SignalAgentV2(BaseAgent):
    """Sinyal uretim agent'i — ArbitrageEngine + research context."""

    def __init__(self, arb_engine=None, timeout_seconds: float = 20.0):
        super().__init__(name="SignalAgent", timeout_seconds=timeout_seconds)
        self.arb_engine = arb_engine

    async def run(self, **kwargs) -> SignalResult:
        """
        kwargs:
            candidates: list[dict]  — filtered market candidates
            capital: float          — available capital
            research: ResearchResult | None — research context (for enrichment)
        """
        candidates: list[dict] = kwargs.get("candidates", [])
        capital: float = kwargs.get("capital", 100.0)
        research: ResearchResult | None = kwargs.get("research", None)

        if not self.arb_engine:
            logger.error("[SignalAgent] ArbitrageEngine not set!")
            return SignalResult(total_candidates=len(candidates))

        # Run the 6-model pipeline
        raw_signals = await self.arb_engine.analyze(candidates, capital)
        logger.info(f"[SignalAgent] ArbitrageEngine produced {len(raw_signals)} signals")

        # Enrich with research context
        enriched = []
        for sig in raw_signals:
            market = sig.market
            cid = market.get("condition_id", "")
            symbol = self._detect_symbol(market.get("question", ""))

            es = EnrichedSignal(
                market=market,
                condition_id=cid,
                direction=sig.direction,
                bayesian_prob=sig.bayesian_prob,
                market_price=sig.market_price,
                edge=sig.edge,
                entry_price=sig.entry_price,
                size=sig.size,
                z_score=sig.z_score,
                signal_type=sig.signal_type,
                reasoning=sig.reasoning,
                token_id=sig.token_id,
                side_diagnostics=sig.side_diagnostics,
            )

            # Research enrichment
            if research:
                ctx = research.get_market_context(cid, symbol)
                es.whale_direction = ctx.get("whale_direction", "NEUTRAL")
                es.whale_volume_surge = ctx.get("whale_volume_surge", 1.0)
                es.smart_money_signal = ctx.get("smart_money_signal", 0.0)
                es.regime_direction = ctx.get("regime_direction", "NEUTRAL")
                es.regime_strength = ctx.get("regime_strength", 0.0)
                es.fear_greed = ctx.get("fear_greed")
                es.social_sentiment = ctx.get("social_sentiment", 0.0)
                es.spot_rsi = ctx.get("spot_rsi")
                # Order flow enrichment
                es.orderflow_bias = ctx.get("orderflow_bias", 0.0)
                es.orderflow_label = ctx.get("orderflow_label", "NEUTRAL")
                es.orderflow_obi = ctx.get("orderflow_obi", 0.0)
                es.orderflow_cvd = ctx.get("orderflow_cvd", 0.0)
                es.orderflow_confidence = ctx.get("orderflow_confidence", 0.0)

            # Compute composite scores
            es.confluence_score = self._compute_confluence(es)
            es.risk_flags = self._detect_risk_flags(es)

            enriched.append(es)

        # Sort by confluence score (highest first)
        enriched.sort(key=lambda s: s.confluence_score, reverse=True)

        return SignalResult(
            signals=enriched,
            total_candidates=len(candidates),
            total_signals=len(enriched),
        )

    def _compute_confluence(self, sig: EnrichedSignal) -> float:
        """
        Confluence score: kac tane bagimsiz sinyal ayni yonde?
        0.0 = hicbir seye katilmiyorlar, 1.0 = herkes ayni yonde.
        """
        votes = 0.0
        total_weight = 0.0

        # 1. Edge strength (weight: 3)
        if sig.edge > 0:
            edge_vote = min(sig.edge / 0.20, 1.0)  # 0.20 edge = full vote
            votes += edge_vote * 3
        total_weight += 3

        # 2. Whale alignment (weight: 2)
        if sig.whale_direction != "NEUTRAL":
            whale_aligned = (
                (sig.direction == "YES" and sig.whale_direction == "BULLISH")
                or (sig.direction == "NO" and sig.whale_direction == "BEARISH")
            )
            votes += (1.0 if whale_aligned else -0.5) * 2
        total_weight += 2

        # 3. Smart money (weight: 2)
        if sig.smart_money_signal != 0:
            smart_aligned = (
                (sig.direction == "YES" and sig.smart_money_signal > 0)
                or (sig.direction == "NO" and sig.smart_money_signal < 0)
            )
            votes += (abs(sig.smart_money_signal) if smart_aligned else -0.3) * 2
        total_weight += 2

        # 4. Regime alignment (weight: 1.5)
        if sig.regime_direction != "NEUTRAL":
            regime_aligned = (
                (sig.direction == "YES" and sig.regime_direction == "UP")
                or (sig.direction == "NO" and sig.regime_direction == "DOWN")
            )
            votes += (sig.regime_strength if regime_aligned else -0.3) * 1.5
        total_weight += 1.5

        # 5. Volume surge (weight: 1)
        if sig.whale_volume_surge > 1.5:
            votes += 1.0
        total_weight += 1

        # 6. Order Flow alignment (weight: 2.5) — NEW
        # Order flow is a strong independent signal (low correlation with others)
        if sig.orderflow_bias != 0 and sig.orderflow_confidence > 0.3:
            of_aligned = (
                (sig.direction == "YES" and sig.orderflow_bias > 15)
                or (sig.direction == "NO" and sig.orderflow_bias < -15)
            )
            # Scale by bias strength (0-100 -> 0-1)
            of_strength = min(abs(sig.orderflow_bias) / 60, 1.0)
            if of_aligned:
                votes += of_strength * sig.orderflow_confidence * 2.5
            else:
                # Order flow disagrees — strong negative signal
                votes -= of_strength * sig.orderflow_confidence * 1.5
        total_weight += 2.5

        # Normalize to 0-1
        raw = votes / total_weight if total_weight > 0 else 0.0
        return max(0.0, min(1.0, (raw + 1) / 2))  # shift from [-1,1] to [0,1]

    def _detect_risk_flags(self, sig: EnrichedSignal) -> list[str]:
        """Potansiyel riskleri tespit et."""
        flags = []

        # Regime counter-trade
        if sig.regime_direction == "UP" and sig.direction == "NO":
            flags.append("COUNTER_REGIME_NO")
        elif sig.regime_direction == "DOWN" and sig.direction == "YES":
            flags.append("COUNTER_REGIME_YES")

        # Strong regime (bounce risk)
        if sig.regime_strength > 0.75:
            flags.append("REGIME_OVEREXTENDED")

        # Whale opposition
        if sig.whale_direction == "BULLISH" and sig.direction == "NO":
            flags.append("WHALE_OPPOSITION")
        elif sig.whale_direction == "BEARISH" and sig.direction == "YES":
            flags.append("WHALE_OPPOSITION")

        # Low volume
        if sig.whale_volume_surge < 0.8:
            flags.append("LOW_VOLUME")

        # RSI extremes
        if sig.spot_rsi is not None:
            if sig.spot_rsi > 75 and sig.direction == "YES":
                flags.append("RSI_OVERBOUGHT")
            elif sig.spot_rsi < 25 and sig.direction == "NO":
                flags.append("RSI_OVERSOLD")

        # Small edge
        if sig.edge < 0.08:
            flags.append("THIN_EDGE")

        # Order flow opposition — NEW
        if sig.orderflow_confidence > 0.4:
            if sig.direction == "YES" and sig.orderflow_bias < -30:
                flags.append("ORDERFLOW_OPPOSITION")
            elif sig.direction == "NO" and sig.orderflow_bias > 30:
                flags.append("ORDERFLOW_OPPOSITION")

        return flags

    @staticmethod
    def _detect_symbol(question: str) -> str:
        """Market basligindan Binance sembolunu tespit et."""
        q = question.lower()
        mapping = {
            "bitcoin": "BTCUSDT", "btc": "BTCUSDT",
            "ethereum": "ETHUSDT", "eth": "ETHUSDT",
            "solana": "SOLUSDT", "sol": "SOLUSDT",
            "xrp": "XRPUSDT", "ripple": "XRPUSDT",
            "dogecoin": "DOGEUSDT", "doge": "DOGEUSDT",
            "bnb": "BNBUSDT",
            "hyperliquid": "HYPEUSDT", "hype": "HYPEUSDT",
        }
        for kw, sym in mapping.items():
            if kw in q:
                return sym
        return ""
