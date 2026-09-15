"""
Whale Tracker — Büyük pozisyon hareketleri + hacim artış analizi

Araştırma bulgusu: $10K+ pozisyon son 6 saatte + volume artışı >%30 →
aynı yönde trade açmak win rate'i +5-8 puan artırıyor.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import httpx
from loguru import logger


DATA_API = "https://data-api.polymarket.com"
MIN_WHALE_USDC = 500       # $500+ işlem = whale sayılır
SMART_MONEY_USDC = 10_000  # $10K+ = "akıllı para" (araştırma eşiği)


class WhaleTracker:
    def __init__(self):
        self.session = httpx.AsyncClient(timeout=15)

    async def get_activity(self, condition_id: str) -> dict:
        """
        Son 200 işlemi çeker ve analiz eder.

        Dönüş:
          direction          : BULLISH / BEARISH / NEUTRAL
          large_buys         : $500+ alım sayısı
          large_sells        : $500+ satış sayısı
          total_volume       : Toplam whale hacmi
          smart_money_buys   : $10K+ alım (son 6 saat)
          smart_money_sells  : $10K+ satış (son 6 saat)
          volume_surge       : Son 2 saatteki hacim / önceki 2 saatteki hacim
          whale_alignment    : "BUY" | "SELL" | "NEUTRAL"
        """
        try:
            trades = await self._fetch_recent_trades(condition_id)
            return self._analyze(trades)
        except Exception as e:
            logger.warning(f"Whale tracker hatası: {e}")
            return self._empty()

    async def _fetch_recent_trades(self, condition_id: str) -> list:
        resp = await self.session.get(
            f"{DATA_API}/trades",
            params={"market": condition_id, "limit": 200},
            headers={"User-Agent": "polymarket-bot/1.0"},
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    def _analyze(self, trades: list) -> dict:
        now = datetime.now(timezone.utc)
        cutoff_6h = now - timedelta(hours=6)
        cutoff_2h = now - timedelta(hours=2)
        cutoff_4h = now - timedelta(hours=4)  # Referans penceresi (2h-4h arası)

        large_buys = 0
        large_sells = 0
        smart_money_buys = 0
        smart_money_sells = 0
        total_volume = 0.0
        volume_2h = 0.0   # Son 2 saatteki hacim
        volume_4h = 0.0   # 2-4 saat önceki hacim (karşılaştırma için)

        for trade in trades:
            size = float(trade.get("size", 0) or 0)
            price = float(trade.get("price", 0) or 0)
            usdc_value = size * price
            side = str(trade.get("side", "")).upper()
            outcome = str(trade.get("outcome", "")).upper()

            # `side` (BUY/SELL) tek başına yön değil, hangi outcome token'ının
            # alınıp satıldığını gösterir. Gerçek yön için `outcome` (YES/UP
            # veya NO/DOWN) ile birleştirilmeli — agents/top_trader_signal.py
            # ve agents/copytrade.py bu aynı data-api şemasını aynı şekilde
            # işliyor. side'ı tek başına okumak, NO/DOWN tarafında yoğunlaşan
            # whale alımlarını BULLISH gibi raporlar (yön tersine döner).
            is_bullish_trade = (side == "BUY" and outcome in ("YES", "UP")) or (
                side == "SELL" and outcome in ("NO", "DOWN")
            )
            is_bearish_trade = (side == "SELL" and outcome in ("YES", "UP")) or (
                side == "BUY" and outcome in ("NO", "DOWN")
            )

            # Trade zamanı
            ts = trade.get("timestamp") or trade.get("createdAt") or ""
            trade_time = self._parse_time(ts)

            # Hacim pencere analizi
            if trade_time:
                if trade_time >= cutoff_2h:
                    volume_2h += usdc_value
                elif trade_time >= cutoff_4h:
                    volume_4h += usdc_value

            if usdc_value < MIN_WHALE_USDC:
                continue

            total_volume += usdc_value

            if is_bullish_trade:
                large_buys += 1
            elif is_bearish_trade:
                large_sells += 1

            # Akıllı para: $10K+, son 6 saat
            if usdc_value >= SMART_MONEY_USDC and trade_time and trade_time >= cutoff_6h:
                if is_bullish_trade:
                    smart_money_buys += 1
                elif is_bearish_trade:
                    smart_money_sells += 1

        # Yön kararı (akıllı para varsa onu önceliklendir)
        if smart_money_buys > 0 or smart_money_sells > 0:
            if smart_money_buys > smart_money_sells:
                direction = "BULLISH"
            elif smart_money_sells > smart_money_buys:
                direction = "BEARISH"
            else:
                direction = "NEUTRAL"
        elif large_buys > large_sells * 1.5:
            direction = "BULLISH"
        elif large_sells > large_buys * 1.5:
            direction = "BEARISH"
        else:
            direction = "NEUTRAL"

        # Hacim artış oranı (organik ilgi göstergesi)
        volume_surge = (volume_2h / volume_4h) if volume_4h > 10 else 1.0

        # QualityFilter için uyumlu yön
        if direction == "BULLISH":
            whale_alignment = "BUY"
        elif direction == "BEARISH":
            whale_alignment = "SELL"
        else:
            whale_alignment = "NEUTRAL"

        if smart_money_buys > 0 or smart_money_sells > 0:
            logger.debug(
                f"Akıllı para: {smart_money_buys} alım / {smart_money_sells} satış | "
                f"Hacim artışı: {volume_surge:.1f}x"
            )

        return {
            "large_buys": large_buys,
            "large_sells": large_sells,
            "direction": direction,
            "whale_alignment": whale_alignment,
            "total_volume": total_volume,
            "smart_money_buys": smart_money_buys,
            "smart_money_sells": smart_money_sells,
            "volume_surge": round(volume_surge, 2),
        }

    def _parse_time(self, ts: str) -> datetime | None:
        if not ts:
            return None
        try:
            clean = ts.replace("Z", "+00:00")
            return datetime.fromisoformat(clean)
        except Exception:
            try:
                return datetime.fromtimestamp(float(ts), tz=timezone.utc)
            except Exception:
                return None

    def _empty(self) -> dict:
        return {
            "large_buys": 0,
            "large_sells": 0,
            "direction": "NEUTRAL",
            "whale_alignment": "NEUTRAL",
            "total_volume": 0.0,
            "smart_money_buys": 0,
            "smart_money_sells": 0,
            "volume_surge": 1.0,
        }
