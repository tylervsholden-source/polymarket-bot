"""
OrderFlowAgent — Binance order book + trade flow analizi.

polymarket-assistant-tool'dan esinlendi (github.com/FiatFiorino/polymarket-assistant-tool).
11 indikatörü tek bir BULLISH/BEARISH/NEUTRAL bias score'a dönüstürür.

Indikatörler:
1. OBI (Order Book Imbalance) — alici/satici baski farkı
2. CVD (Cumulative Volume Delta) — net alim-satim hacmi
3. Buy/Sell Walls — büyük emirler
4. VWAP Deviation — fiyatin VWAP'tan sapmasi
5. EMA Cross — EMA5/EMA20 kesisimi
6. Trade Velocity — islem hizi degisimi
7. Heikin-Ashi Streak — trend surekliligi

Cikti: OrderFlowData — bias_score (-100 to +100) + sinyal detaylari.
"""
from __future__ import annotations

import asyncio
import time
import math
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from agents.subagents.base_agent import BaseAgent


# ── INDICATOR WEIGHTS (tunable) ──────────────────────────────────────
# NOTE: "velocity" is intentionally NOT a directional vote here.
# calc_trade_velocity() measures raw trade *count* change with no buy/sell
# direction at all (unlike calc_cvd(), which does check is_buy per trade).
# A burst of panic *selling* raises trade count exactly as much as a buying
# frenzy, so folding it into the weighted bias sum with a "positive =
# bullish" sign let a spike in activity mask or even flip a real bearish
# OBI/CVD reading into NEUTRAL right when the market was moving hardest.
# trade_velocity is still computed and reported on OrderFlowData for
# diagnostics; it no longer votes on bias direction/label.
BIAS_WEIGHTS = {
    "ema": 10,
    "obi": 8,
    "cvd": 7,
    "vwap": 5,
    "ha_streak": 6,
    "walls": 4,
}

TOTAL_WEIGHT = sum(BIAS_WEIGHTS.values())


@dataclass
class OrderFlowData:
    """Order flow analiz sonucu."""
    bias_score: float = 0.0          # -100 to +100
    bias_label: str = "NEUTRAL"      # BULLISH / BEARISH / NEUTRAL
    obi: float = 0.0                 # -1 to +1 (order book imbalance)
    cvd: float = 0.0                 # cumulative volume delta
    buy_wall: float = 0.0            # en buyuk alim duvari ($)
    sell_wall: float = 0.0           # en buyuk satim duvari ($)
    vwap_dev: float = 0.0            # VWAP sapma %
    ema_cross: float = 0.0           # EMA5 vs EMA20 farki
    ha_streak: int = 0               # Heikin-Ashi streak (-N bearish, +N bullish)
    trade_velocity: float = 0.0      # islem hizi degisimi
    confidence: float = 0.0          # 0-1 sinyal guvenilirligi
    data_quality: str = "FULL"       # FULL / PARTIAL / NONE

    @property
    def is_bullish(self) -> bool:
        return self.bias_score > 20

    @property
    def is_bearish(self) -> bool:
        return self.bias_score < -20

    @property
    def is_strong(self) -> bool:
        return abs(self.bias_score) > 50

    def agrees_with(self, direction: str) -> bool:
        """Sinyal yonu ile uyumlu mu?"""
        if direction == "YES":
            return self.bias_score > 15
        elif direction == "NO":
            return self.bias_score < -15
        return False


# ── INDICATOR CALCULATIONS ───────────────────────────────────────────

def calc_obi(bids: list[list[float]], asks: list[list[float]], depth: int = 10) -> float:
    """Order Book Imbalance: (bid_vol - ask_vol) / (bid_vol + ask_vol)."""
    bid_vol = sum(float(b[1]) for b in bids[:depth]) if bids else 0
    ask_vol = sum(float(a[1]) for a in asks[:depth]) if asks else 0
    total = bid_vol + ask_vol
    if total == 0:
        return 0.0
    return (bid_vol - ask_vol) / total


def calc_cvd(trades: list[dict]) -> float:
    """Cumulative Volume Delta: net buy volume - sell volume."""
    if not trades:
        return 0.0
    buy_vol = sum(t.get("qty", 0) * t.get("price", 0) for t in trades if t.get("is_buy", False))
    sell_vol = sum(t.get("qty", 0) * t.get("price", 0) for t in trades if not t.get("is_buy", True))
    total = buy_vol + sell_vol
    if total == 0:
        return 0.0
    return (buy_vol - sell_vol) / total  # normalize -1 to +1


def calc_walls(bids: list[list[float]], asks: list[list[float]], depth: int = 20) -> tuple[float, float]:
    """En buyuk bid wall ve ask wall ($)."""
    buy_wall = max((float(b[0]) * float(b[1]) for b in bids[:depth]), default=0)
    sell_wall = max((float(a[0]) * float(a[1]) for a in asks[:depth]), default=0)
    return buy_wall, sell_wall


def calc_vwap_dev(klines: list[dict], current_price: float) -> float:
    """VWAP'tan sapma yüzdesi."""
    if not klines or current_price == 0:
        return 0.0
    total_vol = 0.0
    total_pv = 0.0
    for k in klines:
        # typical price = (high + low + close) / 3
        tp = (k.get("high", 0) + k.get("low", 0) + k.get("close", 0)) / 3
        vol = k.get("volume", 0)
        total_pv += tp * vol
        total_vol += vol
    if total_vol == 0:
        return 0.0
    vwap = total_pv / total_vol
    return ((current_price - vwap) / vwap) * 100


def calc_ema(values: list[float], period: int) -> float:
    """Exponential Moving Average."""
    if not values or len(values) < period:
        return values[-1] if values else 0.0
    multiplier = 2 / (period + 1)
    ema = sum(values[:period]) / period  # SMA for seed
    for val in values[period:]:
        ema = (val - ema) * multiplier + ema
    return ema


def calc_ema_cross(closes: list[float]) -> float:
    """EMA5 - EMA20 farki (normalized). Pozitif = bullish."""
    if len(closes) < 20:
        return 0.0
    ema5 = calc_ema(closes, 5)
    ema20 = calc_ema(closes, 20)
    if ema20 == 0:
        return 0.0
    return ((ema5 - ema20) / ema20) * 100


def calc_ha_streak(klines: list[dict]) -> int:
    """Heikin-Ashi streak. Pozitif = bullish, negatif = bearish."""
    if len(klines) < 2:
        return 0

    ha_candles = []
    for i, k in enumerate(klines):
        o = k.get("open", 0)
        h = k.get("high", 0)
        lo = k.get("low", 0)
        c = k.get("close", 0)

        if i == 0:
            ha_open = (o + c) / 2
        else:
            prev = ha_candles[-1]
            ha_open = (prev["open"] + prev["close"]) / 2

        ha_close = (o + h + lo + c) / 4
        ha_candles.append({"open": ha_open, "close": ha_close})

    # Count streak from end
    streak = 0
    for ha in reversed(ha_candles):
        if ha["close"] > ha["open"]:  # bullish
            if streak >= 0:
                streak += 1
            else:
                break
        elif ha["close"] < ha["open"]:  # bearish
            if streak <= 0:
                streak -= 1
            else:
                break
        else:
            break

    return streak


def calc_trade_velocity(trades: list[dict], window_sec: int = 60) -> float:
    """Son N saniyedeki islem yoğunlugu degisimi."""
    if len(trades) < 4:
        return 0.0
    now = time.time() * 1000  # ms
    recent = [t for t in trades if now - t.get("time", 0) < window_sec * 1000]
    older = [t for t in trades if now - t.get("time", 0) >= window_sec * 1000
             and now - t.get("time", 0) < window_sec * 2000]

    recent_count = len(recent)
    older_count = max(len(older), 1)
    return (recent_count - older_count) / older_count


# ── COMPOSITE BIAS SCORE ─────────────────────────────────────────────

def compute_bias_score(
    obi: float,
    cvd: float,
    buy_wall: float,
    sell_wall: float,
    vwap_dev: float,
    ema_cross: float,
    ha_streak: int,
    velocity: float,
) -> tuple[float, str]:
    """
    11 indikatörü tek bir bias score'a dönüstür.
    Returns: (score: -100 to +100, label: BULLISH/BEARISH/NEUTRAL)
    """
    scores = {}

    # OBI: -1 to +1 → -100 to +100
    scores["obi"] = max(-100, min(100, obi * 100))

    # CVD: -1 to +1 → -100 to +100
    scores["cvd"] = max(-100, min(100, cvd * 100))

    # Walls: buy_wall vs sell_wall ratio
    wall_total = buy_wall + sell_wall
    if wall_total > 0:
        wall_ratio = (buy_wall - sell_wall) / wall_total
        scores["walls"] = max(-100, min(100, wall_ratio * 100))
    else:
        scores["walls"] = 0

    # VWAP dev: positive = above VWAP = bullish
    scores["vwap"] = max(-100, min(100, vwap_dev * 20))  # 5% dev = full signal

    # EMA cross: positive = bullish
    scores["ema"] = max(-100, min(100, ema_cross * 50))  # 2% diff = full signal

    # HA streak: ±5 = full signal
    scores["ha_streak"] = max(-100, min(100, ha_streak * 20))

    # velocity is deliberately excluded from the directional score — see
    # the BIAS_WEIGHTS comment above. It is still accepted as a parameter
    # (and stored on OrderFlowData by the caller) purely for diagnostics.

    # Weighted average
    weighted_sum = sum(scores[k] * BIAS_WEIGHTS[k] for k in scores if k in BIAS_WEIGHTS)
    bias = weighted_sum / TOTAL_WEIGHT

    # Clamp
    bias = max(-100, min(100, bias))

    if bias > 20:
        label = "BULLISH"
    elif bias < -20:
        label = "BEARISH"
    else:
        label = "NEUTRAL"

    return round(bias, 1), label


# ── ORDERFLOW AGENT ──────────────────────────────────────────────────

class OrderFlowAgent(BaseAgent):
    """
    Binance order book + trade flow agent.
    ResearchAgent ile paralel calisir, order flow bias uretir.
    """

    # Binance REST endpoints
    BINANCE_DEPTH_URL = "https://api.binance.com/api/v3/depth"
    BINANCE_TRADES_URL = "https://api.binance.com/api/v3/trades"
    BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"

    def __init__(self, timeout_seconds: float = 15.0):
        super().__init__(name="OrderFlowAgent", timeout_seconds=timeout_seconds)
        self._cache: dict[str, OrderFlowData] = {}
        self._cache_ts: dict[str, float] = {}
        self._cache_ttl = 30  # 30 saniye cache

    async def run(self, **kwargs) -> dict[str, OrderFlowData]:
        """
        kwargs:
            symbols: set[str] — Binance sembolleri (BTCUSDT, ETHUSDT, ...)
        Returns:
            dict[symbol -> OrderFlowData]
        """
        symbols: set[str] = kwargs.get("symbols", set())
        if not symbols:
            return {}

        results = {}
        tasks = []

        for sym in symbols:
            # Cache kontrolü
            cached_ts = self._cache_ts.get(sym, 0)
            if time.time() - cached_ts < self._cache_ttl and sym in self._cache:
                results[sym] = self._cache[sym]
                continue
            tasks.append(self._analyze_symbol(sym))

        if tasks:
            gathered = await asyncio.gather(*tasks, return_exceptions=True)
            for res in gathered:
                if isinstance(res, Exception):
                    logger.warning(f"[OrderFlowAgent] Error: {res}")
                    continue
                if res:
                    sym, data = res
                    results[sym] = data
                    self._cache[sym] = data
                    self._cache_ts[sym] = time.time()

        logger.info(
            f"[OrderFlowAgent] Analyzed {len(results)} symbols: "
            + ", ".join(f"{s}={results[s].bias_label}({results[s].bias_score:+.0f})" for s in results)
        )

        return results

    async def _analyze_symbol(self, symbol: str) -> tuple[str, OrderFlowData] | None:
        """Tek sembol icin tam order flow analizi."""
        try:
            import aiohttp
        except ImportError:
            try:
                import httpx
                return await self._analyze_with_httpx(symbol)
            except ImportError:
                logger.warning("[OrderFlowAgent] aiohttp/httpx not installed, using sync fallback")
                return self._analyze_sync(symbol)

        try:
            async with aiohttp.ClientSession() as session:
                # Paralel: orderbook + trades + klines
                depth_task = self._fetch_depth(session, symbol)
                trades_task = self._fetch_trades(session, symbol)
                klines_task = self._fetch_klines(session, symbol)

                depth, trades, klines = await asyncio.gather(
                    depth_task, trades_task, klines_task,
                    return_exceptions=True,
                )

                return self._compute(symbol, depth, trades, klines)

        except Exception as e:
            logger.warning(f"[OrderFlowAgent] {symbol} failed: {e}")
            return symbol, OrderFlowData(data_quality="NONE")

    async def _analyze_with_httpx(self, symbol: str) -> tuple[str, OrderFlowData] | None:
        """httpx fallback."""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                depth_resp, trades_resp, klines_resp = await asyncio.gather(
                    client.get(self.BINANCE_DEPTH_URL, params={"symbol": symbol, "limit": 20}),
                    client.get(self.BINANCE_TRADES_URL, params={"symbol": symbol, "limit": 200}),
                    client.get(self.BINANCE_KLINES_URL, params={
                        "symbol": symbol, "interval": "1m", "limit": 30,
                    }),
                )

                depth = depth_resp.json() if depth_resp.status_code == 200 else None
                trades_raw = trades_resp.json() if trades_resp.status_code == 200 else None
                klines_raw = klines_resp.json() if klines_resp.status_code == 200 else None

                # Parse trades
                trades = []
                if trades_raw:
                    for t in trades_raw:
                        trades.append({
                            "price": float(t.get("price", 0)),
                            "qty": float(t.get("qty", 0)),
                            "time": t.get("time", 0),
                            "is_buy": not t.get("isBuyerMaker", True),
                        })

                # Parse klines
                klines = []
                if klines_raw:
                    for k in klines_raw:
                        klines.append({
                            "open": float(k[1]),
                            "high": float(k[2]),
                            "low": float(k[3]),
                            "close": float(k[4]),
                            "volume": float(k[5]),
                        })

                return self._compute(symbol, depth, trades, klines)

        except Exception as e:
            logger.warning(f"[OrderFlowAgent] httpx {symbol}: {e}")
            return symbol, OrderFlowData(data_quality="NONE")

    def _analyze_sync(self, symbol: str) -> tuple[str, OrderFlowData] | None:
        """Sync fallback (urllib)."""
        import urllib.request
        import json

        try:
            # Depth
            url = f"{self.BINANCE_DEPTH_URL}?symbol={symbol}&limit=20"
            with urllib.request.urlopen(url, timeout=5) as resp:
                depth = json.loads(resp.read())

            # Trades
            url = f"{self.BINANCE_TRADES_URL}?symbol={symbol}&limit=200"
            with urllib.request.urlopen(url, timeout=5) as resp:
                trades_raw = json.loads(resp.read())

            trades = [{
                "price": float(t["price"]),
                "qty": float(t["qty"]),
                "time": t.get("time", 0),
                "is_buy": not t.get("isBuyerMaker", True),
            } for t in trades_raw]

            # Klines
            url = f"{self.BINANCE_KLINES_URL}?symbol={symbol}&interval=1m&limit=30"
            with urllib.request.urlopen(url, timeout=5) as resp:
                klines_raw = json.loads(resp.read())

            klines = [{
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            } for k in klines_raw]

            return self._compute(symbol, depth, trades, klines)

        except Exception as e:
            logger.warning(f"[OrderFlowAgent] sync {symbol}: {e}")
            return symbol, OrderFlowData(data_quality="NONE")

    def _compute(
        self,
        symbol: str,
        depth: Any,
        trades: Any,
        klines: Any,
    ) -> tuple[str, OrderFlowData]:
        """Tum indikatörleri hesapla ve bias score üret."""
        quality = "FULL"
        bids = depth.get("bids", []) if isinstance(depth, dict) else []
        asks = depth.get("asks", []) if isinstance(depth, dict) else []
        if not bids and not asks:
            quality = "PARTIAL"
        if isinstance(trades, Exception):
            trades = []
            quality = "PARTIAL"
        if isinstance(klines, Exception):
            klines = []
            quality = "PARTIAL"
        if not trades and not klines:
            quality = "NONE"

        # Calculate indicators
        obi = calc_obi(bids, asks, depth=10)
        cvd = calc_cvd(trades) if trades else 0.0
        buy_wall, sell_wall = calc_walls(bids, asks, depth=20)

        # Klines-based indicators
        closes = [k.get("close", 0) for k in klines] if klines else []
        current_price = closes[-1] if closes else 0.0
        vwap_dev = calc_vwap_dev(klines, current_price) if klines else 0.0
        ema_cross = calc_ema_cross(closes) if len(closes) >= 20 else 0.0
        ha_streak = calc_ha_streak(klines) if klines else 0
        velocity = calc_trade_velocity(trades) if trades else 0.0

        # Composite score
        bias_score, bias_label = compute_bias_score(
            obi=obi,
            cvd=cvd,
            buy_wall=buy_wall,
            sell_wall=sell_wall,
            vwap_dev=vwap_dev,
            ema_cross=ema_cross,
            ha_streak=ha_streak,
            velocity=velocity,
        )

        # Confidence: data kalitesine göre
        conf_map = {"FULL": 0.9, "PARTIAL": 0.5, "NONE": 0.1}
        confidence = conf_map.get(quality, 0.5)
        # Güçlü sinyaller daha güvenilir
        if abs(bias_score) > 50:
            confidence = min(1.0, confidence + 0.1)

        return symbol, OrderFlowData(
            bias_score=bias_score,
            bias_label=bias_label,
            obi=round(obi, 4),
            cvd=round(cvd, 4),
            buy_wall=round(buy_wall, 2),
            sell_wall=round(sell_wall, 2),
            vwap_dev=round(vwap_dev, 4),
            ema_cross=round(ema_cross, 4),
            ha_streak=ha_streak,
            trade_velocity=round(velocity, 4),
            confidence=round(confidence, 3),
            data_quality=quality,
        )

    # ── ASYNC FETCH METHODS (aiohttp) ────────────────────────────────

    async def _fetch_depth(self, session, symbol: str) -> dict | None:
        """Binance orderbook depth."""
        try:
            url = f"{self.BINANCE_DEPTH_URL}?symbol={symbol}&limit=20"
            async with session.get(url, timeout=5) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            logger.debug(f"[OrderFlowAgent] depth {symbol}: {e}")
        return None

    async def _fetch_trades(self, session, symbol: str) -> list[dict]:
        """Binance recent trades."""
        try:
            url = f"{self.BINANCE_TRADES_URL}?symbol={symbol}&limit=200"
            async with session.get(url, timeout=5) as resp:
                if resp.status == 200:
                    raw = await resp.json()
                    return [{
                        "price": float(t["price"]),
                        "qty": float(t["qty"]),
                        "time": t.get("time", 0),
                        "is_buy": not t.get("isBuyerMaker", True),
                    } for t in raw]
        except Exception as e:
            logger.debug(f"[OrderFlowAgent] trades {symbol}: {e}")
        return []

    async def _fetch_klines(self, session, symbol: str) -> list[dict]:
        """Binance 1m klines."""
        try:
            url = f"{self.BINANCE_KLINES_URL}?symbol={symbol}&interval=1m&limit=30"
            async with session.get(url, timeout=5) as resp:
                if resp.status == 200:
                    raw = await resp.json()
                    return [{
                        "open": float(k[1]),
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                        "volume": float(k[5]),
                    } for k in raw]
        except Exception as e:
            logger.debug(f"[OrderFlowAgent] klines {symbol}: {e}")
        return []

    def get_cached(self, symbol: str) -> OrderFlowData | None:
        """Cache'ten OrderFlowData al."""
        return self._cache.get(symbol)
