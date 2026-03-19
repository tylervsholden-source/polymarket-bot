"""
CryptoFeed — Bitstamp REST API'den gerçek zamanlı çoklu zaman dilimi verisi.
(Binance kurumsal proxy/VPN nedeniyle erişilemiyor; Bitstamp çalışıyor.)

Her coin için: anlık fiyat, RSI(14), momentum, hacim oranı, order book imbalance.
Zaman dilimleri: 5m, 15m, 1h, 4h
"""
from __future__ import annotations

import asyncio
import math
from loguru import logger

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

KLINES_LIMIT = 20   # RSI için yeterli


class BinanceFeed:
    """Bitstamp tabanlı çoklu zaman dilimi veri besleyici.
    Dış API arayüzü değişmedi — BinanceFeed adı korunuyor."""

    def __init__(self, session=None):
        import httpx
        self.session = httpx.AsyncClient(timeout=10, verify=False)
        self._cache: dict[str, dict] = {}   # symbol → {price, ob_imbalance, intervals}

    async def refresh(self, symbols: set[str]) -> None:
        """Verilen semboller için tüm verileri paralel çek."""
        if not symbols:
            return
        tasks = [self._fetch_symbol(sym) for sym in symbols]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _fetch_symbol(self, symbol: str) -> None:
        pair = _SYMBOL_MAP.get(symbol)
        if not pair:
            logger.debug(f"CryptoFeed: bilinmeyen sembol {symbol}")
            return
        try:
            kline_tasks = {iv: self._fetch_klines(pair, iv) for iv in _STEPS}
            kline_results = await asyncio.gather(*kline_tasks.values(), return_exceptions=True)

            price_task, ob_task = await asyncio.gather(
                self._fetch_price(pair),
                self._fetch_ob(pair),
                return_exceptions=True,
            )

            intervals: dict[str, dict] = {}
            for iv, result in zip(kline_tasks.keys(), kline_results):
                if isinstance(result, list) and len(result) >= 3:
                    intervals[iv] = self._process_klines(result)

            self._cache[symbol] = {
                "price": price_task if isinstance(price_task, float) else 0.0,
                "ob_imbalance": ob_task if isinstance(ob_task, float) else 0.0,
                "intervals": intervals,
            }

            if intervals:
                sig_1h = intervals.get("1h", {})
                price_val = price_task if isinstance(price_task, float) else 0.0
                ob_val = ob_task if isinstance(ob_task, float) else 0.0
                logger.debug(
                    f"CryptoFeed {symbol}: ${price_val:.4f} | "
                    f"1h chg={sig_1h.get('change_pct', 0):+.2f}% "
                    f"RSI={sig_1h.get('rsi', 50):.0f} "
                    f"vol={sig_1h.get('volume_ratio', 1):.1f}x "
                    f"OB={ob_val:+.2f}"
                )
        except Exception as e:
            logger.debug(f"CryptoFeed._fetch_symbol {symbol}: {e}")

    async def _fetch_klines(self, pair: str, interval: str) -> list | None:
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
            logger.debug(f"CryptoFeed._fetch_klines {pair}/{interval}: {e}")
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
        closes  = [float(k[4]) for k in klines]
        opens   = [float(k[1]) for k in klines]
        volumes = [float(k[5]) for k in klines]

        # Mevcut mumun % değişimi (open → close)
        cur_open  = opens[-1]
        cur_close = closes[-1]
        change_pct = ((cur_close - cur_open) / cur_open * 100) if cur_open > 0 else 0.0

        # Son N mumun toplam yönsel değişim (trend)
        n = min(4, len(closes) - 1)
        trend_pct = ((closes[-1] - closes[-n-1]) / closes[-n-1] * 100) if closes[-n-1] > 0 else 0.0

        # Hacim oranı: güncel mum / son N mum ortalaması
        avg_vol = sum(volumes[:-1]) / max(len(volumes) - 1, 1)
        vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1.0

        # RSI(14)
        rsi = self._calc_rsi(closes, 14)

        # Momentum: son 3 mumun yönü (+1/-1 her mum)
        momentum = sum(
            1 if closes[i] > closes[i - 1] else -1
            for i in range(max(1, len(closes) - 3), len(closes))
        )

        return {
            "change_pct": round(change_pct, 4),
            "trend_pct": round(trend_pct, 4),
            "volume_ratio": round(vol_ratio, 3),
            "rsi": round(rsi, 1),
            "momentum": momentum,
        }

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

    def get_signal(self, symbol: str, timeframe: str = "1h") -> dict:
        """Verilen sembol ve zaman dilimi için sinyal dict döner."""
        cached = self._cache.get(symbol, {})
        iv_data = cached.get("intervals", {}).get(timeframe, {})
        return {
            "price":        cached.get("price", 0.0),
            "ob_imbalance": cached.get("ob_imbalance", 0.0),
            "change_pct":   iv_data.get("change_pct", 0.0),
            "trend_pct":    iv_data.get("trend_pct", 0.0),
            "volume_ratio": iv_data.get("volume_ratio", 1.0),
            "rsi":          iv_data.get("rsi", 50.0),
            "momentum":     iv_data.get("momentum", 0),
            "volatility":   abs(iv_data.get("trend_pct", 0.0)) / 100 + 0.005,
        }

    def has_data(self, symbol: str) -> bool:
        return bool(self._cache.get(symbol, {}).get("price"))
