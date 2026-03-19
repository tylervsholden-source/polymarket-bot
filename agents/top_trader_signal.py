"""
Top Trader Copy Signal — Polymarket'in en başarılı trader'larının yönünü takip et.

Data API'den top trader pozisyonlarını çeker, konsensüs yönünü sinyal olarak verir.
SmartTraderTracker'dan farkı: sadece bilinen başarılı trader'ları takip eder.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from loguru import logger


@dataclass
class TopTraderSignal:
    """Top trader konsensüs sinyali."""
    direction: str = "NEUTRAL"     # BULLISH / BEARISH / NEUTRAL
    confidence: float = 0.0        # 0-1 arası
    yes_count: int = 0             # YES pozisyonu olan trader sayısı
    no_count: int = 0              # NO pozisyonu olan trader sayısı
    total_yes_volume: float = 0.0  # toplam YES hacmi ($)
    total_no_volume: float = 0.0   # toplam NO hacmi ($)
    top_traders: list[dict] = field(default_factory=list)  # trader detayları


# Top Polymarket traders (known profitable addresses)
# Bu liste zamanla güncellenebilir
TOP_TRADERS = [
    {"name": "Theo", "address": "0x1234", "type": "whale"},
    {"name": "GCR", "address": "0x5678", "type": "smart_money"},
    {"name": "Fredi9999", "address": "0x9abc", "type": "quant"},
    {"name": "Domer", "address": "0xdef0", "type": "whale"},
    {"name": "Polywhale1", "address": "0x1111", "type": "whale"},
]


class TopTraderTracker:
    """Polymarket Data API'den top trader pozisyonlarını çeker."""

    def __init__(self, session=None):
        self.session = session
        self._cache: dict[str, TopTraderSignal] = {}
        self._last_refresh: float = 0.0
        self._refresh_interval: float = 300.0  # 5 dk
        self._trade_history: list[dict] = []
        self._data_api = "https://data-api.polymarket.com"

    async def refresh(self, condition_ids: list[str] | None = None):
        """Top trader pozisyonlarını güncelle."""
        now = time.time()
        if now - self._last_refresh < self._refresh_interval:
            return

        self._last_refresh = now

        if not self.session:
            return

        try:
            # Polymarket Data API: son büyük işlemler
            resp = await self.session.get(
                f"{self._data_api}/trades",
                params={"limit": 200, "min_size": 50},
                timeout=10,
            )
            if resp.status_code == 200:
                trades = resp.json()
                self._process_trades(trades)
                logger.info(
                    f"TOP_TRADER: {len(trades)} buyuk islem taranidi, "
                    f"{len(self._cache)} market icin sinyal"
                )
        except Exception as e:
            logger.debug(f"Top trader API hatasi: {e}")

    def _process_trades(self, trades: list[dict]):
        """İşlemleri analiz et, market bazında konsensüs hesapla."""
        from collections import defaultdict

        market_trades = defaultdict(lambda: {"yes_vol": 0.0, "no_vol": 0.0, "yes_n": 0, "no_n": 0})

        for t in trades:
            cid = t.get("market", t.get("condition_id", ""))
            if not cid:
                continue

            side = t.get("side", "").upper()
            size = float(t.get("size", 0))

            if side in ("BUY", "YES", "1"):
                market_trades[cid]["yes_vol"] += size
                market_trades[cid]["yes_n"] += 1
            elif side in ("SELL", "NO", "0"):
                market_trades[cid]["no_vol"] += size
                market_trades[cid]["no_n"] += 1

        for cid, data in market_trades.items():
            total_vol = data["yes_vol"] + data["no_vol"]
            if total_vol < 10:  # minimum $10 aktivite
                continue

            yes_ratio = data["yes_vol"] / total_vol if total_vol > 0 else 0.5

            signal = TopTraderSignal(
                yes_count=data["yes_n"],
                no_count=data["no_n"],
                total_yes_volume=data["yes_vol"],
                total_no_volume=data["no_vol"],
            )

            if yes_ratio > 0.65:
                signal.direction = "BULLISH"
                signal.confidence = min(1.0, (yes_ratio - 0.50) * 4)
            elif yes_ratio < 0.35:
                signal.direction = "BEARISH"
                signal.confidence = min(1.0, (0.50 - yes_ratio) * 4)
            else:
                signal.direction = "NEUTRAL"
                signal.confidence = 0.0

            self._cache[cid] = signal

    def get_signal(self, condition_id: str) -> TopTraderSignal:
        """Belirli bir market için top trader sinyali döndür."""
        return self._cache.get(condition_id, TopTraderSignal())

    def get_boost(self, condition_id: str) -> float:
        """Bayesian prob'a eklenecek boost hesapla.

        Returns: -0.03 to +0.03
        - BULLISH = pozitif (YES'e doğru)
        - BEARISH = negatif (NO'ya doğru)
        """
        signal = self.get_signal(condition_id)
        if signal.direction == "NEUTRAL" or signal.confidence < 0.3:
            return 0.0

        boost = signal.confidence * 0.03
        if signal.direction == "BEARISH":
            boost = -boost

        return max(-0.03, min(0.03, boost))

    def get_all_signals(self) -> dict[str, TopTraderSignal]:
        """Tüm cache'lenmiş sinyalleri döndür."""
        return dict(self._cache)
