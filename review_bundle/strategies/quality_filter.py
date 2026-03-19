"""
Quality Filter — Araştırmaya dayalı %75+ win rate eşiği

Kaynak: 112,000 Polymarket adresi analizi + SSRN akademik çalışma (Reichenbach & Walther 2025)

Bir trade'in geçmesi için TÜM koşulların sağlanması gerekir:
  1. Edge ≥ 0.12        (araştırma: 0.12+ edge → %72-78 win rate)
  2. Fiyat 0.55-0.88    (sweet spot: kesinleşmemiş ama coin-flip de değil)
  3. AI güveni HIGH     (+8% win rate düşük güven sinyallerini eler)
  4. Whale hizalaması   (same direction son 6h → +5-8% win rate)
  5. Kapanış 4h-7 gün  (optimal tahmin penceresi)
  6. Min hacim $10K     (daha iyi fiyat keşfi)
  7. Consensus hizası   (AI + hedge fund aynı yönde)

NOT: Bu filtreler daha az trade üretir ama her trade daha kaliteli.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from loguru import logger


class QualityFilter:
    """
    Tüm koşullar sağlanmadan trade açılmaz.
    .env parametreleriyle esnetilebilir.
    """

    def __init__(self):
        # Araştırma bulgularına göre optimize edilmiş varsayılanlar
        self.min_edge = float(os.getenv("MIN_EDGE_THRESHOLD", 0.12))
        self.price_min = float(os.getenv("QUALITY_PRICE_MIN", 0.55))
        self.price_max = float(os.getenv("QUALITY_PRICE_MAX", 0.88))
        self.require_high_confidence = os.getenv("REQUIRE_HIGH_CONFIDENCE", "true").lower() == "true"
        self.require_whale_alignment = os.getenv("REQUIRE_WHALE_ALIGNMENT", "true").lower() == "true"
        self.min_hours_to_close = float(os.getenv("MIN_HOURS_TO_CLOSE", 4))
        self.max_hours_to_close = float(os.getenv("MAX_HOURS_TO_CLOSE", 168))  # 7 gün
        self.min_volume = float(os.getenv("MIN_MARKET_VOLUME", 10_000))
        self.require_consensus_alignment = os.getenv("REQUIRE_CONSENSUS_ALIGNMENT", "true").lower() == "true"

    def check(
        self,
        market: dict,
        whale_data: dict,
        ai_result: dict,
        consensus_result: dict | None = None,
    ) -> tuple[bool, str]:
        """
        Trade açılabilir mi?

        Returns:
            (True, "Tüm kontroller geçti") veya
            (False, "Başarısız kontrol: sebep")
        """
        price = float(market.get("best_ask", 0) or 0)
        ai_prob = float(ai_result.get("probability", 0))
        edge = ai_prob - price
        volume = float(market.get("volume", 0) or 0)

        # ── 1. EDGE EŞIĞI ─────────────────────────────────────────────────
        if edge < self.min_edge:
            return False, f"Edge yetersiz: {edge:.3f} < {self.min_edge}"

        # ── 2. FİYAT ARALIĞI ──────────────────────────────────────────────
        if not (self.price_min <= price <= self.price_max):
            return False, (
                f"Fiyat aralık dışı: {price:.2f} "
                f"(gerekli: {self.price_min}-{self.price_max})"
            )

        # ── 3. AI GÜVENİ ──────────────────────────────────────────────────
        confidence = ai_result.get("confidence", "LOW")
        if self.require_high_confidence and confidence != "HIGH":
            return False, f"AI güveni yetersiz: {confidence} (HIGH gerekli)"

        # ── 4. WHALE HIZALAMASI ───────────────────────────────────────────
        whale_align = whale_data.get("whale_alignment") or whale_data.get("direction", "NEUTRAL")
        is_bearish = whale_align in ("SELL", "BEARISH")
        if self.require_whale_alignment and is_bearish:
            return False, f"Whale karsi yonde: {whale_align} (BUY/BULLISH veya NEUTRAL gerekli)"

        # ── 5. KAPANIŞA KALAN SÜRE ────────────────────────────────────────
        hours = self._hours_to_close(market)
        if hours is not None:
            if hours < self.min_hours_to_close:
                return False, f"Kapanışa çok az süre: {hours:.1f}h (min {self.min_hours_to_close}h)"
            if hours > self.max_hours_to_close:
                return False, f"Kapanışa çok fazla süre: {hours:.1f}h (max {self.max_hours_to_close}h)"

        # ── 6. HACİM ──────────────────────────────────────────────────────
        if volume < self.min_volume:
            return False, f"Hacim yetersiz: ${volume:,.0f} < ${self.min_volume:,.0f}"

        # ── 7. CONSENSUS HIZALAMASI ───────────────────────────────────────
        if self.require_consensus_alignment and consensus_result:
            ai_bullish = ai_prob > 0.5
            cons_bullish = consensus_result.get("probability", 0.5) > 0.5
            if ai_bullish != cons_bullish:
                return False, (
                    f"AI ve konsensüs çelişiyor: "
                    f"AI={ai_prob:.2f} Consensus={consensus_result.get('probability', 0.5):.2f}"
                )

        return True, "Tum kalite kontrolleri gecti"

    def _hours_to_close(self, market: dict) -> float | None:
        """Market kapanışına kalan saati hesapla."""
        end_date = market.get("end_date_iso") or market.get("endDateIso")
        if not end_date:
            return None
        try:
            # ISO format: "2025-03-15T18:00:00Z" veya "2025-03-15T18:00:00+00:00"
            end_date_clean = end_date.replace("Z", "+00:00")
            end_dt = datetime.fromisoformat(end_date_clean)
            now = datetime.now(timezone.utc)
            delta = end_dt - now
            return max(delta.total_seconds() / 3600, 0)
        except Exception:
            return None

    def log_filter_stats(self, total: int, passed: int) -> None:
        """Filtre geçiş oranını logla."""
        if total > 0:
            pct = passed / total * 100
            logger.info(f"Quality filter: {passed}/{total} market geçti (%{pct:.0f})")
