"""
Moonshot Stratejisi — planktonXD modeli

Mantık:
  - Piyasada 0.01-0.06 arası fiyatlı (çok düşük olasılık) marketleri tara
  - Her birine sabit küçük miktar yatır ($1-5)
  - %99'u sıfıra gider, 1-2 tanesi 20x-100x yapar
  - Kelly değil — flat bet sizing (çünkü Kelly çok düşük fiyatlarda ridiculous büyük pozisyon önerir)

Hedef: Ana stratejinin yanında küçük "lottery ticket" portföyü tut
"""
from __future__ import annotations

import os
from loguru import logger


class MoonshotStrategy:
    """
    Moonshot filtreleme ve pozisyon boyutlandırma.

    Parametreler (.env):
      MOONSHOT_MAX_PRICE      = 0.06   # Bu fiyatın altındaki marketler moonshot adayı
      MOONSHOT_MIN_PRICE      = 0.01   # Çok düşük fiyatları da ele (likidite sorunu olabilir)
      MOONSHOT_BET_SIZE       = 3      # Her pozisyon için sabit $ miktarı
      MOONSHOT_MAX_POSITIONS  = 25     # Günlük max moonshot pozisyon sayısı
      MOONSHOT_BUDGET_PCT     = 0.15   # Toplam sermayenin moonshot'a ayrılan %'si
      MOONSHOT_MIN_VOLUME     = 500    # Min hacim (düşük likidite tuzağından kaçın)
    """

    def __init__(self):
        self.max_price = float(os.getenv("MOONSHOT_MAX_PRICE", 0.06))
        self.min_price = float(os.getenv("MOONSHOT_MIN_PRICE", 0.01))
        self.bet_size = float(os.getenv("MOONSHOT_BET_SIZE", 3))
        self.max_positions = int(os.getenv("MOONSHOT_MAX_POSITIONS", 25))
        self.budget_pct = float(os.getenv("MOONSHOT_BUDGET_PCT", 0.15))
        self.min_volume = float(os.getenv("MOONSHOT_MIN_VOLUME", 500))

    def is_moonshot_candidate(self, market: dict) -> bool:
        """Market moonshot kriterlerini karşılıyor mu?"""
        price = float(market.get("best_ask", 1.0) or 1.0)
        volume = float(market.get("volume", 0) or 0)

        if not (self.min_price <= price <= self.max_price):
            return False
        if volume < self.min_volume:
            return False
        return True

    def position_size(self, capital: float) -> float:
        """
        Moonshot için sabit küçük bet — Kelly kullanma.
        Bütçe sınırı: toplam capital'in MOONSHOT_BUDGET_PCT'si kadar moonshot'a ayrılır.
        Ama tek pozisyon her zaman MOONSHOT_BET_SIZE kadar.
        """
        max_budget = capital * self.budget_pct
        if self.bet_size > max_budget:
            logger.debug(f"Moonshot bütçe aşımı ({max_budget:.1f}$ kaldı). Geçildi.")
            return 0.0
        return self.bet_size

    def expected_value_ok(self, ai_nonzero_prob: float, price: float) -> bool:
        """
        AI bu olayın gerçekleşme ihtimalinin sıfırdan büyük olduğunu düşünüyor mu?
        EV = (1/price - 1) * ai_prob - (1 - ai_prob)
        Moonshot için eşik çok düşük: EV > 0 yeterli
        """
        if price <= 0:
            return False
        payout_ratio = (1.0 / price) - 1.0
        ev = payout_ratio * ai_nonzero_prob - (1.0 - ai_nonzero_prob)
        logger.debug(f"Moonshot EV: {ev:.2f} (payout={payout_ratio:.0f}x, prob={ai_nonzero_prob:.3f})")
        return ev > 0

    def log_summary(self, open_moonshots: int, capital: float) -> None:
        budget_used = open_moonshots * self.bet_size
        budget_total = capital * self.budget_pct
        logger.info(
            f"Moonshot: {open_moonshots}/{self.max_positions} pozisyon | "
            f"${budget_used:.0f}/${budget_total:.0f} bütçe"
        )
