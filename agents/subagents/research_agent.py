"""
ResearchAgent — Piyasa durumu, whale aktivitesi, on-chain, sentiment toplama.

Gorevler:
1. BinanceFeed'den spot, RSI, hacim, OB, regime verisi
2. SmartTraderTracker'dan akilli para sinyali
3. WhaleTracker'dan buyuk pozisyon hareketleri
4. EnhancedSignals'dan options, multi-exchange orderflow, social
5. MarketIndexWatcher'dan global indeks durumu

Cikti: ResearchResult — tum piyasa context'ini tek objede toplar.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from agents.subagents.base_agent import BaseAgent


@dataclass
class WhaleData:
    """Whale tracker sonucu."""
    direction: str = "NEUTRAL"       # BULLISH / BEARISH / NEUTRAL
    large_buys: int = 0
    large_sells: int = 0
    smart_money_buys: int = 0
    smart_money_sells: int = 0
    volume_surge: float = 1.0
    whale_alignment: str = "NEUTRAL"  # BUY / SELL / NEUTRAL


@dataclass
class SmartTraderData:
    """Smart trader consensus."""
    signal: float = 0.0              # -1.0 to +1.0 (negative=bearish)
    confidence: float = 0.0
    aligned_traders: int = 0
    total_traders: int = 0


@dataclass
class RegimeData:
    """4H regime durumu."""
    direction: str = "NEUTRAL"       # UP / DOWN / NEUTRAL
    strength: float = 0.0            # 0.0 - 1.0
    btc_change_4h: float = 0.0
    eth_change_4h: float = 0.0


@dataclass
class EnhancedData:
    """Options, orderflow, social verisi."""
    max_pain: float | None = None
    put_call_ratio: float | None = None
    implied_vol: float | None = None
    multi_exchange_imbalance: float = 0.0  # -1 to +1
    social_sentiment: float = 0.0          # -1 to +1
    fear_greed_index: int | None = None


@dataclass
class ResearchResult:
    """Tum arastirma verilerini toplayan sonuc objesi."""
    timestamp: float = field(default_factory=time.time)

    # Per-market research (condition_id -> data)
    whale_data: dict[str, WhaleData] = field(default_factory=dict)
    smart_trader_data: dict[str, SmartTraderData] = field(default_factory=dict)

    # Global context
    regime: RegimeData = field(default_factory=RegimeData)
    enhanced: dict[str, EnhancedData] = field(default_factory=dict)  # per symbol
    global_indices: dict[str, float] = field(default_factory=dict)

    # Spot data (symbol -> dict of indicators)
    spot_data: dict[str, dict] = field(default_factory=dict)

    # Order flow data (symbol -> OrderFlowData)
    orderflow_data: dict = field(default_factory=dict)  # symbol -> OrderFlowData

    def get_market_context(self, condition_id: str, symbol: str = "") -> dict:
        """Tek market icin tum context'i birlestir."""
        whale = self.whale_data.get(condition_id, WhaleData())
        smart = self.smart_trader_data.get(condition_id, SmartTraderData())
        enhanced = self.enhanced.get(symbol, EnhancedData()) if symbol else EnhancedData()
        spot = self.spot_data.get(symbol, {}) if symbol else {}
        orderflow = self.orderflow_data.get(symbol) if symbol else None

        ctx = {
            "whale_direction": whale.direction,
            "whale_alignment": whale.whale_alignment,
            "whale_volume_surge": whale.volume_surge,
            "smart_money_signal": smart.signal,
            "smart_money_confidence": smart.confidence,
            "regime_direction": self.regime.direction,
            "regime_strength": self.regime.strength,
            "btc_4h_change": self.regime.btc_change_4h,
            "fear_greed": enhanced.fear_greed_index,
            "put_call_ratio": enhanced.put_call_ratio,
            "max_pain": enhanced.max_pain,
            "social_sentiment": enhanced.social_sentiment,
            "multi_exchange_imbalance": enhanced.multi_exchange_imbalance,
            "spot_rsi": spot.get("rsi"),
            "spot_macd": spot.get("macd"),
            "spot_volume_ratio": spot.get("volume_ratio"),
            # Order flow data
            "orderflow_bias": orderflow.bias_score if orderflow else 0.0,
            "orderflow_label": orderflow.bias_label if orderflow else "NEUTRAL",
            "orderflow_obi": orderflow.obi if orderflow else 0.0,
            "orderflow_cvd": orderflow.cvd if orderflow else 0.0,
            "orderflow_confidence": orderflow.confidence if orderflow else 0.0,
        }
        return ctx

    def get_aggregate_sentiment(self) -> dict:
        """Global sentiment ozeti."""
        # Whale consensus
        bullish_whales = sum(1 for w in self.whale_data.values() if w.direction == "BULLISH")
        bearish_whales = sum(1 for w in self.whale_data.values() if w.direction == "BEARISH")
        total_whales = len(self.whale_data)

        # Smart trader consensus
        smart_signals = [s.signal for s in self.smart_trader_data.values() if s.signal != 0]
        avg_smart = sum(smart_signals) / len(smart_signals) if smart_signals else 0.0

        return {
            "whale_bullish_pct": bullish_whales / max(total_whales, 1),
            "whale_bearish_pct": bearish_whales / max(total_whales, 1),
            "smart_money_avg_signal": avg_smart,
            "regime": self.regime.direction,
            "regime_strength": self.regime.strength,
        }


class ResearchAgent(BaseAgent):
    """Piyasa arastirma agent'i — whale + smart trader + regime + enhanced signals."""

    def __init__(
        self,
        binance_feed=None,
        smart_tracker=None,
        whale_tracker_cls=None,
        enhanced_signals=None,
        timeout_seconds: float = 25.0,
    ):
        super().__init__(name="ResearchAgent", timeout_seconds=timeout_seconds)
        self.binance_feed = binance_feed
        self.smart_tracker = smart_tracker
        self._whale_tracker_cls = whale_tracker_cls
        self._whale_tracker = None
        self.enhanced_signals = enhanced_signals

    async def run(self, **kwargs) -> ResearchResult:
        """
        kwargs:
            candidates: list[dict]  — filtered market candidates
            symbols: set[str]       — unique symbols to fetch
        """
        candidates: list[dict] = kwargs.get("candidates", [])
        symbols: set[str] = kwargs.get("symbols", set())

        result = ResearchResult()

        # ── PARALLEL DATA FETCHING ──────────────────────────────────────
        tasks = []

        # 1. Spot data refresh
        if self.binance_feed and symbols:
            tasks.append(("spot", self._fetch_spot(symbols)))

        # 2. Smart trader refresh
        if self.smart_tracker:
            tasks.append(("smart", self._fetch_smart_trader(candidates)))

        # 3. Whale data per market
        if self._whale_tracker_cls and candidates:
            tasks.append(("whale", self._fetch_whale_data(candidates)))

        # 4. Enhanced signals
        if self.enhanced_signals and symbols:
            tasks.append(("enhanced", self._fetch_enhanced(symbols)))

        # Run all in parallel
        if tasks:
            gathered = await asyncio.gather(
                *[t[1] for t in tasks],
                return_exceptions=True,
            )
            for (name, _), res in zip(tasks, gathered):
                if isinstance(res, Exception):
                    logger.warning(f"[ResearchAgent] {name} failed: {res}")
                    continue
                if name == "spot":
                    result.spot_data = res or {}
                elif name == "smart":
                    result.smart_trader_data = res or {}
                elif name == "whale":
                    result.whale_data = res or {}
                elif name == "enhanced":
                    result.enhanced = res or {}

        # ── REGIME DATA (from BinanceFeed cache) ────────────────────────
        result.regime = self._extract_regime()

        # ── GLOBAL INDICES ──────────────────────────────────────────────
        result.global_indices = self._extract_global_indices()

        logger.info(
            f"[ResearchAgent] Done: {len(result.spot_data)} spot, "
            f"{len(result.whale_data)} whale, {len(result.smart_trader_data)} smart, "
            f"regime={result.regime.direction}/{result.regime.strength:.2f}"
        )

        return result

    # ── PRIVATE FETCH METHODS ───────────────────────────────────────────

    async def _fetch_spot(self, symbols: set[str]) -> dict[str, dict]:
        """BinanceFeed'den spot veriyi cek."""
        try:
            await self.binance_feed.refresh(symbols)
            spot = {}
            for sym in symbols:
                data = self.binance_feed.get_signal(sym)
                if data:
                    spot[sym] = data
            return spot
        except Exception as e:
            logger.warning(f"[ResearchAgent] spot fetch: {e}")
            return {}

    async def _fetch_smart_trader(
        self, candidates: list[dict]
    ) -> dict[str, SmartTraderData]:
        """SmartTraderTracker'dan sinyal al."""
        try:
            await self.smart_tracker.refresh()
            result = {}
            for m in candidates:
                cid = m.get("condition_id", "")
                sig = self.smart_tracker.get_signal(cid)
                if sig:
                    result[cid] = SmartTraderData(
                        signal=sig.get("signal", 0.0),
                        confidence=sig.get("confidence", 0.0),
                        aligned_traders=sig.get("aligned", 0),
                        total_traders=sig.get("total", 0),
                    )
            return result
        except Exception as e:
            logger.warning(f"[ResearchAgent] smart trader: {e}")
            return {}

    async def _fetch_whale_data(
        self, candidates: list[dict]
    ) -> dict[str, WhaleData]:
        """WhaleTracker ile buyuk pozisyon hareketlerini analiz et."""
        try:
            # Tek seferlik, uzun omurlu instance (binance_feed/smart_tracker
            # ile ayni pattern) — her cagrida yeniden olusturulursa, WhaleTracker
            # kendi httpx.AsyncClient session'ini hicbir zaman kapatmadigi icin
            # her orchestrator cycle'inda (60-120sn) bir tane daha sizdirir.
            if self._whale_tracker is None:
                self._whale_tracker = self._whale_tracker_cls()
            tracker = self._whale_tracker
            result = {}
            # Batch: max 5 concurrent whale fetches
            sem = asyncio.Semaphore(5)

            async def _fetch_one(m: dict) -> tuple[str, WhaleData | None]:
                cid = m.get("condition_id", "")
                async with sem:
                    try:
                        raw = await tracker.get_activity(cid)
                        return cid, WhaleData(
                            direction=raw.get("direction", "NEUTRAL"),
                            large_buys=raw.get("large_buys", 0),
                            large_sells=raw.get("large_sells", 0),
                            smart_money_buys=raw.get("smart_money_buys", 0),
                            smart_money_sells=raw.get("smart_money_sells", 0),
                            volume_surge=raw.get("volume_surge", 1.0),
                            whale_alignment=raw.get("whale_alignment", "NEUTRAL"),
                        )
                    except Exception:
                        return cid, None

            tasks = [_fetch_one(m) for m in candidates[:10]]  # max 10 markets
            results = await asyncio.gather(*tasks)
            for cid, wd in results:
                if wd:
                    result[cid] = wd
            return result
        except Exception as e:
            logger.warning(f"[ResearchAgent] whale fetch: {e}")
            return {}

    async def _fetch_enhanced(self, symbols: set[str]) -> dict[str, EnhancedData]:
        """EnhancedSignals'dan options, orderflow, social veri."""
        try:
            await self.enhanced_signals.refresh(symbols)
            result = {}
            for sym in symbols:
                opts = self.enhanced_signals.get_options_signal(sym) or {}
                flow = self.enhanced_signals.get_multi_exchange_signal(sym) or {}
                social = self.enhanced_signals.get_social_signal(sym) or {}
                result[sym] = EnhancedData(
                    max_pain=opts.get("max_pain"),
                    put_call_ratio=opts.get("pcr"),
                    implied_vol=opts.get("iv"),
                    multi_exchange_imbalance=flow.get("orderflow", 0.0),
                    social_sentiment=social.get("score", 0.0),
                    fear_greed_index=social.get("fear_greed"),
                )
            return result
        except Exception as e:
            logger.warning(f"[ResearchAgent] enhanced: {e}")
            return {}

    def _extract_regime(self) -> RegimeData:
        """BinanceFeed cache'inden regime durumu cikar."""
        if not self.binance_feed:
            return RegimeData()
        try:
            regime = getattr(self.binance_feed, "_regime", None)
            if regime and isinstance(regime, dict):
                return RegimeData(
                    direction=regime.get("regime", "NEUTRAL"),
                    strength=regime.get("strength", 0.0),
                    btc_change_4h=regime.get("btc_4h", 0.0),
                    eth_change_4h=regime.get("eth_4h", 0.0),
                )
            # Fallback: get from latest spot data
            btc = self.binance_feed.get_signal("BTCUSDT") or {}
            eth = self.binance_feed.get_signal("ETHUSDT") or {}
            btc_4h = btc.get("change_4h", 0.0)
            eth_4h = eth.get("change_4h", 0.0)
            avg_change = (btc_4h + eth_4h) / 2
            if avg_change > 0.5:
                direction = "UP"
            elif avg_change < -0.5:
                direction = "DOWN"
            else:
                direction = "NEUTRAL"
            return RegimeData(
                direction=direction,
                strength=min(abs(avg_change) / 3.0, 1.0),
                btc_change_4h=btc_4h,
                eth_change_4h=eth_4h,
            )
        except Exception:
            return RegimeData()

    def _extract_global_indices(self) -> dict[str, float]:
        """MarketIndexWatcher'dan global indeksleri al."""
        try:
            from agents.market_index_watcher import market_watcher
            return {
                "sp500": market_watcher.get("sp500", 0.0),
                "nasdaq": market_watcher.get("nasdaq", 0.0),
                "btc_dominance": market_watcher.get("btc_dom", 0.0),
            }
        except Exception:
            return {}
