"""
CryptoFeed — Bitstamp REST API'den gerçek zamanlı çoklu zaman dilimi verisi.
(Bitstamp primary, Binance secondary — Binance erişimi proxy/VPN'e bağlı, graceful degradation ile çalışır.)

Her coin için: anlık fiyat, RSI(14), momentum, hacim oranı, order book imbalance.
Zaman dilimleri: 5m, 15m, 1h, 4h

Technical indicators via `ta` library:
  ADX (trend strength), OBV (volume confirmation), CMF (money flow)
"""
from __future__ import annotations

import asyncio
import math
from loguru import logger
from core.candlestick_analyzer import CandlestickAnalyzer

# ta library for advanced indicators
try:
    import pandas as pd
    from ta.trend import ADXIndicator
    from ta.volume import OnBalanceVolumeIndicator, ChaikinMoneyFlowIndicator
    _TA_AVAILABLE = True
except ImportError:
    _TA_AVAILABLE = False

# ccxt for multi-exchange price consensus
try:
    import ccxt.async_support as ccxt_async
    _CCXT_AVAILABLE = True
except ImportError:
    _CCXT_AVAILABLE = False

# GARCH volatility forecasting
try:
    from arch import arch_model
    import numpy as np
    _GARCH_AVAILABLE = True
except ImportError:
    _GARCH_AVAILABLE = False

from agents.enhanced_signals import EnhancedSignals

BITSTAMP_BASE = "https://www.bitstamp.net/api/v2"

# Binance sembol → Bitstamp pair
_SYMBOL_MAP: dict[str, str] = {
    "BTCUSDT":  "btcusd",
    "ETHUSDT":  "ethusd",
    "SOLUSDT":  "solusd",
    "XRPUSDT":  "xrpusd",
    "DOGEUSDT": "dogeusd",
    "BNBUSDT":  "bnbusd",
    "HYPEUSDT": "hypeusd",   # yoksa fetch hata verir, graceful handle edilir
}

# Bitstamp step (saniye cinsinden) → interval adı
_STEPS: dict[str, int] = {
    "5m":  300,
    "15m": 900,
    "1h":  3600,
    "4h":  14400,
}

KLINES_LIMIT = 52   # BB(20), EMA(50), Ichimoku(52) için yeterli


class BinanceFeed:
    """Bitstamp tabanlı çoklu zaman dilimi veri besleyici.
    Dış API arayüzü değişmedi — BinanceFeed adı korunuyor."""

    # ccxt exchange symbols mapping
    _CCXT_SYMBOLS: dict[str, str] = {
        "BTCUSDT": "BTC/USDT", "ETHUSDT": "ETH/USDT", "SOLUSDT": "SOL/USDT",
        "XRPUSDT": "XRP/USDT", "DOGEUSDT": "DOGE/USDT", "BNBUSDT": "BNB/USDT",
    }

    # Binance Futures symbol mapping
    _FUTURES_SYMBOLS: dict[str, str] = {
        "BTCUSDT": "BTCUSDT", "ETHUSDT": "ETHUSDT", "SOLUSDT": "SOLUSDT",
        "XRPUSDT": "XRPUSDT", "DOGEUSDT": "DOGEUSDT", "BNBUSDT": "BNBUSDT",
    }

    # Binance Spot REST base
    _BINANCE_SPOT_BASE = "https://api.binance.com/api/v3"
    _BINANCE_FUTURES_BASE = "https://fapi.binance.com/fapi/v1"

    def __init__(self, session=None):
        import httpx
        self.session = httpx.AsyncClient(timeout=10)
        self._cache: dict[str, dict] = {}   # symbol → {price, ob_imbalance, intervals}
        self._exchanges: list = []           # ccxt exchange instances
        self._exchanges_initialized = False
        # Enhanced signals (multi-exchange, options, whale, social)
        self.enhanced = EnhancedSignals(http_session=self.session)
        # Real-time WebSocket feed (optional, started on first refresh)
        self._ws_feed = None
        self._ws_started = False
        # Fear & Greed Index (cached, refreshed every 5 min)
        self._fng_value: int = 50           # 0=extreme fear, 100=extreme greed
        self._fng_label: str = "Neutral"
        self._fng_last_fetch: float = 0.0
        # ── Cross-Exchange Lead-Lag ──────────────────────────────────────────
        self._binance_prices: dict[str, float] = {}   # symbol → Binance spot price
        # ── Funding Rate + Open Interest ─────────────────────────────────────
        self._funding_data: dict[str, dict] = {}       # symbol → {rate, predicted, oi, oi_change}
        self._funding_last_fetch: float = 0.0
        # ── Liquidation Tracker ──────────────────────────────────────────────
        self._recent_liqs: dict[str, list] = {}        # symbol → [{side, qty_usd, ts}]
        self._liq_last_fetch: float = 0.0
        # ── S&P 500 Correlation ──────────────────────────────────────────────
        self._spx_change_pct: float = 0.0
        self._spx_price: float = 0.0
        self._spx_last_fetch: float = 0.0
        # ── Long/Short Ratio (Binance Futures) ──────────────────────────────
        self._ls_ratio_data: dict[str, dict] = {}   # symbol → {ratio, long%, short%, top_ratio, top_long%, top_short%}
        self._ls_ratio_last_fetch: float = 0.0
        # ── Real-time Price History (lag prevention) ────────────────────────
        self._price_history: dict[str, list] = {}   # symbol → [(timestamp, price), ...]

    # CoinGecko coin ID mapping
    _COINGECKO_IDS: dict[str, str] = {
        "BTCUSDT": "bitcoin", "ETHUSDT": "ethereum", "SOLUSDT": "solana",
        "XRPUSDT": "ripple", "DOGEUSDT": "dogecoin", "BNBUSDT": "binancecoin",
    }

    async def _fetch_fear_greed(self) -> None:
        """Fetch Fear & Greed Index (cached 5 min)."""
        import time as _time
        now = _time.time()
        if now - self._fng_last_fetch < 300:  # 5 min cache
            return
        try:
            resp = await self.session.get(
                "https://api.alternative.me/fng/",
                params={"limit": "1"},
            )
            if resp.status_code == 200:
                data = resp.json().get("data", [{}])[0]
                self._fng_value = int(data.get("value", 50))
                self._fng_label = data.get("value_classification", "Neutral")
                self._fng_last_fetch = now
                logger.info(f"FEAR_GREED: {self._fng_value} ({self._fng_label})")
        except Exception as e:
            logger.debug(f"Fear & Greed fetch failed: {e}")

    def get_fear_greed(self) -> dict:
        """Return current Fear & Greed Index data."""
        return {
            "fng_value": self._fng_value,
            "fng_label": self._fng_label,
            "fng_signal": (self._fng_value - 50) / 50.0,  # -1 (extreme fear) to +1 (extreme greed)
        }

    # ── Cross-Exchange Lead-Lag ────────────────────────────────────────────
    async def _fetch_binance_spot_prices(self, symbols: set[str]) -> None:
        """Fetch Binance spot prices for lead-lag detection vs Bitstamp."""
        for sym in symbols:
            if sym not in self._FUTURES_SYMBOLS:
                continue
            try:
                resp = await self.session.get(
                    f"{self._BINANCE_SPOT_BASE}/ticker/price",
                    params={"symbol": sym},
                )
                if resp.status_code == 200:
                    self._binance_prices[sym] = float(resp.json().get("price", 0))
            except Exception:
                pass

    def get_cross_exchange_signal(self, symbol: str) -> dict:
        """Compare Binance spot vs Bitstamp. Returns lead-lag signal.

        Returns:
            {
                "binance_price": float,
                "bitstamp_price": float,
                "spread_pct": float,        # (binance - bitstamp) / bitstamp * 100
                "signal": str,              # "BULLISH" / "BEARISH" / "NEUTRAL"
                "boost": float,             # -0.03 to +0.03 edge boost
            }
        """
        binance_p = self._binance_prices.get(symbol, 0)
        cached = self._cache.get(symbol, {})
        bitstamp_p = cached.get("bitstamp_price", 0)

        if not binance_p or not bitstamp_p:
            return {"binance_price": 0, "bitstamp_price": 0, "spread_pct": 0,
                    "signal": "NEUTRAL", "boost": 0.0}

        spread_pct = (binance_p - bitstamp_p) / bitstamp_p * 100

        # Threshold: > 0.03% = Binance leading up, < -0.03% = leading down
        # 5dk window'da 0.03% spread = anlamlı lead
        threshold = 0.03
        if spread_pct > threshold:
            signal = "BULLISH"
            boost = min(0.03, spread_pct * 0.15)  # scale: 0.1% spread → 0.015 boost
        elif spread_pct < -threshold:
            signal = "BEARISH"
            boost = max(-0.03, spread_pct * 0.15)
        else:
            signal = "NEUTRAL"
            boost = 0.0

        return {
            "binance_price": round(binance_p, 4),
            "bitstamp_price": round(bitstamp_p, 4),
            "spread_pct": round(spread_pct, 4),
            "signal": signal,
            "boost": round(boost, 4),
        }

    # ── Funding Rate + Open Interest ─────────────────────────────────────────
    async def _fetch_funding_oi(self, symbols: set[str]) -> None:
        """Fetch Binance Futures funding rate + open interest (cached 60s)."""
        import time as _time
        now = _time.time()
        if now - self._funding_last_fetch < 60:
            return
        self._funding_last_fetch = now

        for sym in symbols:
            if sym not in self._FUTURES_SYMBOLS:
                continue
            try:
                # Predicted funding rate + mark price
                premium_resp, oi_resp = await asyncio.gather(
                    self.session.get(
                        f"{self._BINANCE_FUTURES_BASE}/premiumIndex",
                        params={"symbol": sym},
                    ),
                    self.session.get(
                        f"{self._BINANCE_FUTURES_BASE}/openInterest",
                        params={"symbol": sym},
                    ),
                    return_exceptions=True,
                )

                funding_rate = 0.0
                predicted_rate = 0.0
                if not isinstance(premium_resp, Exception) and premium_resp.status_code == 200:
                    pdata = premium_resp.json()
                    funding_rate = float(pdata.get("lastFundingRate", 0))
                    predicted_rate = float(pdata.get("nextFundingRate", 0) or 0)

                oi_value = 0.0
                if not isinstance(oi_resp, Exception) and oi_resp.status_code == 200:
                    oi_value = float(oi_resp.json().get("openInterest", 0))

                # Track OI change
                prev = self._funding_data.get(sym, {})
                prev_oi = prev.get("oi", 0)
                oi_change_pct = ((oi_value - prev_oi) / prev_oi * 100) if prev_oi > 0 else 0.0

                self._funding_data[sym] = {
                    "rate": funding_rate,
                    "predicted": predicted_rate,
                    "oi": oi_value,
                    "oi_change_pct": round(oi_change_pct, 2),
                }
            except Exception as e:
                logger.debug(f"Funding/OI fetch failed for {sym}: {e}")

    def get_funding_signal(self, symbol: str) -> dict:
        """Interpret funding rate + OI into directional signal.

        Returns:
            {
                "funding_rate": float,
                "oi": float,
                "oi_change_pct": float,
                "signal": str,          # "BULLISH" / "BEARISH" / "NEUTRAL"
                "risk": str,            # "LONG_SQUEEZE" / "SHORT_SQUEEZE" / "NORMAL"
                "boost": float,         # -0.03 to +0.03
            }
        """
        data = self._funding_data.get(symbol, {})
        if not data:
            return {"funding_rate": 0, "oi": 0, "oi_change_pct": 0,
                    "signal": "NEUTRAL", "risk": "NORMAL", "boost": 0.0}

        rate = data.get("rate", 0)
        oi = data.get("oi", 0)
        oi_chg = data.get("oi_change_pct", 0)

        # Funding rate thresholds (8h rate)
        # > 0.05% = lots of longs paying shorts → crowded long → pullback risk
        # < -0.05% = lots of shorts paying longs → crowded short → squeeze risk
        signal = "NEUTRAL"
        risk = "NORMAL"
        boost = 0.0

        if rate > 0.0005:  # 0.05%
            if oi_chg > 3:  # OI increasing + high funding = very crowded
                signal = "BEARISH"
                risk = "LONG_SQUEEZE"
                boost = max(-0.03, -abs(rate) * 20)  # 0.1% funding → -0.02 boost
            else:
                signal = "BEARISH"
                risk = "NORMAL"
                boost = max(-0.02, -abs(rate) * 10)
        elif rate < -0.0005:  # -0.05%
            if oi_chg > 3:
                signal = "BULLISH"
                risk = "SHORT_SQUEEZE"
                boost = min(0.03, abs(rate) * 20)
            else:
                signal = "BULLISH"
                risk = "NORMAL"
                boost = min(0.02, abs(rate) * 10)

        return {
            "funding_rate": round(rate * 100, 4),  # as percentage
            "oi": round(oi, 2),
            "oi_change_pct": oi_chg,
            "signal": signal,
            "risk": risk,
            "boost": round(boost, 4),
        }

    # ── Liquidation Tracker ──────────────────────────────────────────────────
    async def _fetch_liquidations(self, symbols: set[str]) -> None:
        """Fetch recent liquidations from Binance Futures (cached 30s)."""
        import time as _time
        now = _time.time()
        if now - self._liq_last_fetch < 30:
            return
        self._liq_last_fetch = now

        for sym in symbols:
            if sym not in self._FUTURES_SYMBOLS:
                continue
            try:
                resp = await self.session.get(
                    f"{self._BINANCE_FUTURES_BASE}/allForceOrders",
                    params={"symbol": sym, "limit": 20},
                )
                if resp.status_code == 200:
                    orders = resp.json()
                    recent = []
                    for o in orders:
                        qty = float(o.get("origQty", 0))
                        price = float(o.get("price", 0))
                        usd_val = qty * price
                        if usd_val > 1000:  # only significant liquidations
                            recent.append({
                                "side": o.get("side", ""),  # SELL = long liq, BUY = short liq
                                "qty_usd": round(usd_val, 0),
                                "ts": o.get("time", 0),
                            })
                    self._recent_liqs[sym] = recent
            except Exception as e:
                logger.debug(f"Liquidation fetch failed for {sym}: {e}")

    def get_liquidation_signal(self, symbol: str) -> dict:
        """Analyze recent liquidations for cascade risk.

        Returns:
            {
                "long_liq_usd": float,   # total long liquidations (bearish)
                "short_liq_usd": float,  # total short liquidations (bullish)
                "net_signal": str,       # "LONG_CASCADE" / "SHORT_CASCADE" / "NEUTRAL"
                "boost": float,          # -0.02 to +0.02
                "count": int,
            }
        """
        liqs = self._recent_liqs.get(symbol, [])
        if not liqs:
            return {"long_liq_usd": 0, "short_liq_usd": 0,
                    "net_signal": "NEUTRAL", "boost": 0.0, "count": 0}

        # SELL side = long liquidation (forced sell = bearish)
        # BUY side = short liquidation (forced buy = bullish)
        import time as _time
        now_ms = _time.time() * 1000
        cutoff = now_ms - 300_000  # last 5 minutes

        long_liq = sum(l["qty_usd"] for l in liqs if l["side"] == "SELL" and l["ts"] > cutoff)
        short_liq = sum(l["qty_usd"] for l in liqs if l["side"] == "BUY" and l["ts"] > cutoff)
        count = sum(1 for l in liqs if l["ts"] > cutoff)

        net = short_liq - long_liq  # positive = more short squeezes = bullish

        # Thresholds: $100K+ = meaningful, $500K+ = strong signal
        signal = "NEUTRAL"
        boost = 0.0
        if long_liq > 100_000 and long_liq > short_liq * 2:
            signal = "LONG_CASCADE"
            boost = max(-0.02, -long_liq / 10_000_000)  # $1M liq → -0.01
        elif short_liq > 100_000 and short_liq > long_liq * 2:
            signal = "SHORT_CASCADE"
            boost = min(0.02, short_liq / 10_000_000)
        elif abs(net) > 50_000:
            boost = max(-0.01, min(0.01, net / 5_000_000))

        return {
            "long_liq_usd": round(long_liq, 0),
            "short_liq_usd": round(short_liq, 0),
            "net_signal": signal,
            "boost": round(boost, 4),
            "count": count,
        }

    # ── Long/Short Ratio (Binance Futures — free, no API key) ────────────────
    async def _fetch_long_short_ratio(self, symbols: set[str]) -> None:
        """Fetch global + top trader long/short ratio (cached 60s).

        Endpoints (no auth required):
        - /futures/data/globalLongShortAccountRatio  — all traders
        - /futures/data/topLongShortPositionRatio    — top 20% by position
        """
        import time as _time
        now = _time.time()
        if now - self._ls_ratio_last_fetch < 60:
            return
        self._ls_ratio_last_fetch = now

        _DATA_BASE = "https://fapi.binance.com/futures/data"

        for sym in symbols:
            if sym not in self._FUTURES_SYMBOLS:
                continue
            try:
                global_resp, top_resp = await asyncio.gather(
                    self.session.get(
                        f"{_DATA_BASE}/globalLongShortAccountRatio",
                        params={"symbol": sym, "period": "5m", "limit": 1},
                    ),
                    self.session.get(
                        f"{_DATA_BASE}/topLongShortPositionRatio",
                        params={"symbol": sym, "period": "5m", "limit": 1},
                    ),
                    return_exceptions=True,
                )

                g_ratio = 1.0
                g_long = 0.5
                g_short = 0.5
                if not isinstance(global_resp, Exception) and global_resp.status_code == 200:
                    gdata = global_resp.json()
                    if gdata:
                        g_ratio = float(gdata[0].get("longShortRatio", 1.0))
                        g_long = float(gdata[0].get("longAccount", 0.5))
                        g_short = float(gdata[0].get("shortAccount", 0.5))

                t_ratio = 1.0
                t_long = 0.5
                t_short = 0.5
                if not isinstance(top_resp, Exception) and top_resp.status_code == 200:
                    tdata = top_resp.json()
                    if tdata:
                        t_ratio = float(tdata[0].get("longShortRatio", 1.0))
                        t_long = float(tdata[0].get("longAccount", 0.5))
                        t_short = float(tdata[0].get("shortAccount", 0.5))

                self._ls_ratio_data[sym] = {
                    "global_ratio": g_ratio,
                    "global_long_pct": g_long,
                    "global_short_pct": g_short,
                    "top_ratio": t_ratio,
                    "top_long_pct": t_long,
                    "top_short_pct": t_short,
                }
            except Exception as e:
                logger.debug(f"L/S ratio fetch failed for {sym}: {e}")

    def get_long_short_signal(self, symbol: str) -> dict:
        """Long/Short ratio → contrarian + smart money signal.

        Logic:
        - Global ratio: contrarian — crowd too long → bearish, too short → bullish
        - Top trader ratio: follow — smart money long → bullish, short → bearish
        - Combined: if both agree → strong signal, if conflict → weak/neutral

        Returns:
            {
                "global_ratio": float, "top_ratio": float,
                "crowd_signal": str, "smart_signal": str,
                "boost": float (-0.02 to +0.02),
            }
        """
        data = self._ls_ratio_data.get(symbol, {})
        if not data:
            return {"global_ratio": 1.0, "top_ratio": 1.0,
                    "crowd_signal": "NEUTRAL", "smart_signal": "NEUTRAL",
                    "boost": 0.0}

        g_ratio = data["global_ratio"]
        t_ratio = data["top_ratio"]

        # Crowd signal (contrarian): ratio > 1.2 = crowded long → bearish
        crowd_signal = "NEUTRAL"
        crowd_boost = 0.0
        if g_ratio > 1.2:       # 55%+ long → contrarian bearish
            crowd_signal = "BEARISH"
            crowd_boost = -min(0.015, (g_ratio - 1.0) * 0.01)
        elif g_ratio < 0.83:    # 55%+ short → contrarian bullish
            crowd_signal = "BULLISH"
            crowd_boost = min(0.015, (1.0 - g_ratio) * 0.01)

        # Smart money signal (follow): top traders long → bullish
        smart_signal = "NEUTRAL"
        smart_boost = 0.0
        if t_ratio > 1.15:      # top traders 53%+ long → follow bullish
            smart_signal = "BULLISH"
            smart_boost = min(0.01, (t_ratio - 1.0) * 0.01)
        elif t_ratio < 0.87:    # top traders 53%+ short → follow bearish
            smart_signal = "BEARISH"
            smart_boost = -min(0.01, (1.0 - t_ratio) * 0.01)

        # Combined: crowd contrarian + smart money follow
        boost = round(crowd_boost + smart_boost, 4)
        boost = max(-0.02, min(0.02, boost))  # cap ±0.02

        return {
            "global_ratio": round(g_ratio, 3),
            "top_ratio": round(t_ratio, 3),
            "crowd_signal": crowd_signal,
            "smart_signal": smart_signal,
            "boost": boost,
        }

    # ── S&P 500 Correlation ──────────────────────────────────────────────────
    async def _fetch_spx(self) -> None:
        """Fetch S&P 500 intraday change (cached 2 min). Uses Yahoo Finance."""
        import time as _time
        now = _time.time()
        if now - self._spx_last_fetch < 120:
            return
        self._spx_last_fetch = now

        try:
            # Yahoo Finance chart API — 1d range, 5m interval
            resp = await self.session.get(
                "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC",
                params={"range": "1d", "interval": "5m"},
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if resp.status_code == 200:
                result = resp.json().get("chart", {}).get("result", [{}])[0]
                meta = result.get("meta", {})
                prev_close = meta.get("chartPreviousClose", 0)
                current = meta.get("regularMarketPrice", 0)
                if prev_close > 0 and current > 0:
                    self._spx_price = current
                    self._spx_change_pct = (current - prev_close) / prev_close * 100
                    logger.debug(
                        f"SPX: ${current:.0f} ({self._spx_change_pct:+.2f}%)"
                    )
        except Exception as e:
            logger.debug(f"SPX fetch failed: {e}")

    def get_spx_signal(self) -> dict:
        """S&P 500 correlation signal for crypto direction.

        During US market hours, crypto correlates with SPX.
        Sharp SPX moves predict crypto direction 1-3 minutes ahead.

        Returns:
            {
                "spx_price": float,
                "spx_change_pct": float,
                "signal": str,           # "BULLISH" / "BEARISH" / "NEUTRAL"
                "boost": float,          # -0.02 to +0.02
                "active": bool,          # True during US market hours
            }
        """
        from datetime import datetime, timezone, timedelta

        # Check if US market is open (9:30 AM - 4:00 PM ET)
        et_offset = timedelta(hours=-4)  # EDT
        now_et = datetime.now(timezone.utc) + et_offset
        market_open = now_et.hour > 9 or (now_et.hour == 9 and now_et.minute >= 30)
        market_close = now_et.hour < 16
        is_weekday = now_et.weekday() < 5
        active = market_open and market_close and is_weekday

        if not active or self._spx_price == 0:
            return {"spx_price": 0, "spx_change_pct": 0,
                    "signal": "NEUTRAL", "boost": 0.0, "active": False}

        chg = self._spx_change_pct
        # SPX thresholds: > 0.3% = strong move, > 0.1% = mild
        signal = "NEUTRAL"
        boost = 0.0
        if chg > 0.3:
            signal = "BULLISH"
            boost = min(0.02, chg * 0.02)  # 1% SPX → 0.02 boost
        elif chg > 0.1:
            signal = "BULLISH"
            boost = min(0.01, chg * 0.01)
        elif chg < -0.3:
            signal = "BEARISH"
            boost = max(-0.02, chg * 0.02)
        elif chg < -0.1:
            signal = "BEARISH"
            boost = max(-0.01, chg * 0.01)

        return {
            "spx_price": round(self._spx_price, 2),
            "spx_change_pct": round(chg, 3),
            "signal": signal,
            "boost": round(boost, 4),
            "active": active,
        }

    async def _init_exchanges(self) -> None:
        """Mark exchanges as initialized (uses REST APIs, no ccxt needed)."""
        if self._exchanges_initialized:
            return
        self._exchanges_initialized = True
        logger.info("Multi-exchange: CoinGecko + Bitstamp + Coinpaprika + Binance Futures consensus active")

    async def _fetch_multi_exchange_price(self, symbol: str) -> dict:
        """Fetch price from multiple public APIs. Returns consensus data."""
        coin_id = self._COINGECKO_IDS.get(symbol)
        if not coin_id:
            return {}

        prices = []
        exchange_prices = {}

        # Source 1: CoinGecko (aggregated from 700+ exchanges)
        try:
            resp = await self.session.get(
                f"https://api.coingecko.com/api/v3/simple/price",
                params={"ids": coin_id, "vs_currencies": "usd"},
            )
            if resp.status_code == 200:
                p = resp.json().get(coin_id, {}).get("usd")
                if p:
                    prices.append(float(p))
                    exchange_prices["coingecko"] = float(p)
        except Exception:
            pass

        # Source 2: Coinpaprika (independent aggregator)
        _paprika_map = {
            "bitcoin": "btc-bitcoin", "ethereum": "eth-ethereum", "solana": "sol-solana",
            "ripple": "xrp-xrp", "dogecoin": "doge-dogecoin", "binancecoin": "bnb-binance-coin",
        }
        paprika_id = _paprika_map.get(coin_id)
        if paprika_id:
            try:
                resp = await self.session.get(
                    f"https://api.coinpaprika.com/v1/tickers/{paprika_id}",
                )
                if resp.status_code == 200:
                    p = resp.json().get("quotes", {}).get("USD", {}).get("price")
                    if p:
                        prices.append(float(p))
                        exchange_prices["coinpaprika"] = float(p)
            except Exception:
                pass

        if not prices:
            return {}

        prices.sort()
        median = prices[len(prices) // 2]
        spread = (max(prices) - min(prices)) / median * 100 if median > 0 else 0.0

        return {
            "consensus_price": median,
            "exchange_prices": exchange_prices,
            "exchange_spread_pct": round(spread, 4),
            "exchange_count": len(prices),
        }

    async def refresh(self, symbols: set[str]) -> None:
        """Verilen semboller için tüm verileri paralel çek."""
        if not symbols:
            return
        # Start WS feed on first refresh (background thread)
        if not self._ws_started:
            self._ws_started = True
            try:
                from agents.ws_feed import RealtimeFeed
                self._ws_feed = RealtimeFeed()
                self._ws_feed.start(symbols)
            except Exception as e:
                logger.debug(f"WS feed start failed: {e}")
                self._ws_feed = None
        await self._init_exchanges()
        # Fetch all data sources in parallel
        extra_tasks = [
            self._fetch_fear_greed(),
            self._fetch_binance_spot_prices(symbols),
            self._fetch_funding_oi(symbols),
            self._fetch_liquidations(symbols),
            self._fetch_long_short_ratio(symbols),
            self._fetch_spx(),
            self.enhanced.refresh(symbols),  # multi-exchange, options, whale, social
        ]
        symbol_tasks = [self._fetch_symbol(sym) for sym in symbols]
        await asyncio.gather(*symbol_tasks, *extra_tasks, return_exceptions=True)

    async def _fetch_symbol(self, symbol: str) -> None:
        pair = _SYMBOL_MAP.get(symbol)
        if not pair:
            logger.debug(f"CryptoFeed: bilinmeyen sembol {symbol}")
            return
        try:
            kline_tasks = {iv: self._fetch_klines(pair, iv) for iv in _STEPS}
            kline_results = await asyncio.gather(*kline_tasks.values(), return_exceptions=True)

            price_task, ob_task, multi_ex = await asyncio.gather(
                self._fetch_price(pair),
                self._fetch_ob(pair),
                self._fetch_multi_exchange_price(symbol),
                return_exceptions=True,
            )
            if isinstance(multi_ex, Exception):
                multi_ex = {}

            intervals: dict[str, dict] = {}
            raw_klines: dict[str, list] = {}
            for iv, result in zip(kline_tasks.keys(), kline_results):
                if isinstance(result, list) and len(result) >= 3:
                    intervals[iv] = self._process_klines(result)
                    raw_klines[iv] = result  # Store for multi-TF candle analysis

            bitstamp_price = price_task if isinstance(price_task, float) else 0.0
            # Use consensus price if available, fallback to Bitstamp
            consensus = multi_ex.get("consensus_price", 0.0) if isinstance(multi_ex, dict) else 0.0
            # Priority: WS real-time (< 5s) > consensus > Bitstamp REST
            ws_price = 0.0
            if self._ws_feed:
                _wp = self._ws_feed.get_price(symbol)
                if _wp and self._ws_feed.get_age_seconds(symbol) < 5.0:
                    ws_price = _wp
            final_price = ws_price if ws_price > 0 else (consensus if consensus > 0 else bitstamp_price)

            # ── Record price history for lag prevention ─────────────────
            if final_price > 0:
                import time as _t
                now_ts = _t.time()
                if symbol not in self._price_history:
                    self._price_history[symbol] = []
                self._price_history[symbol].append((now_ts, final_price))
                # Keep only last 5 minutes of history
                cutoff = now_ts - 300
                self._price_history[symbol] = [
                    (ts, p) for ts, p in self._price_history[symbol] if ts > cutoff
                ]

            self._cache[symbol] = {
                "price": final_price,
                "bitstamp_price": bitstamp_price,
                "ob_imbalance": ob_task if isinstance(ob_task, float) else 0.0,
                "intervals": intervals,
                "raw_klines": raw_klines,
                "multi_exchange": multi_ex if isinstance(multi_ex, dict) else {},
            }

            if intervals:
                sig_1h = intervals.get("1h", {})
                ob_val = ob_task if isinstance(ob_task, float) else 0.0
                mx = self._cache[symbol].get("multi_exchange", {})
                ex_info = f"ex={mx.get('exchange_count', 0)} spread={mx.get('exchange_spread_pct', 0):.3f}%" if mx else "ex=bitstamp_only"
                logger.debug(
                    f"CryptoFeed {symbol}: ${final_price:.4f} | "
                    f"1h chg={sig_1h.get('change_pct', 0):+.2f}% "
                    f"RSI={sig_1h.get('rsi', 50):.0f} "
                    f"vol={sig_1h.get('volume_ratio', 1):.1f}x "
                    f"OB={ob_val:+.2f} | {ex_info}"
                )
        except Exception as e:
            logger.debug(f"CryptoFeed._fetch_symbol {symbol}: {e}")

    # Bitstamp interval (seconds) → Binance kline interval string
    _BINANCE_INTERVAL_MAP: dict[str, str] = {
        "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h",
    }

    async def _fetch_klines(self, pair: str, interval: str) -> list | None:
        # Try Bitstamp first
        result = await self._fetch_klines_bitstamp(pair, interval)
        if result:
            return result
        # Fallback: Binance Spot (for coins not on Bitstamp, e.g. HYPE)
        return await self._fetch_klines_binance(pair, interval)

    async def _fetch_klines_bitstamp(self, pair: str, interval: str) -> list | None:
        try:
            step = _STEPS[interval]
            resp = await self.session.get(
                f"{BITSTAMP_BASE}/ohlc/{pair}/",
                params={"step": step, "limit": KLINES_LIMIT},
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {}).get("ohlc", [])
                # Bitstamp format: [{timestamp, open, high, low, close, volume}]
                # Convert to list-of-lists matching Binance format [_, open, _, _, close, volume]
                return [
                    [c["timestamp"], c["open"], c["high"], c["low"], c["close"], c["volume"]]
                    for c in data
                ]
        except Exception as e:
            logger.debug(f"CryptoFeed._fetch_klines_bitstamp {pair}/{interval}: {e}")
        return None

    async def _fetch_klines_binance(self, pair: str, interval: str) -> list | None:
        """Fallback: Binance Spot klines for coins not on Bitstamp (e.g. HYPE)."""
        try:
            # pair is bitstamp format (e.g. "hypeusd") → convert to Binance (e.g. "HYPEUSDT")
            _reverse_map = {v: k for k, v in _SYMBOL_MAP.items()}
            binance_sym = _reverse_map.get(pair)
            if not binance_sym:
                return None
            bn_interval = self._BINANCE_INTERVAL_MAP.get(interval)
            if not bn_interval:
                return None
            resp = await self.session.get(
                f"{self._BINANCE_SPOT_BASE}/klines",
                params={"symbol": binance_sym, "interval": bn_interval, "limit": KLINES_LIMIT},
            )
            if resp.status_code == 200:
                data = resp.json()
                # Binance format: [[open_time, open, high, low, close, volume, ...]]
                return [
                    [str(c[0]), c[1], c[2], c[3], c[4], c[5]]
                    for c in data
                ]
        except Exception as e:
            logger.debug(f"CryptoFeed._fetch_klines_binance {pair}/{interval}: {e}")
        return None

    async def _fetch_ob(self, pair: str) -> float:
        try:
            resp = await self.session.get(
                f"{BITSTAMP_BASE}/order_book/{pair}/",
                params={"group": 1},
            )
            if resp.status_code == 200:
                d = resp.json()
                bid_vol = sum(float(b[1]) for b in d.get("bids", [])[:10])
                ask_vol = sum(float(a[1]) for a in d.get("asks", [])[:10])
                total = bid_vol + ask_vol
                return (bid_vol - ask_vol) / total if total > 0 else 0.0
        except Exception:
            pass
        return 0.0

    async def _fetch_price(self, pair: str) -> float:
        try:
            resp = await self.session.get(f"{BITSTAMP_BASE}/ticker/{pair}/")
            if resp.status_code == 200:
                return float(resp.json().get("last", 0))
        except Exception:
            pass
        return 0.0

    def _process_klines(self, klines: list) -> dict:
        try:
            closes  = [float(k[4]) for k in klines]
            opens   = [float(k[1]) for k in klines]
            highs   = [float(k[2]) for k in klines]
            lows    = [float(k[3]) for k in klines]
            volumes = [float(k[5]) for k in klines]
        except (IndexError, ValueError, TypeError) as e:
            logger.debug(f"CryptoFeed._process_klines parse error: {e}")
            return {}

        # Formasyon Analizi
        patterns = CandlestickAnalyzer.analyze(klines)

        # Mevcut mumun % değişimi (open → close)
        cur_open  = opens[-1]
        cur_close = closes[-1]
        change_pct = ((cur_close - cur_open) / cur_open * 100) if cur_open > 0 else 0.0

        # Son N mumun toplam yönsel değişim (trend)
        n = min(4, len(closes) - 1)
        trend_pct = ((closes[-1] - closes[-n-1]) / closes[-n-1] * 100) if closes[-n-1] > 0 else 0.0

        # ── MACRO TREND: Son 15 mumun toplam yönsel değişim ──────────────
        # Dead cat bounce koruması: 5 yeşil mum olsa bile 15 mum düşüşteyse
        # büyük trend hala bearish → YES girişi bloklanmalı.
        macro_n = min(15, len(closes) - 1)
        macro_trend_pct = ((closes[-1] - closes[-macro_n-1]) / closes[-macro_n-1] * 100) if macro_n > 0 and closes[-macro_n-1] > 0 else 0.0

        # Hacim oranı: güncel mum / son N mum ortalaması
        avg_vol = sum(volumes[:-1]) / max(len(volumes) - 1, 1)
        vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1.0
        vol_ratio = min(vol_ratio, 30.0)  # Clamp: extreme spikes don't blow up multiplier

        # RSI(14)
        rsi = self._calc_rsi(closes, 14)

        # Momentum: son 3 mumun yönü (+1/-1 her mum)
        momentum = sum(
            1 if closes[i] > closes[i - 1] else -1
            for i in range(max(1, len(closes) - 3), len(closes))
        )

        # MACD(3,15,3) — kısa periyot, 5dk marketlere uygun
        macd_hist_raw = self._calc_macd(closes, fast=3, slow=15, signal=3)
        mid_price = closes[-1] if closes[-1] > 0 else 1.0
        macd_hist_pct = (macd_hist_raw / mid_price) * 100

        # ── Yeni teknik indikatörler ──────────────────────────────────────
        bb = self._calc_bollinger(closes)
        ema_cross = self._calc_ema_cross(closes)
        atr_pct = self._calc_atr(highs, lows, closes)
        stoch = self._calc_stoch_rsi(closes)
        sr = self._calc_support_resistance(highs, lows, closes)
        vwap = self._calc_vwap(highs, lows, closes, volumes)
        ichi = self._calc_ichimoku(highs, lows, closes)
        fib = self._calc_fibonacci(highs, lows, closes)

        # ── OPT-3: Momentum deceleration (son 3 mum change karşılaştır) ──
        # abs(change[-1]) < abs(change[-2]) < abs(change[-3]) → momentum azalıyor
        candle_changes = []
        for i in range(max(1, len(closes) - 3), len(closes)):
            prev_c = closes[i - 1]
            if prev_c > 0:
                candle_changes.append((closes[i] - prev_c) / prev_c * 100)
            else:
                candle_changes.append(0.0)
        momentum_decelerating = False
        if len(candle_changes) >= 3:
            a0, a1, a2 = abs(candle_changes[-3]), abs(candle_changes[-2]), abs(candle_changes[-1])
            momentum_decelerating = (a2 < a1 < a0) and a0 > 0.05  # at least 0.05% initial move

        # ── CONSECUTIVE CANDLE COUNTER (bounce detection) ──────────────
        # Son 4 saat verisi: 8 ardışık bearish mum → %100 bounce.
        # Gerçek data kanıtı: BTC 4x, ETH 5x, SOL 4x bounce son 4 saatte.
        # Bot bunu göremiyordu çünkü sadece son muma bakıyordu.
        consecutive_bearish = 0
        consecutive_bullish = 0
        # Sondan geriye say — streak kırılınca dur
        for i in range(len(closes) - 1, 0, -1):
            if closes[i] < opens[i]:
                if consecutive_bullish > 0:
                    break  # streak kırıldı
                consecutive_bearish += 1
            elif closes[i] > opens[i]:
                if consecutive_bearish > 0:
                    break  # streak kırıldı
                consecutive_bullish += 1
            else:
                break  # doji = streak kırılması

        # Bounce signal: ilk yeşil mum, öncesinde 4+ bearish streak
        # Streak 0 ama önceki mumlar bearish ise → bounce başladı
        bounce_signal = False
        pre_bounce_streak = 0
        if closes[-1] >= opens[-1] and len(closes) >= 5:
            # Son mum yeşil — önceki ardışık bearish mumları say
            for i in range(len(closes) - 2, 0, -1):
                if closes[i] < opens[i]:
                    pre_bounce_streak += 1
                else:
                    break
            if pre_bounce_streak >= 4:
                bounce_signal = True

        # ── BULLISH EXHAUSTION DETECTION ──────────────────────────────
        # 2-3+ ardışık yeşil mum + yüklü kümülatif hareket = tükenme.
        # Piyasa "çok yükseldi" → mean reversion (pullback) olasılığı yüksek.
        # Bu NO trade için ideal giriş noktası.
        bullish_exhaustion = False
        bullish_exhaustion_magnitude = 0.0
        if consecutive_bullish >= 2:
            # Yeşil mumların gövde büyüklüklerini topla (% cinsinden)
            for i in range(len(closes) - 1,
                           max(0, len(closes) - 1 - consecutive_bullish), -1):
                if i > 0 and closes[i] > opens[i]:
                    bullish_exhaustion_magnitude += (
                        (closes[i] - opens[i]) / opens[i] * 100
                    )
            # 0.30%+ kümülatif hareket = anlamlı tükenme (5dk crypto'da)
            if bullish_exhaustion_magnitude >= 0.30:
                bullish_exhaustion = True

        result = {
            "change_pct": round(change_pct, 4),
            "trend_pct": round(trend_pct, 4),
            "macro_trend_pct": round(macro_trend_pct, 4),
            "volume_ratio": round(vol_ratio, 3),
            "rsi": round(rsi, 1),
            "momentum": momentum,
            "macd_hist": round(macd_hist_pct, 6),
            "patterns": patterns,
            "momentum_decelerating": momentum_decelerating,
            # Bollinger Bands
            "bb_width": bb["bb_width"],
            "bb_pos": bb["bb_pos"],
            "bb_squeeze": bb.get("bb_squeeze", False),
            "bb_breakout": bb.get("bb_breakout", 0.0),
            "bb_bandwidth_pctile": bb.get("bb_bandwidth_pctile", 50.0),
            # EMA crossover
            "ema_cross": ema_cross["ema_cross"],
            # ATR (normalized %)
            "atr_pct": round(atr_pct, 4),
            # Stochastic RSI
            "stoch_k": stoch["stoch_k"],
            "stoch_d": stoch["stoch_d"],
            # Support / Resistance
            "sr_position": sr["sr_position"],
            # VWAP
            "vwap_dev": vwap["vwap_dev"],
            # Ichimoku
            "ichi_signal": ichi["ichi_signal"],
            "ichi_tk_cross": ichi.get("ichi_tk_cross", 0.0),
            # Fibonacci
            "fib_level": fib["fib_level"],
            # Bounce detection
            "consecutive_bearish": consecutive_bearish,
            "consecutive_bullish": consecutive_bullish,
            "bounce_signal": bounce_signal,
            "pre_bounce_streak": pre_bounce_streak,
            # Bullish exhaustion (2-3+ green candles, significant cum. move)
            "bullish_exhaustion": bullish_exhaustion,
            "bullish_exhaustion_magnitude": round(bullish_exhaustion_magnitude, 4),
            # ── NEW: ta library indicators ──
            **self._calc_ta_indicators(highs, lows, closes, volumes),
            # ── GARCH volatility forecast ──
            **self._calc_garch(closes),
        }

        # ── COMPOSITE TECHNICAL SCORE ──────────────────────────────────────
        # Aggregates ALL indicators into single -1.0 to +1.0 score.
        # Positive = bullish, negative = bearish. Magnitude = confidence.
        result["tech_score"] = self._calc_composite_score(result)

        return result

    def _calc_garch(self, closes: list[float]) -> dict:
        """GARCH(1,1) volatility forecast. Predicts if vol is expanding or contracting."""
        out = {"garch_forecast": 0.0, "vol_expanding": False}
        if not _GARCH_AVAILABLE or len(closes) < 30:
            return out
        try:
            # Log returns in basis points
            returns = np.diff(np.log(np.array(closes, dtype=float))) * 10000
            if len(returns) < 20 or np.std(returns) < 0.01:
                return out
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model = arch_model(returns, vol="Garch", p=1, q=1, rescale=False)
                res = model.fit(disp="off", show_warning=False)
                # Forecast 1 step ahead
                forecast = res.forecast(horizon=1)
                fwd_var = forecast.variance.values[-1, 0]
                current_var = res.conditional_volatility.iloc[-1] ** 2
                out["garch_forecast"] = round(float(fwd_var ** 0.5), 4)
                out["vol_expanding"] = bool(fwd_var > current_var * 1.1)  # 10%+ increase
        except Exception:
            pass
        return out

    def _calc_ta_indicators(self, highs: list[float], lows: list[float],
                            closes: list[float], volumes: list[float]) -> dict:
        """ADX, OBV, CMF via ta library. Falls back to manual calc if ta unavailable."""
        out = {"adx": 25.0, "adx_plus": 0.0, "adx_minus": 0.0,
               "obv_slope": 0.0, "cmf": 0.0}
        if len(closes) < 15:
            return out

        if _TA_AVAILABLE:
            try:
                df = pd.DataFrame({"high": highs, "low": lows, "close": closes, "volume": volumes})
                # ADX(14): trend strength (>25 = trending, <20 = ranging)
                adx_ind = ADXIndicator(df["high"], df["low"], df["close"], window=14)
                adx_val = adx_ind.adx().iloc[-1]
                adx_plus = adx_ind.adx_pos().iloc[-1]
                adx_minus = adx_ind.adx_neg().iloc[-1]
                out["adx"] = round(adx_val if not math.isnan(adx_val) else 25.0, 1)
                out["adx_plus"] = round(adx_plus if not math.isnan(adx_plus) else 0.0, 1)
                out["adx_minus"] = round(adx_minus if not math.isnan(adx_minus) else 0.0, 1)

                # OBV slope: rising OBV = volume confirms price direction
                obv = OnBalanceVolumeIndicator(df["close"], df["volume"]).on_balance_volume()
                obv_vals = obv.dropna().values
                if len(obv_vals) >= 5:
                    slope = (obv_vals[-1] - obv_vals[-5]) / (abs(obv_vals[-5]) + 1e-9)
                    out["obv_slope"] = round(max(-1.0, min(1.0, slope)), 4)

                # CMF(20): Chaikin Money Flow — positive = buying pressure
                cmf = ChaikinMoneyFlowIndicator(df["high"], df["low"], df["close"], df["volume"], window=min(20, len(closes) - 1))
                cmf_val = cmf.chaikin_money_flow().iloc[-1]
                out["cmf"] = round(cmf_val if not math.isnan(cmf_val) else 0.0, 4)
            except Exception as e:
                logger.debug(f"ta indicators error: {e}")
        else:
            # Manual fallback: simple ADX approximation
            out["adx"] = self._calc_adx_simple(highs, lows, closes)
            # Manual OBV slope
            if len(closes) >= 5:
                obv_sum = 0
                for i in range(1, len(closes)):
                    obv_sum += volumes[i] if closes[i] > closes[i-1] else -volumes[i]
                obv_prev = 0
                for i in range(1, len(closes) - 5):
                    obv_prev += volumes[i] if closes[i] > closes[i-1] else -volumes[i]
                out["obv_slope"] = round(max(-1.0, min(1.0, (obv_sum - obv_prev) / (abs(obv_prev) + 1e-9))), 4)
        return out

    def _calc_adx_simple(self, highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
        """Simple ADX approximation without ta library."""
        if len(closes) < period + 1:
            return 25.0
        # Average True Range as proxy for trend strength
        trs = []
        for i in range(-period, 0):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        atr = sum(trs) / period
        price_range = max(highs[-period:]) - min(lows[-period:])
        if price_range == 0:
            return 15.0
        # Normalized directional movement
        return round(min(50.0, (atr / closes[-1] * 100) * 15), 1)

    def _calc_composite_score(self, data: dict) -> float:
        """
        Composite technical score: -1.0 (strong bearish) to +1.0 (strong bullish).
        Aggregates ALL computed indicators with empirically-tuned weights.

        Weight distribution:
          Momentum signals (50%): change_pct, macd, ema_cross, momentum
          Volume signals (20%): volume_ratio, obv_slope, cmf
          Trend signals (20%): ichi_signal, bb_pos, vwap_dev, adx direction
          Pattern signals (10%): candlestick pattern_score
        """
        signals = []

        # ── MOMENTUM (50% weight) ──
        # Spot change: strongest single predictor for 5-min markets
        chg = data.get("change_pct", 0.0)
        if abs(chg) > 0.05:
            signals.append(("spot_momentum", max(-1.0, min(1.0, chg / 0.5)), 0.20))

        # MACD histogram direction
        macd = data.get("macd_hist", 0.0)
        if abs(macd) > 0.001:
            signals.append(("macd", max(-1.0, min(1.0, macd / 0.01)), 0.12))

        # EMA crossover
        ema_c = data.get("ema_cross", 0.0)
        if abs(ema_c) > 0.005:
            signals.append(("ema_cross", max(-1.0, min(1.0, ema_c / 0.1)), 0.10))

        # Raw momentum (candle direction count)
        mom = data.get("momentum", 0)
        if mom != 0:
            signals.append(("candle_momentum", max(-1.0, min(1.0, mom / 3.0)), 0.08))

        # ── VOLUME (20% weight) ──
        # Volume ratio: high volume confirms trend
        vol = data.get("volume_ratio", 1.0)
        vol_confirm = 0.0
        if vol > 1.5:
            vol_confirm = min(1.0, (vol - 1.0) / 3.0)  # high vol = confirm direction
        elif vol < 0.5:
            vol_confirm = -0.3  # low vol = suspect move
        # Direction from spot change
        if chg > 0:
            signals.append(("volume_confirm", vol_confirm, 0.08))
        elif chg < 0:
            signals.append(("volume_confirm", -vol_confirm, 0.08))

        # OBV slope: volume flow direction
        obv = data.get("obv_slope", 0.0)
        if abs(obv) > 0.01:
            signals.append(("obv_flow", max(-1.0, min(1.0, obv * 2)), 0.07))

        # CMF: money flow
        cmf = data.get("cmf", 0.0)
        if abs(cmf) > 0.01:
            signals.append(("cmf", max(-1.0, min(1.0, cmf * 3)), 0.05))

        # ── TREND (20% weight) ──
        # Ichimoku cloud signal
        ichi = data.get("ichi_signal", 0.0)
        if abs(ichi) > 0.1:
            signals.append(("ichimoku", max(-1.0, min(1.0, ichi)), 0.07))

        # Bollinger position (trend-following: >0.8=bullish, <0.2=bearish)
        bb = data.get("bb_pos", 0.5)
        bb_signal = (bb - 0.5) * 2  # normalize to -1..+1
        if abs(bb_signal) > 0.2:
            signals.append(("bollinger", max(-1.0, min(1.0, bb_signal)), 0.05))

        # VWAP deviation
        vwap = data.get("vwap_dev", 0.0)
        if abs(vwap) > 0.05:
            signals.append(("vwap", max(-1.0, min(1.0, vwap / 0.3)), 0.05))

        # ADX direction: +DI vs -DI
        adx = data.get("adx", 25.0)
        adx_p = data.get("adx_plus", 0.0)
        adx_m = data.get("adx_minus", 0.0)
        if adx > 20 and (adx_p + adx_m) > 0:
            adx_dir = (adx_p - adx_m) / (adx_p + adx_m)  # -1 to +1
            # Scale by ADX strength (stronger trend = more confident)
            adx_weight = min(1.0, adx / 40.0)
            signals.append(("adx_direction", adx_dir * adx_weight, 0.03))

        # ── PATTERNS (10% weight) ──
        pattern_score = CandlestickAnalyzer.pattern_score(data.get("patterns", []))
        if abs(pattern_score) > 0.1:
            signals.append(("candle_patterns", pattern_score, 0.10))

        # ── SENTIMENT (Fear & Greed) ──
        # Extreme fear (< 20) = contrarian bullish, extreme greed (> 80) = contrarian bearish
        fng_signal = (self._fng_value - 50) / 50.0  # -1 to +1
        if abs(fng_signal) > 0.4:
            signals.append(("fear_greed", fng_signal, 0.05))

        # ── AGGREGATE ──
        if not signals:
            return 0.0

        total_weight = sum(w for _, _, w in signals)
        if total_weight == 0:
            return 0.0

        score = sum(val * w for _, val, w in signals) / total_weight
        return round(max(-1.0, min(1.0, score)), 4)

    def _calc_macd(self, closes: list[float], fast: int = 3, slow: int = 15, signal: int = 3) -> float:
        """MACD(fast,slow,signal) histogram değeri döner. Pozitif=UP, Negatif=DOWN."""
        if len(closes) < slow + signal:
            return 0.0
        # EMA hesaplama
        def ema(data: list[float], period: int) -> list[float]:
            k = 2.0 / (period + 1)
            result = [data[0]]
            for val in data[1:]:
                result.append(val * k + result[-1] * (1 - k))
            return result
        ema_fast = ema(closes, fast)
        ema_slow = ema(closes, slow)
        macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
        signal_line = ema(macd_line, signal)
        return macd_line[-1] - signal_line[-1]

    def _calc_rsi(self, closes: list[float], period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(-period, 0):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    # ── Technical Analysis Indicators ─────────────────────────────────────

    @staticmethod
    def _ema(data: list[float], period: int) -> list[float]:
        """EMA hesaplama — tüm indikatörlerde kullanılır."""
        if not data:
            return []
        k = 2.0 / (period + 1)
        result = [data[0]]
        for val in data[1:]:
            result.append(val * k + result[-1] * (1 - k))
        return result

    def _calc_bollinger(self, closes: list[float], period: int = 20, num_std: float = 2.0) -> dict:
        """Bollinger Bands (20,2): squeeze/breakout detection."""
        if len(closes) < period:
            return {"bb_upper": 0.0, "bb_lower": 0.0, "bb_mid": 0.0,
                    "bb_width": 0.0, "bb_pos": 0.5,
                    "bb_squeeze": False, "bb_breakout": 0.0, "bb_bandwidth_pctile": 50.0}
        window = closes[-period:]
        mid = sum(window) / period
        variance = sum((x - mid) ** 2 for x in window) / period
        std = math.sqrt(variance)
        upper = mid + num_std * std
        lower = mid - num_std * std
        width = (upper - lower) / mid if mid > 0 else 0.0
        # Position: 0=lower band, 1=upper band
        pos = (closes[-1] - lower) / (upper - lower) if (upper - lower) > 0 else 0.5

        # ── Squeeze detection: bandwidth < 1.2% = low volatility compression ──
        is_squeeze = width < 0.012

        # ── Breakout detection: price crossing bands ──
        # +1.0 = upper band breakout (bullish), -1.0 = lower band breakout (bearish)
        breakout = 0.0
        price = closes[-1]
        prev_price = closes[-2] if len(closes) > 1 else price
        if price > upper and prev_price <= upper:
            breakout = 1.0   # upper band breakout
        elif price < lower and prev_price >= lower:
            breakout = -1.0  # lower band breakout
        elif price > upper:
            breakout = min(1.0, (price - upper) / (std if std > 0 else 1) * 0.5)
        elif price < lower:
            breakout = max(-1.0, (price - lower) / (std if std > 0 else 1) * 0.5)

        # ── Bandwidth percentile: how tight are bands vs recent history ──
        bb_bandwidth_pctile = 50.0
        if len(closes) >= period + 10:
            recent_widths = []
            for i in range(10):
                idx = len(closes) - 1 - i
                if idx >= period:
                    w = closes[idx - period:idx]
                    m = sum(w) / period
                    s = math.sqrt(sum((x - m) ** 2 for x in w) / period)
                    bw = (2 * num_std * s) / m if m > 0 else 0
                    recent_widths.append(bw)
            if recent_widths:
                below = sum(1 for w in recent_widths if w > width)
                bb_bandwidth_pctile = (below / len(recent_widths)) * 100

        return {
            "bb_upper": round(upper, 6),
            "bb_lower": round(lower, 6),
            "bb_mid": round(mid, 6),
            "bb_width": round(width, 6),
            "bb_pos": round(max(0.0, min(1.0, pos)), 4),
            "bb_squeeze": is_squeeze,
            "bb_breakout": round(breakout, 4),
            "bb_bandwidth_pctile": round(bb_bandwidth_pctile, 1),
        }

    def _calc_ema_cross(self, closes: list[float]) -> dict:
        """EMA crossover: 9/21 (short-term) signal."""
        if len(closes) < 21:
            return {"ema_9": 0.0, "ema_21": 0.0, "ema_cross": 0.0}
        ema9 = self._ema(closes, 9)
        ema21 = self._ema(closes, 21)
        # Cross signal: positive = bullish (9 above 21), negative = bearish
        cross = (ema9[-1] - ema21[-1]) / closes[-1] * 100 if closes[-1] > 0 else 0.0
        return {
            "ema_9": round(ema9[-1], 6),
            "ema_21": round(ema21[-1], 6),
            "ema_cross": round(cross, 4),
        }

    def _calc_atr(self, highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
        """ATR(14): volatilite ölçümü, fiyata göre normalize (%)."""
        if len(closes) < period + 1:
            return 0.0
        trs = []
        for i in range(-period, 0):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(tr)
        atr = sum(trs) / period
        # Fiyata göre normalize → yüzde
        return (atr / closes[-1] * 100) if closes[-1] > 0 else 0.0

    def _calc_stoch_rsi(self, closes: list[float], rsi_period: int = 14, stoch_period: int = 14,
                         k_smooth: int = 3, d_smooth: int = 3) -> dict:
        """Stochastic RSI(14,14,3,3): oversold/overbought momentum."""
        if len(closes) < rsi_period + stoch_period + 1:
            return {"stoch_k": 50.0, "stoch_d": 50.0}
        # RSI serisi hesapla
        rsi_values = []
        for i in range(rsi_period + 1, len(closes) + 1):
            rsi_values.append(self._calc_rsi(closes[:i], rsi_period))
        if len(rsi_values) < stoch_period:
            return {"stoch_k": 50.0, "stoch_d": 50.0}
        # Stochastic RSI
        stoch_vals = []
        for i in range(stoch_period - 1, len(rsi_values)):
            window = rsi_values[i - stoch_period + 1:i + 1]
            mn, mx = min(window), max(window)
            stoch_vals.append((rsi_values[i] - mn) / (mx - mn) * 100 if (mx - mn) > 0 else 50.0)
        # %K = SMA(stoch, k_smooth), %D = SMA(%K, d_smooth)
        if len(stoch_vals) >= k_smooth:
            k_line = sum(stoch_vals[-k_smooth:]) / k_smooth
        else:
            k_line = stoch_vals[-1] if stoch_vals else 50.0
        # For %D we'd need k_line history, simplified:
        d_line = k_line  # single-point approximation
        return {"stoch_k": round(k_line, 1), "stoch_d": round(d_line, 1)}

    def _calc_support_resistance(self, highs: list[float], lows: list[float], closes: list[float]) -> dict:
        """Son N mumdan support/resistance seviyeleri."""
        if len(highs) < 5:
            return {"support": 0.0, "resistance": 0.0, "sr_position": 0.5}
        n = min(20, len(highs))
        recent_highs = highs[-n:]
        recent_lows = lows[-n:]
        resistance = max(recent_highs)
        support = min(recent_lows)
        rng = resistance - support
        pos = (closes[-1] - support) / rng if rng > 0 else 0.5
        return {
            "support": round(support, 6),
            "resistance": round(resistance, 6),
            "sr_position": round(max(0.0, min(1.0, pos)), 4),
        }

    def _calc_vwap(self, highs: list[float], lows: list[float], closes: list[float],
                    volumes: list[float]) -> dict:
        """VWAP: volume-weighted average price, deviation."""
        if len(closes) < 3 or sum(volumes) == 0:
            return {"vwap": 0.0, "vwap_dev": 0.0}
        n = min(20, len(closes))
        total_vol = sum(volumes[-n:])
        if total_vol == 0:
            return {"vwap": closes[-1], "vwap_dev": 0.0}
        typical_prices = [(highs[-n + i] + lows[-n + i] + closes[-n + i]) / 3 for i in range(n)]
        vwap = sum(tp * v for tp, v in zip(typical_prices, volumes[-n:])) / total_vol
        # Deviation: current price vs VWAP (%)
        dev = (closes[-1] - vwap) / vwap * 100 if vwap > 0 else 0.0
        return {"vwap": round(vwap, 6), "vwap_dev": round(dev, 4)}

    def _calc_ichimoku(self, highs: list[float], lows: list[float], closes: list[float]) -> dict:
        """Ichimoku Cloud (9,26,52): trend direction + cloud position."""
        n = len(highs)
        if n < 26:
            return {"ichi_signal": 0.0, "ichi_cloud_top": 0.0, "ichi_cloud_bot": 0.0}
        # Tenkan-sen (conversion line): (9-period high + 9-period low) / 2
        tenkan = (max(highs[-9:]) + min(lows[-9:])) / 2
        # Kijun-sen (base line): (26-period high + 26-period low) / 2
        kijun = (max(highs[-26:]) + min(lows[-26:])) / 2
        # Senkou Span A: (Tenkan + Kijun) / 2
        span_a = (tenkan + kijun) / 2
        # Senkou Span B: (52-period high + 52-period low) / 2
        if n >= 52:
            span_b = (max(highs[-52:]) + min(lows[-52:])) / 2
        else:
            span_b = (max(highs) + min(lows)) / 2
        cloud_top = max(span_a, span_b)
        cloud_bot = min(span_a, span_b)
        # Signal: price above cloud = bullish, below = bearish, normalized
        price = closes[-1]
        if cloud_top == cloud_bot:
            signal = 0.0
        elif price > cloud_top:
            signal = min(1.0, (price - cloud_top) / (cloud_top - cloud_bot + 0.001))
        elif price < cloud_bot:
            signal = max(-1.0, (price - cloud_bot) / (cloud_top - cloud_bot + 0.001))
        else:
            signal = (price - cloud_bot) / (cloud_top - cloud_bot) * 2 - 1  # inside cloud
        # Tenkan/Kijun cross signal
        tk_cross = (tenkan - kijun) / price * 100 if price > 0 else 0.0
        return {
            "ichi_signal": round(signal, 4),
            "ichi_tk_cross": round(tk_cross, 4),
            "ichi_cloud_top": round(cloud_top, 6),
            "ichi_cloud_bot": round(cloud_bot, 6),
        }

    def _calc_fibonacci(self, highs: list[float], lows: list[float], closes: list[float]) -> dict:
        """Fibonacci retracement: son swing'den seviyelere mesafe."""
        n = min(20, len(highs))
        if n < 5:
            return {"fib_level": 0.5, "fib_retrace": 0.5}
        swing_high = max(highs[-n:])
        swing_low = min(lows[-n:])
        rng = swing_high - swing_low
        if rng <= 0:
            return {"fib_level": 0.5, "fib_retrace": 0.5}
        # Current position as fibonacci level (0 = low, 1 = high)
        fib_level = (closes[-1] - swing_low) / rng
        # Closest standard fib level: 0.236, 0.382, 0.5, 0.618, 0.786
        fibs = [0.236, 0.382, 0.5, 0.618, 0.786]
        closest = min(fibs, key=lambda f: abs(fib_level - f))
        return {
            "fib_level": round(fib_level, 4),
            "fib_retrace": round(closest, 3),
        }

    def get_signal(self, symbol: str, timeframe: str = "1h") -> dict:
        """Verilen sembol ve zaman dilimi için sinyal dict döner."""
        cached = self._cache.get(symbol, {})
        iv_data = cached.get("intervals", {}).get(timeframe, {})

        # İstenen timeframe verisi yoksa 1h'ye fallback et (5m verisi bazen eksik)
        if not iv_data:
            iv_data = cached.get("intervals", {}).get("1h", {})

        return {
            "price":        cached.get("price", 0.0),
            "ob_imbalance": cached.get("ob_imbalance", 0.0),
            "change_pct":   iv_data.get("change_pct", 0.0),
            "trend_pct":    iv_data.get("trend_pct", 0.0),
            "macro_trend_pct": iv_data.get("macro_trend_pct", 0.0),
            "volume_ratio": iv_data.get("volume_ratio", 1.0),
            "rsi":          iv_data.get("rsi", 50.0),
            "momentum":     iv_data.get("momentum", 0),
            "macd_hist":    iv_data.get("macd_hist", 0.0),
            "volatility":   abs(iv_data.get("trend_pct", 0.0)) / 100 + 0.005,
            "patterns":     iv_data.get("patterns", []),
            # ── New technical indicators ──
            "bb_width":     iv_data.get("bb_width", 0.0),
            "bb_pos":       iv_data.get("bb_pos", 0.5),
            # bb_squeeze/bb_breakout are computed alongside bb_width/bb_pos above
            # and cached in iv_data, but were never forwarded here —
            # strategies/arbitrage_engine.py._evaluate_market() reads these via
            # spot.get("bb_squeeze", False)/spot.get("bb_breakout", 0.0), so the
            # missing keys always fell back to the default and
            # strategies/bayesian.py's Bollinger breakout/squeeze signal
            # (bb_breakout * (1.5 if bb_squeeze else 1.0)) never fired live —
            # identical in shape to the bullish_exhaustion field-mismatch bug.
            "bb_squeeze":   iv_data.get("bb_squeeze", False),
            "bb_breakout":  iv_data.get("bb_breakout", 0.0),
            "ema_cross":    iv_data.get("ema_cross", 0.0),
            "atr_pct":      iv_data.get("atr_pct", 0.0),
            "stoch_k":      iv_data.get("stoch_k", 50.0),
            "stoch_d":      iv_data.get("stoch_d", 50.0),
            "sr_position":  iv_data.get("sr_position", 0.5),
            "vwap_dev":     iv_data.get("vwap_dev", 0.0),
            "ichi_signal":  iv_data.get("ichi_signal", 0.0),
            "ichi_tk_cross": iv_data.get("ichi_tk_cross", 0.0),
            "fib_level":    iv_data.get("fib_level", 0.5),
            # OPT-3: momentum deceleration
            "momentum_decelerating": iv_data.get("momentum_decelerating", False),
            # Bounce detection
            "consecutive_bearish": iv_data.get("consecutive_bearish", 0),
            "consecutive_bullish": iv_data.get("consecutive_bullish", 0),
            "bounce_signal": iv_data.get("bounce_signal", False),
            "pre_bounce_streak": iv_data.get("pre_bounce_streak", 0),
            # Bullish exhaustion (computed alongside bounce detection above and
            # cached in iv_data, but never forwarded here — strategies/
            # arbitrage_engine.py._evaluate_market() reads these via
            # spot.get("bullish_exhaustion", False)/spot.get(
            # "bullish_exhaustion_magnitude", 0.0), so the missing keys always
            # fell back to the default and its EXHAUSTION_NO_ACTIVATE gate
            # (legitimate NO-entry on an overextended bullish run) never fired
            # live, identical in shape to the OPT-7 field-mismatch bug).
            "bullish_exhaustion": iv_data.get("bullish_exhaustion", False),
            "bullish_exhaustion_magnitude": iv_data.get("bullish_exhaustion_magnitude", 0.0),
            # ta library indicators
            "adx": iv_data.get("adx", 25.0),
            "adx_plus": iv_data.get("adx_plus", 0.0),
            "adx_minus": iv_data.get("adx_minus", 0.0),
            "obv_slope": iv_data.get("obv_slope", 0.0),
            "cmf": iv_data.get("cmf", 0.0),
            # Composite technical score
            "tech_score": iv_data.get("tech_score", 0.0),
            # Multi-exchange consensus
            "consensus_price": cached.get("multi_exchange", {}).get("consensus_price", 0.0),
            "exchange_spread_pct": cached.get("multi_exchange", {}).get("exchange_spread_pct", 0.0),
            "exchange_count": cached.get("multi_exchange", {}).get("exchange_count", 0),
            # Fear & Greed Index
            "fng_value": self._fng_value,
            "fng_signal": (self._fng_value - 50) / 50.0,
            # GARCH volatility
            "garch_forecast": iv_data.get("garch_forecast", 0.0),
            "vol_expanding": iv_data.get("vol_expanding", False),
        }

    def get_candle_analysis(self, symbol: str) -> dict:
        """
        Multi-timeframe candlestick analysis for a symbol.
        Analyzes patterns + trends across 4h/1h/15m/5m timeframes.

        Returns:
            {
                "candle_bias": float,      # -1 to +1 weighted directional signal
                "candle_strength": float,  # 0-1 confidence in the signal
                "dominant_tf": str,        # which TF has strongest pattern
                "pattern_count": int,      # total patterns across all TFs
                "alignment": float,        # 0-1 how aligned TFs are
                "tf_patterns": dict,       # patterns per TF
                "tf_trends": dict,         # trend analysis per TF
                "tf_scores": dict,         # combined score per TF
            }
        """
        cached = self._cache.get(symbol, {})
        raw_klines = cached.get("raw_klines", {})

        if not raw_klines:
            return {
                "candle_bias": 0.0, "candle_strength": 0.0,
                "dominant_tf": "1h", "pattern_count": 0,
                "alignment": 0.0, "tf_patterns": {}, "tf_trends": {},
                "tf_scores": {},
            }

        # Lookback per timeframe: how many candles to analyze for trend
        tf_lookback = {"4h": 6, "1h": 24, "15m": 16, "5m": 12}

        tf_patterns = {}
        tf_trends = {}

        for tf, klines in raw_klines.items():
            if not klines or len(klines) < 3:
                continue
            # Pattern detection (uses last 3-5 candles)
            tf_patterns[tf] = CandlestickAnalyzer.analyze(klines)
            # Trend analysis over lookback window
            lookback = tf_lookback.get(tf, 6)
            tf_trends[tf] = CandlestickAnalyzer.trend_analysis(klines, lookback)

        # Multi-TF aggregation
        result = CandlestickAnalyzer.multi_tf_score(tf_patterns, tf_trends)
        result["tf_patterns"] = tf_patterns
        result["tf_trends"] = tf_trends
        return result

    def get_market_regime(self) -> dict:
        """BTC + ETH 4h trend'inden piyasa rejimi belirle.

        Returns:
            {
                "regime": "BULLISH" | "BEARISH" | "NEUTRAL",
                "btc_4h_pct": float,   # BTC 4h change %
                "eth_4h_pct": float,   # ETH 4h change %
                "btc_5m_pct": float,   # BTC 5m anlık hareket %
                "eth_5m_pct": float,   # ETH 5m anlık hareket %
                "strength": float,     # 0-1 rejim gücü
            }
        """
        btc = self._cache.get("BTCUSDT", {})
        eth = self._cache.get("ETHUSDT", {})

        btc_4h = btc.get("intervals", {}).get("4h", {})
        eth_4h = eth.get("intervals", {}).get("4h", {})
        btc_5m = btc.get("intervals", {}).get("5m", {})
        eth_5m = eth.get("intervals", {}).get("5m", {})

        btc_4h_pct = btc_4h.get("change_pct", 0.0)
        eth_4h_pct = eth_4h.get("change_pct", 0.0)
        btc_5m_pct = btc_5m.get("change_pct", 0.0)
        eth_5m_pct = eth_5m.get("change_pct", 0.0)

        # 4h trend: BTC %60 ağırlık, ETH %40
        trend_4h = btc_4h_pct * 0.6 + eth_4h_pct * 0.4

        # 5m ani hareket: büyük düşüş = kaskad sinyali
        momentum_5m = btc_5m_pct * 0.6 + eth_5m_pct * 0.4

        # Rejim belirleme
        # 4h trend yönü + 5m momentum birleşimi
        combined = trend_4h * 0.6 + momentum_5m * 0.4

        if combined > 0.15:
            regime = "BULLISH"
        elif combined < -0.15:
            regime = "BEARISH"
        else:
            regime = "NEUTRAL"

        # Divider 2.5: BTC -1% → str≈0.28, -2% → str≈0.72, -3%+ → str=1.00
        # Eski 1.0 divider her şeyi 1.00'a satüre ediyordu (nüans kaybı)
        strength = min(1.0, abs(combined) / 2.5)

        # ── 4H VOLUME DIP BOUNCE (56.76% bounce probability) ──
        # Quant: 4h bearish candle + volume > 2x avg = capitulation → bounce
        _4h_vol_bounce = False
        _btc_cached = self._cache.get("BTCUSDT", {})
        _btc_4h_iv = _btc_cached.get("intervals", {}).get("4h", {})
        _btc_4h_vol_ratio = _btc_4h_iv.get("volume_ratio", 1.0)
        _btc_4h_change = _btc_4h_iv.get("change_pct", 0.0)
        if _btc_4h_vol_ratio > 2.0 and _btc_4h_change < -0.3:
            _4h_vol_bounce = True
            logger.info(
                f"4H_VOL_DIP_BOUNCE: BTC 4h vol={_btc_4h_vol_ratio:.1f}x "
                f"chg={_btc_4h_change:+.2f}% → bounce signal active"
            )

        logger.info(
            f"REGIME: {regime} (str={strength:.2f}) | "
            f"BTC 4h={btc_4h_pct:+.2f}% 5m={btc_5m_pct:+.2f}% | "
            f"ETH 4h={eth_4h_pct:+.2f}% 5m={eth_5m_pct:+.2f}%"
        )

        return {
            "regime": regime,
            "btc_4h_pct": btc_4h_pct,
            "eth_4h_pct": eth_4h_pct,
            "btc_5m_pct": btc_5m_pct,
            "eth_5m_pct": eth_5m_pct,
            "strength": float(f"{strength:.3f}"),
            "4h_vol_bounce": _4h_vol_bounce,
        }

    async def close(self) -> None:
        """Cleanup ccxt exchange connections."""
        for ex in self._exchanges:
            try:
                await ex.close()
            except Exception:
                pass
        self._exchanges = []

    def get_mtf_consensus(self, symbol: str) -> dict:
        """Multi-Timeframe Consensus: 5m, 15m, 1h yönlerini karşılaştır.

        Returns:
            {
                "aligned": bool,        # Tüm TF'ler aynı yönde mi
                "direction": str,       # "UP" / "DOWN" / "MIXED"
                "agreement": float,     # 0.0-1.0 (1=tam uyum)
                "details": dict,        # her TF'nin change_pct'si
                "boost": float,         # -0.02 to +0.02 edge boost
            }
        """
        cached = self._cache.get(symbol, {})
        intervals = cached.get("intervals", {})

        tf_directions = {}
        tf_changes = {}
        noise_threshold = 0.10  # %0.10'dan küçük hareket = noise

        for tf in ("5m", "15m", "1h"):
            iv = intervals.get(tf, {})
            chg = iv.get("change_pct", 0.0)
            tf_changes[tf] = chg
            if chg > noise_threshold:
                tf_directions[tf] = "UP"
            elif chg < -noise_threshold:
                tf_directions[tf] = "DOWN"
            else:
                tf_directions[tf] = "FLAT"

        directions = [d for d in tf_directions.values() if d != "FLAT"]
        if not directions:
            return {
                "aligned": False, "direction": "MIXED",
                "agreement": 0.0, "details": tf_changes, "boost": 0.0,
            }

        up_count = sum(1 for d in directions if d == "UP")
        down_count = sum(1 for d in directions if d == "DOWN")
        total = len(directions)

        if up_count == total:
            direction = "UP"
            agreement = 1.0
        elif down_count == total:
            direction = "DOWN"
            agreement = 1.0
        else:
            direction = "MIXED"
            agreement = max(up_count, down_count) / total

        aligned = agreement >= 1.0 and total >= 2

        # Boost: tam uyum = +/-0.02, kısmi = 0
        boost = 0.0
        if aligned:
            # Ağırlıklı ortalama: 1h %50, 15m %30, 5m %20
            weighted_chg = (
                tf_changes.get("1h", 0) * 0.50 +
                tf_changes.get("15m", 0) * 0.30 +
                tf_changes.get("5m", 0) * 0.20
            )
            boost = max(-0.02, min(0.02, weighted_chg * 0.01))

        return {
            "aligned": aligned,
            "direction": direction,
            "agreement": round(agreement, 2),
            "details": tf_changes,
            "boost": round(boost, 4),
        }

    def get_recent_change(self, symbol: str, seconds: int = 60) -> float:
        """Return price change % over last N seconds. 0.0 if insufficient data."""
        import time as _t
        history = self._price_history.get(symbol, [])
        if len(history) < 2:
            return 0.0
        now_ts = _t.time()
        cutoff = now_ts - seconds
        # Find oldest price within the window
        old_price = None
        for ts, p in history:
            if ts >= cutoff:
                old_price = p
                break
        if old_price is None or old_price <= 0:
            return 0.0
        current_price = history[-1][1]
        return ((current_price - old_price) / old_price) * 100

    def has_data(self, symbol: str) -> bool:
        return bool(self._cache.get(symbol, {}).get("price"))
