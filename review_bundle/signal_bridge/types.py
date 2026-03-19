"""
signal_bridge/types.py

Bridge katmanının veri modelleri.

Üç taraf arasında akan veri:
    crypto_directional  →  DirectionalSignal
    polymarket_bot      →  PolymarketCandidate
    bridge output       →  MarketMatchResult → TradeIntent
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from calibration.types import SUPPORTED_HORIZONS


class Direction(str, Enum):
    UP       = "UP"
    DOWN     = "DOWN"
    NO_TRADE = "NO_TRADE"


class TradeSide(str, Enum):
    YES    = "YES"
    NO     = "NO"
    REJECT = "REJECT"


class Polarity(str, Enum):
    """
    Market wording'inin yön yorumu.

    NORMAL  : UP → YES, DOWN → NO
              Örnek: "Will BTC be above X?"  /  "Bitcoin Up or Down"
    INVERTED: UP → NO,  DOWN → YES
              Örnek: "Will BTC fall below X?"
    AMBIGUOUS: Tespit edilemeyen — REJECT
    """
    NORMAL    = "NORMAL"
    INVERTED  = "INVERTED"
    AMBIGUOUS = "AMBIGUOUS"


class RejectionReason(str, Enum):
    NO_TRADE_SIGNAL     = "NO_TRADE_SIGNAL"      # signal.direction == NO_TRADE
    ASSET_MISMATCH      = "ASSET_MISMATCH"        # market asset != signal asset
    AMBIGUOUS_WORDING   = "AMBIGUOUS_WORDING"     # wording parse edilemedi
    TIMING_TOO_CLOSE      = "TIMING_TOO_CLOSE"      # resolution çok yakın
    TIMING_TOO_FAR        = "TIMING_TOO_FAR"        # resolution çok uzak
    UNSUPPORTED_HORIZON   = "UNSUPPORTED_HORIZON"   # horizon_minutes desteklenmiyor (5/15 dışı)
    MARKET_INACTIVE     = "MARKET_INACTIVE"       # market active değil
    LOW_CONFIDENCE      = "LOW_CONFIDENCE"        # model güveni yetersiz
    LOW_LIQUIDITY       = "LOW_LIQUIDITY"         # piyasa likiditesi yetersiz
    HIGH_SPREAD         = "HIGH_SPREAD"           # bid-ask spreadi fazla
    NEGATIVE_EDGE       = "NEGATIVE_EDGE"         # fee sonrası edge negatif
    NO_CANDIDATE_MARKETS = "NO_CANDIDATE_MARKETS" # uygun market yok


@dataclass
class DirectionalSignal:
    """
    crypto_directional modülünün bridge'e gönderdiği sinyal.

    Alanlar
    -------
    asset           : "BTC" | "ETH" — sinyal üretilen asset
    horizon_minutes : 5 | 15 — sinyal horizon'u (dakika)
    timestamp_utc   : sinyal üretim zamanı (UTC aware)
    direction       : UP / DOWN / NO_TRADE
    confidence      : 0.0..1.0 — model çıkış skoru (predict_proba max)
    model_version   : izlenebilirlik için model versiyonu
    expected_move_pct : (opsiyonel) beklenen fiyat hareketi yüzdesi
    volatility_regime : (opsiyonel) 0=low, 1=mid, 2=high
    """
    asset:             str
    horizon_minutes:   int
    timestamp_utc:     datetime
    direction:         Direction
    confidence:        float
    model_version:     str            = "v0"
    expected_move_pct: Optional[float] = None
    volatility_regime: Optional[int]   = None

    def __post_init__(self) -> None:
        if self.horizon_minutes not in SUPPORTED_HORIZONS:
            raise ValueError(
                f"DirectionalSignal.horizon_minutes={self.horizon_minutes} is not supported; "
                f"must be one of {sorted(SUPPORTED_HORIZONS)}"
            )


@dataclass
class PolymarketCandidate:
    """
    Polymarket'ten çekilen market adayı.
    polymarket_bot.get_active_markets() çıktısından doldurulur.

    Alanlar
    -------
    market_id     : Polymarket market ID
    title         : market soru metni (eşleştirme için kritik)
    description   : ek açıklama (asset çakışması kontrolü için)
    end_time_utc  : resolution zamanı (UTC aware)
    yes_token_id  : YES token CLOB ID
    no_token_id   : NO token CLOB ID
    best_bid_yes  : YES tarafı en iyi alış fiyatı
    best_ask_yes  : YES tarafı en iyi satış fiyatı (alım için ödenen)
    best_bid_no   : NO tarafı en iyi alış fiyatı
    best_ask_no   : NO tarafı en iyi satış fiyatı
    volume        : toplam hacim (USDC)
    liquidity     : anlık likidite (USDC)
    status        : "active" | "closed" | "resolved"
    """
    market_id:    str
    title:        str
    description:  str
    end_time_utc: datetime
    yes_token_id: str
    no_token_id:  str
    best_bid_yes: float
    best_ask_yes: float
    best_bid_no:  float
    best_ask_no:  float
    volume:       float
    liquidity:    float
    status:       str


@dataclass
class MarketMatchResult:
    """
    Tek bir market adayının sinyal ile eşleştirme sonucu.

    rejection_reason is None  → eşleşme geçerli
    rejection_reason set      → eşleşme reddedildi
    """
    candidate:               PolymarketCandidate
    matched_asset:           str           # eşleşen asset ("BTC", "ETH", ...)
    polarity:                Polarity
    time_to_resolution_sec:  int           # saniye cinsinden kalan süre
    match_score:             float         # 0.0..1.0 — yüksek = daha iyi timing
    rejection_reason:        Optional[RejectionReason] = None


@dataclass
class TradeIntent:
    """
    Bridge çıktısı: polymarket_bot'a iletilen ticaret niyeti.

    mapped_side == REJECT → işlem açılmaz; rejection_reason dolu
    mapped_side == YES/NO → işlem açılabilir; tüm filtreler geçildi
    """
    signal:                  DirectionalSignal
    market_id:               str
    mapped_side:             TradeSide
    confidence:              float
    rationale:               str
    rejection_reason:        Optional[RejectionReason] = None
    time_to_resolution_sec:  int           = 0
    ask_price:               float         = 0.0
    expected_edge:           Optional[float] = None
    timing_ok:               bool          = True
    pricing_ok:              bool          = True
    # Hangi token alınacak (polymarket_bot kullanır)
    token_id:                str           = ""
