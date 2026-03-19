import os
from loguru import logger
from strategies.base_strategy import BaseStrategy


class KellyCriterion(BaseStrategy):
    """
    Kelly Criterion ile optimal pozisyon boyutu hesaplar.
    Dynamic Kelly: streak-aware multiplier — win streak artırır, loss streak azaltır.
    """

    KELLY_FRACTION_BASE = 0.25   # Adaptive Kelly: base fraction
    KELLY_FRACTION_MAX = 0.50    # Max fraction at full signal strength

    # Dynamic Kelly: streak multipliers
    _STREAK_BOOST_PER_WIN = 0.05    # her ardışık win +5% multiplier
    _STREAK_CUT_PER_LOSS = 0.15     # her ardışık loss -15% multiplier
    _STREAK_MULT_MIN = 0.40         # minimum multiplier (3 loss → 0.55)
    _STREAK_MULT_MAX = 1.30         # maximum multiplier (6 win → 1.30)

    def __init__(self):
        self.max_position_pct = float(os.getenv("MAX_POSITION_PCT", 0.20))
        self.min_edge = float(os.getenv("MIN_EDGE_THRESHOLD", 0.08))
        self._win_streak: int = 0
        self._loss_streak: int = 0
        self._streak_multiplier: float = 1.0

    def update_streak(self, closed_trades: list[dict]):
        """Son kapanışlardan win/loss streak hesapla ve multiplier güncelle.

        Args:
            closed_trades: position_manager.data["closed"] listesi
        """
        win_streak = 0
        loss_streak = 0
        for trade in reversed(closed_trades[-15:]):
            result = trade.get("result", "")
            if result == "WIN":
                if loss_streak == 0:
                    win_streak += 1
                else:
                    break
            elif result == "LOSS":
                if win_streak == 0:
                    loss_streak += 1
                else:
                    break

        self._win_streak = win_streak
        self._loss_streak = loss_streak

        if win_streak > 0:
            self._streak_multiplier = min(
                self._STREAK_MULT_MAX,
                1.0 + win_streak * self._STREAK_BOOST_PER_WIN
            )
        elif loss_streak > 0:
            self._streak_multiplier = max(
                self._STREAK_MULT_MIN,
                1.0 - loss_streak * self._STREAK_CUT_PER_LOSS
            )
        else:
            self._streak_multiplier = 1.0

        if self._streak_multiplier != 1.0:
            logger.info(
                f"DYNAMIC_KELLY: W{win_streak}/L{loss_streak} → "
                f"multiplier={self._streak_multiplier:.2f}"
            )

    def position_size(self, edge: float, price: float, capital: float,
                      signal_strength: float = 0.5) -> float:
        """
        Kelly formülü: f* = (bp - q) / b
        b = kazanç oranı (1/price - 1)
        p = tahmini kazanma olasılığı (price + edge)
        q = 1 - p

        Adaptive: fraction scales 0.25→0.50 based on signal_strength.
        Dynamic: streak multiplier scales size up/down based on recent results.
        """
        if edge <= 0 or price <= 0 or price >= 1:
            return 0.0

        p = price + edge  # AI tahmini
        q = 1 - p
        b = (1 / price) - 1  # Net kazanç oranı

        if b <= 0:
            return 0.0

        # Adaptive Kelly fraction: low confidence = quarter-Kelly, high = half-Kelly
        fraction = self.KELLY_FRACTION_BASE + signal_strength * (self.KELLY_FRACTION_MAX - self.KELLY_FRACTION_BASE)

        kelly_f = (b * p - q) / b
        kelly_f = max(0, kelly_f) * fraction  # Adaptive Kelly

        # Dynamic Kelly: streak multiplier
        kelly_f *= self._streak_multiplier

        # Portföy üst sınırı
        max_size = capital * self.max_position_pct
        size = min(capital * kelly_f, max_size)

        logger.debug(
            f"Kelly: p={p:.2f} b={b:.2f} f={kelly_f:.3f} "
            f"streak_mult={self._streak_multiplier:.2f} → ${size:.2f}"
        )
        return round(size, 2)

    def should_enter(self, edge: float, **kwargs) -> bool:
        return edge >= self.min_edge
