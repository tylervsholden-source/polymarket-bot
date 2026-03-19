"""
YES+NO Sum Monitor — Arbitraj fırsat tespiti.

YES_ask + NO_ask normalde ~$1.00 olmalı.
- < $0.98 → arbitraj fırsatı (ikisini de al, kapanışta $1 al)
- > $1.02 → aşırı fiyatlanmış (bekle, düzeltme gelecek)
- Sapma büyükse → market maker yok, likidite sorunu
"""
from __future__ import annotations

from dataclasses import dataclass
from loguru import logger


@dataclass
class SumAnalysis:
    """YES+NO fiyat toplamı analiz sonucu."""
    yes_ask: float = 0.0
    no_ask: float = 0.0
    total: float = 0.0
    deviation: float = 0.0        # 1.0'dan sapma (negatif = arbitraj)
    deviation_pct: float = 0.0
    arbitrage_opportunity: bool = False   # ikisini de alarak garanti kar
    overpriced: bool = False              # toplam > 1.02
    market_efficient: bool = True         # sapma < 0.02
    estimated_profit_pct: float = 0.0     # arbitraj yapılırsa tahmini kar %


class SumMonitor:
    """Market YES+NO fiyat toplamını izler, sapma tespit eder."""

    def __init__(self, arb_threshold: float = 0.02, overprice_threshold: float = 0.02):
        self.arb_threshold = arb_threshold        # <0.98 = arb
        self.overprice_threshold = overprice_threshold  # >1.02 = overpriced
        self._history: list[SumAnalysis] = []

    def analyze(self, yes_ask: float, no_ask: float, market_id: str = "") -> SumAnalysis:
        """YES ve NO ask fiyatlarını analiz et.

        Args:
            yes_ask: YES token best ask fiyatı
            no_ask: NO token best ask fiyatı (gerçek orderbook'tan)

        Returns:
            SumAnalysis
        """
        result = SumAnalysis(
            yes_ask=yes_ask,
            no_ask=no_ask,
            total=yes_ask + no_ask,
        )

        result.deviation = result.total - 1.0
        result.deviation_pct = result.deviation * 100

        if result.total < (1.0 - self.arb_threshold):
            result.arbitrage_opportunity = True
            result.market_efficient = False
            # Kar: $1 - toplam maliyet
            result.estimated_profit_pct = (1.0 - result.total) / result.total * 100
            logger.info(
                f"ARB_OPPORTUNITY: {market_id[:40]} | "
                f"YES={yes_ask:.3f} + NO={no_ask:.3f} = {result.total:.3f} | "
                f"Est.Profit={result.estimated_profit_pct:.1f}%"
            )

        elif result.total > (1.0 + self.overprice_threshold):
            result.overpriced = True
            result.market_efficient = False
            logger.info(
                f"OVERPRICED: {market_id[:40]} | "
                f"YES={yes_ask:.3f} + NO={no_ask:.3f} = {result.total:.3f} | "
                f"Sapma={result.deviation_pct:+.1f}%"
            )

        self._history.append(result)
        if len(self._history) > 500:
            self._history = self._history[-200:]

        return result

    def get_edge_adjustment(self, analysis: SumAnalysis) -> float:
        """Sum analizinden edge ayarlaması hesapla.

        Returns:
            Negatif sapma = piyasa ucuz, edge artır
            Pozitif sapma = piyasa pahalı, edge azalt
        """
        if analysis.market_efficient:
            return 0.0

        # Toplam < 1.0 → fiyatlar ucuz → daha fazla edge var
        # Toplam > 1.0 → fiyatlar pahalı → edge azalır
        return -analysis.deviation * 0.5  # yarı etki

    def get_arb_opportunities(self) -> list[SumAnalysis]:
        """Son tespit edilen arbitraj fırsatlarını döndür."""
        return [a for a in self._history[-50:] if a.arbitrage_opportunity]

    def get_stats(self) -> dict:
        """İstatistikler."""
        if not self._history:
            return {"avg_sum": 1.0, "arb_count": 0, "overprice_count": 0}

        recent = self._history[-100:]
        return {
            "avg_sum": sum(a.total for a in recent) / len(recent),
            "min_sum": min(a.total for a in recent),
            "max_sum": max(a.total for a in recent),
            "arb_count": sum(1 for a in recent if a.arbitrage_opportunity),
            "overprice_count": sum(1 for a in recent if a.overpriced),
            "efficient_pct": sum(1 for a in recent if a.market_efficient) / len(recent) * 100,
        }
