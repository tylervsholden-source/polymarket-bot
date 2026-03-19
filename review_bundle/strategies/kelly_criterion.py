import os
from loguru import logger
from strategies.base_strategy import BaseStrategy


class KellyCriterion(BaseStrategy):
    """
    Kelly Criterion ile optimal pozisyon boyutu hesaplar.
    Aşırı risk almayı önlemek için yarı-Kelly (0.5x) kullanılır.
    """

    KELLY_FRACTION = 0.5  # Tam Kelly yerine yarı-Kelly

    def __init__(self):
        self.max_position_pct = float(os.getenv("MAX_POSITION_PCT", 0.20))
        self.min_edge = float(os.getenv("MIN_EDGE_THRESHOLD", 0.05))

    def position_size(self, edge: float, price: float, capital: float) -> float:
        """
        Kelly formülü: f* = (bp - q) / b
        b = kazanç oranı (1/price - 1)
        p = tahmini kazanma olasılığı (price + edge)
        q = 1 - p
        """
        if edge <= 0 or price <= 0 or price >= 1:
            return 0.0

        p = price + edge  # AI tahmini
        q = 1 - p
        b = (1 / price) - 1  # Net kazanç oranı

        if b <= 0:
            return 0.0

        kelly_f = (b * p - q) / b
        kelly_f = max(0, kelly_f) * self.KELLY_FRACTION  # Yarı-Kelly

        # Portföy üst sınırı
        max_size = capital * self.max_position_pct
        size = min(capital * kelly_f, max_size)

        logger.debug(f"Kelly: p={p:.2f} b={b:.2f} f={kelly_f:.3f} → ${size:.2f}")
        return round(size, 2)

    def should_enter(self, edge: float, **kwargs) -> bool:
        return edge >= self.min_edge
