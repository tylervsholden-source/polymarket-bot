"""
signal_bridge/bridge_config.py

Bridge katmanı konfigürasyonu.
Tüm eşikler burada tanımlanır — hardcoded değer bridge mantığında kabul edilmez.

Varsayılan değerler BridgeConfig dataclass'ında tutulur.
Test ve paper-trading override'ı için constructor parametresi kullanılır.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BridgeConfig:
    """
    Bridge katmanı için tüm eşikler.

    Confidence
    ----------
    min_confidence      : işlem açmak için minimum model güveni (>= değer)
                          model.min_confidence'dan biraz yüksek tutulur

    Likidite / Spread
    -----------------
    min_liquidity       : minimum market likiditesi (USDC)
    max_spread          : maksimum kabul edilebilir bid-ask spread
                          spread = best_ask_yes - best_bid_yes

    Edge
    ----
    min_edge_after_fee  : fee sonrası minimum beklenen edge
                          edge = confidence - ask_price - assumed_taker_fee_pct
    assumed_taker_fee_pct : Polymarket taker fee varsayımı

    Timing Windows (saniye, horizon başına)
    ----------------------------------------
    timing_{H}m_min_sec : resolution için minimum kalan süre
                          Bu süreden kısaysa → TIMING_TOO_CLOSE
    timing_{H}m_max_sec : resolution için maksimum kalan süre
                          Bu süreden uzunsa → TIMING_TOO_FAR

    Mantık: 15m sinyal için ideal market, 5-25 dakika içinde resolve eder.
    Çok yakın → girdi fırsatı yok / spread genişlemiş
    Çok uzak  → sinyalimiz artık geçerli değil

    Asset Eşleştirme
    ----------------
    supported_assets : bridge'in aktif olarak işlem yapacağı assetler
    """

    # Confidence
    min_confidence: float = 0.58

    # Likidite / Spread
    min_liquidity: float = 1_000.0
    max_spread:    float = 0.05

    # Edge
    min_edge_after_fee:    float = 0.02
    assumed_taker_fee_pct: float = 0.01

    # Timing — 5m sinyal
    timing_5m_min_sec: int = 60      # < 1 dakika → çok yakın
    timing_5m_max_sec: int = 600     # > 10 dakika → çok uzak

    # Timing — 15m sinyal
    timing_15m_min_sec: int = 120    # < 2 dakika → çok yakın
    timing_15m_max_sec: int = 1_800  # > 30 dakika → çok uzak

    def timing_window(self, horizon_minutes: int) -> tuple[int, int]:
        """
        Verilen horizon için (min_sec, max_sec) döner.

        Raises ValueError for unsupported horizon.
        """
        if horizon_minutes == 5:
            return (self.timing_5m_min_sec, self.timing_5m_max_sec)
        if horizon_minutes == 15:
            return (self.timing_15m_min_sec, self.timing_15m_max_sec)
        raise ValueError(
            f"BridgeConfig: desteklenmeyen horizon_minutes={horizon_minutes}. "
            f"Desteklenen: 5, 15"
        )


# Singleton — test override'ı için yeni instance oluştur
DEFAULT_CONFIG = BridgeConfig()
