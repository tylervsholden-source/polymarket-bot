"""
Kalshi Cross-Arb — Polymarket vs Kalshi fiyat farkı tespiti.

Aynı event'i iki venue'de karşılaştırır:
- Polymarket YES price vs Kalshi YES price
- Fark > threshold ise arbitraj fırsatı

Not: Kalshi API key gerektirir. Yoksa public endpoint'lerden tahmin yapar.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from loguru import logger


@dataclass
class CrossArbSignal:
    """Cross-venue arbitraj sinyali."""
    poly_yes: float = 0.0          # Polymarket YES fiyatı
    kalshi_yes: float = 0.0        # Kalshi YES fiyatı
    spread: float = 0.0            # fiyat farkı (poly - kalshi)
    spread_pct: float = 0.0        # yüzde olarak fark
    arb_direction: str = "NONE"    # "BUY_POLY" / "BUY_KALSHI" / "NONE"
    estimated_profit: float = 0.0  # tahmini kar ($)
    event_name: str = ""
    is_actionable: bool = False    # yeterli spread ve likidite var mı


# Crypto up/down market keyword → Kalshi event series mapping
KALSHI_CRYPTO_EVENTS = {
    "bitcoin": "KXBTC",
    "ethereum": "KXETH",
    "solana": "KXSOL",
}


class KalshiArbTracker:
    """Kalshi API'den fiyat çekip Polymarket ile karşılaştırır."""

    def __init__(self, session=None):
        self.session = session
        self._kalshi_api = "https://api.elections.kalshi.com/trade-api/v2"
        self._cache: dict[str, dict] = {}  # event → {yes_price, timestamp}
        self._last_refresh: float = 0.0
        self._refresh_interval: float = 120.0  # 2 dk
        self._arb_signals: list[CrossArbSignal] = []
        self._min_spread: float = float(os.getenv("KALSHI_MIN_SPREAD", 0.03))

    async def refresh(self):
        """Kalshi'den crypto event fiyatlarını çek."""
        now = time.time()
        if now - self._last_refresh < self._refresh_interval:
            return

        self._last_refresh = now

        if not self.session:
            return

        for asset, series in KALSHI_CRYPTO_EVENTS.items():
            try:
                resp = await self.session.get(
                    f"{self._kalshi_api}/markets",
                    params={
                        "series_ticker": series,
                        "status": "open",
                        "limit": 5,
                    },
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    markets = data.get("markets", [])
                    for m in markets:
                        ticker = m.get("ticker", "")
                        yes_price = m.get("yes_ask", 0) / 100.0  # Kalshi cents → dollars
                        if yes_price > 0:
                            self._cache[f"{asset}_{ticker}"] = {
                                "yes_price": yes_price,
                                "no_price": 1.0 - yes_price,
                                "ticker": ticker,
                                "title": m.get("title", ""),
                                "timestamp": now,
                            }
                    logger.info(f"KALSHI: {asset} {len(markets)} market taranidi")
                elif resp.status_code == 401:
                    logger.debug("Kalshi API key gerekli, public data kullaniliyor")
                    break
            except Exception as e:
                logger.debug(f"Kalshi API hatasi ({asset}): {e}")

    def check_arbitrage(
        self, asset: str, poly_yes: float, poly_no: float, market_title: str = ""
    ) -> CrossArbSignal | None:
        """Polymarket fiyatını Kalshi ile karşılaştır.

        Args:
            asset: "bitcoin", "ethereum", "solana"
            poly_yes: Polymarket YES ask fiyatı
            poly_no: Polymarket NO ask fiyatı
            market_title: market başlığı (matching için)

        Returns:
            CrossArbSignal or None (fırsat yoksa)
        """
        # Cache'te bu asset var mı?
        matching = [
            (k, v) for k, v in self._cache.items()
            if k.startswith(asset) and time.time() - v["timestamp"] < 300
        ]

        if not matching:
            return None

        # En yakın zaman dilimine match et
        best_match = None
        for key, kalshi_data in matching:
            signal = CrossArbSignal(
                poly_yes=poly_yes,
                kalshi_yes=kalshi_data["yes_price"],
                event_name=kalshi_data.get("title", key),
            )

            signal.spread = poly_yes - kalshi_data["yes_price"]
            signal.spread_pct = abs(signal.spread) * 100

            if abs(signal.spread) >= self._min_spread:
                if signal.spread > 0:
                    # Polymarket daha pahalı → Kalshi'den al
                    signal.arb_direction = "BUY_KALSHI"
                else:
                    # Kalshi daha pahalı → Polymarket'ten al
                    signal.arb_direction = "BUY_POLY"

                signal.estimated_profit = abs(signal.spread) * 10  # $10 pozisyon için
                signal.is_actionable = True

                if best_match is None or abs(signal.spread) > abs(best_match.spread):
                    best_match = signal

        if best_match and best_match.is_actionable:
            logger.info(
                f"KALSHI_ARB: {asset} | Poly={best_match.poly_yes:.3f} "
                f"Kalshi={best_match.kalshi_yes:.3f} | "
                f"Spread={best_match.spread:+.3f} ({best_match.spread_pct:.1f}%) | "
                f"Dir={best_match.arb_direction} | Est=${best_match.estimated_profit:.2f}"
            )
            self._arb_signals.append(best_match)
            if len(self._arb_signals) > 200:
                self._arb_signals = self._arb_signals[-100:]

        return best_match

    def get_edge_adjustment(self, asset: str, poly_yes: float) -> float:
        """Kalshi fiyat farkından edge ayarlaması.

        Polymarket Kalshi'den ucuzsa → edge artır (buy on poly).
        Polymarket Kalshi'den pahalıysa → edge azalt (poly overpriced).

        Returns: -0.02 to +0.02
        """
        matching = [
            v for k, v in self._cache.items()
            if k.startswith(asset) and time.time() - v["timestamp"] < 300
        ]

        if not matching:
            return 0.0

        # En güncel Kalshi fiyatı
        kalshi_yes = matching[0]["yes_price"]
        diff = kalshi_yes - poly_yes  # pozitif = poly ucuz (iyi)

        adjustment = max(-0.02, min(0.02, diff * 0.5))
        return adjustment

    def get_opportunities(self) -> list[CrossArbSignal]:
        """Son tespit edilen arbitraj fırsatları."""
        return [s for s in self._arb_signals[-20:] if s.is_actionable]

    def get_stats(self) -> dict:
        """Kalshi arb istatistikleri."""
        recent = self._arb_signals[-50:]
        return {
            "total_opportunities": len([s for s in recent if s.is_actionable]),
            "avg_spread_pct": (
                sum(s.spread_pct for s in recent) / len(recent) if recent else 0
            ),
            "cached_markets": len(self._cache),
            "buy_poly_count": sum(1 for s in recent if s.arb_direction == "BUY_POLY"),
            "buy_kalshi_count": sum(1 for s in recent if s.arb_direction == "BUY_KALSHI"),
        }
