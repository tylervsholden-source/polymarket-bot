"""
Candlestick Analyzer — OHLC verilerinden teknik formasyonları tanır.
Tanınan modeller: Doji, Hammer, Engulfing, Morning/Evening Star, vb.
"""
from __future__ import annotations

class CandlestickAnalyzer:
    @staticmethod
    def analyze(klines: list[list]) -> list[str]:
        """
        Klines format: [[timestamp, open, high, low, close, volume], ...]
        Son mumu ve bir önceki mumları analiz eder.
        """
        if len(klines) < 3:
            return []

        patterns = []
        
        # Son 3 mumu al
        c1 = [float(x) for x in klines[-1]] # Güncel mum
        c2 = [float(x) for x in klines[-2]] # Bir önceki mum
        c3 = [float(x) for x in klines[-3]] # İki önceki mum

        # Yardımcı fonksiyonlar
        def is_bullish(c): return c[4] > c[1]
        def is_bearish(c): return c[4] < c[1]
        def body_size(c): return abs(c[4] - c[1])
        def upper_wick(c): return c[2] - max(c[4], c[1])
        def lower_wick(c): return min(c[4], c[1]) - c[3]
        def total_range(c): return c[2] - c[3]

        # 1. DOJI (Kararsızlık)
        # Gövde tüm range'in %10'undan küçükse
        if total_range(c1) > 0 and body_size(c1) <= total_range(c1) * 0.1:
            patterns.append("DOJI")

        # 2. HAMMER (Boğa Dönüşü) - Düşüş trendi sonunda anlamlı
        # Alt fitil gövdenin en az 2 katı, üst fitil çok küçük
        if body_size(c1) > 0 and lower_wick(c1) >= body_size(c1) * 2 and upper_wick(c1) <= body_size(c1) * 0.5:
            patterns.append("HAMMER")

        # 3. SHOOTING STAR (Ayı Dönüşü) - Yükseliş trendi sonunda
        if body_size(c1) > 0 and upper_wick(c1) >= body_size(c1) * 2 and lower_wick(c1) <= body_size(c1) * 0.5:
            patterns.append("SHOOTING_STAR")

        # 4. BULLISH ENGULFING
        if is_bearish(c2) and is_bullish(c1) and c1[4] > c2[1] and c1[1] < c2[4]:
            patterns.append("BULLISH_ENGULFING")

        # 5. BEARISH ENGULFING
        if is_bullish(c2) and is_bearish(c1) and c1[4] < c2[1] and c1[1] > c2[4]:
            patterns.append("BEARISH_ENGULFING")

        # 6. MORNING STAR (Boğa Dönüşü - 3 Mum)
        if is_bearish(c3) and body_size(c2) < body_size(c3) * 0.3 and is_bullish(c1) and c1[4] > (c3[1] + c3[4]) / 2:
            patterns.append("MORNING_STAR")

        # 7. EVENING STAR (Ayı Dönüşü - 3 Mum)
        if is_bullish(c3) and body_size(c2) < body_size(c3) * 0.3 and is_bearish(c1) and c1[4] < (c3[1] + c3[4]) / 2:
            patterns.append("EVENING_STAR")

        # 8. MARUBOZU (Güçlü Momentum)
        if total_range(c1) > 0 and body_size(c1) >= total_range(c1) * 0.9:
            patterns.append("BULLISH_MARUBOZU" if is_bullish(c1) else "BEARISH_MARUBOZU")

        return patterns
