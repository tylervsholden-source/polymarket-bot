"""
signal_bridge/market_matcher.py

Crypto directional sinyalini Polymarket market adaylarıyla eşleştirir.

Desteklenen market tipleri
--------------------------
1. "X Up or Down" formatı (birincil):
   Örnek: "Bitcoin Up or Down - March 14, 12:30PM-12:45PM ET"
   Polarity: daima NORMAL (YES = fiyat yükseldi, NO = fiyat düştü)
   Asset: market başlığındaki ilk kelime ("Bitcoin" → BTC)

2. Açık yön marketleri (ikincil, basit keyword tespiti):
   "above" / "higher" / "increase" → NORMAL
   "below" / "fall" / "drop"       → INVERTED

Reddedilen durumlar
-------------------
- Market aktif değil (status != "active")
- Asset eşleşmiyor (BTC için Ethereum marketi gibi)
- Aynı anda birden fazla farklı asset içeriyor (ambiguous)
- Wording NORMAL/INVERTED olarak sınıflandırılamıyor
- Time-to-resolution config timing window dışında

Filtre sırası
-------------
1. Aktif mi?
2. Asset eşleşiyor mu?
3. Time-to-resolution window içinde mi?
4. Polarity tespit edilebiliyor mu?
→ Geçen marketler match_score'a göre sıralanır (iyi timing = yüksek skor)
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from signal_bridge.bridge_config import BridgeConfig, DEFAULT_CONFIG
from signal_bridge.types import (
    DirectionalSignal,
    MarketMatchResult,
    Polarity,
    PolymarketCandidate,
    RejectionReason,
)

# ── Asset sözlüğü ─────────────────────────────────────────────────────────────

_ASSET_KEYWORDS: dict[str, list[str]] = {
    "BTC": ["bitcoin", "btc"],
    "ETH": ["ethereum", "eth", "ether"],
    "SOL": ["solana", "sol"],
    "XRP": ["ripple", "xrp"],
    "DOGE": ["dogecoin", "doge"],
    "BNB": ["bnb", "binancecoin"],
    "HYPE": ["hyperliquid", "hype"],
}

# ── Wording pattern'leri ──────────────────────────────────────────────────────

# "Up or Down" → daima NORMAL
_UP_OR_DOWN_RE = re.compile(r'\bup\s+or\s+down\b', re.IGNORECASE)

# NORMAL polarity göstergeleri (UP → YES)
_NORMAL_PATTERNS = [
    r'\babove\b', r'\bhigher\b', r'\bincrease[sd]?\b',
    r'\brise[sd]?\b', r'\bgoes?\s+up\b', r'\bgain[sd]?\b',
    r'\bexceed[sd]?\b',
]

# INVERTED polarity göstergeleri (UP → NO, DOWN → YES)
_INVERTED_PATTERNS = [
    r'\bbelow\b', r'\blower\b', r'\bdecrease[sd]?\b',
    r'\bfall[sd]?\b', r'\bfalling\b',
    r'\bdrop[ps]?\b', r'\bdecline[sd]?\b',
    r'\bgoes?\s+down\b',
]


# ── Public API ────────────────────────────────────────────────────────────────

def match_markets(
    signal: DirectionalSignal,
    candidates: list[PolymarketCandidate],
    now_utc: Optional[datetime] = None,
    config: BridgeConfig = DEFAULT_CONFIG,
) -> list[MarketMatchResult]:
    """
    Sinyale uygun market adaylarını değerlendirir.

    Parametreler
    ------------
    signal     : crypto_directional sinyali
    candidates : Polymarket market listesi (polymarket_bot'tan)
    now_utc    : geçerli zaman (None → datetime.now(UTC))
    config     : BridgeConfig instance (test override için)

    Döndürür
    --------
    MarketMatchResult listesi:
    - Geçerliler önce, match_score azalan sırada
    - Reddedilenler arkada (rejection_reason dolu)
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    results = [_evaluate(signal, c, now_utc, config) for c in candidates]

    valid    = sorted(
        [r for r in results if r.rejection_reason is None],
        key=lambda r: r.match_score,
        reverse=True,
    )
    rejected = [r for r in results if r.rejection_reason is not None]
    return valid + rejected


def best_match(
    signal: DirectionalSignal,
    candidates: list[PolymarketCandidate],
    now_utc: Optional[datetime] = None,
    config: BridgeConfig = DEFAULT_CONFIG,
) -> Optional[MarketMatchResult]:
    """
    En iyi geçerli eşleşmeyi döner; geçerli yoksa None.
    """
    results = match_markets(signal, candidates, now_utc, config)
    valid = [r for r in results if r.rejection_reason is None]
    return valid[0] if valid else None


# ── Tek market değerlendirme ──────────────────────────────────────────────────

def _evaluate(
    signal: DirectionalSignal,
    candidate: PolymarketCandidate,
    now_utc: datetime,
    config: BridgeConfig,
) -> MarketMatchResult:
    def _reject(reason: RejectionReason) -> MarketMatchResult:
        return MarketMatchResult(
            candidate=candidate,
            matched_asset="",
            polarity=Polarity.AMBIGUOUS,
            time_to_resolution_sec=0,
            match_score=0.0,
            rejection_reason=reason,
        )

    # 1. Aktif mi?
    if candidate.status != "active":
        return _reject(RejectionReason.MARKET_INACTIVE)

    # 2. Asset eşleşmesi
    text = (candidate.title + " " + candidate.description).lower()
    matched_asset = _match_asset(text, signal.asset)
    if matched_asset is None:
        return _reject(RejectionReason.ASSET_MISMATCH)

    # 3. Time-to-resolution
    tte = _time_to_resolution_sec(candidate.end_time_utc, now_utc)
    timing_rejection = _check_timing(tte, signal.horizon_minutes, config)
    if timing_rejection is not None:
        return MarketMatchResult(
            candidate=candidate,
            matched_asset=matched_asset,
            polarity=Polarity.AMBIGUOUS,
            time_to_resolution_sec=tte,
            match_score=0.0,
            rejection_reason=timing_rejection,
        )

    # 4. Polarity tespiti
    polarity = _detect_polarity(candidate.title)
    if polarity == Polarity.AMBIGUOUS:
        return MarketMatchResult(
            candidate=candidate,
            matched_asset=matched_asset,
            polarity=Polarity.AMBIGUOUS,
            time_to_resolution_sec=tte,
            match_score=0.0,
            rejection_reason=RejectionReason.AMBIGUOUS_WORDING,
        )

    score = _timing_score(tte, signal.horizon_minutes, config)

    return MarketMatchResult(
        candidate=candidate,
        matched_asset=matched_asset,
        polarity=polarity,
        time_to_resolution_sec=tte,
        match_score=score,
        rejection_reason=None,
    )


# ── Yardımcı fonksiyonlar ─────────────────────────────────────────────────────

def _kw_found(text: str, keyword: str) -> bool:
    """Word-boundary korumalı keyword arama (substring false positive önler)."""
    return bool(re.search(r'\b' + re.escape(keyword) + r'\b', text))


def _match_asset(text: str, signal_asset: str) -> Optional[str]:
    """
    Verilen text'te sinyal asset'ini arar.

    Kurallar:
    - Signal asset'inin keyword'leri text'te bulunmalı (word boundary ile)
    - Başka bir asset keyword'ü de varsa → None (ambiguous)
    - Hiçbir keyword yoksa → None (mismatch)

    Word boundary: 'eth' → \beth\b  → 'ethereum' içindeki 'eth'i yakalamaz,
    yalnızca bağımsız token olarak eşleşir.
    """
    target_kws = _ASSET_KEYWORDS.get(signal_asset.upper(), [signal_asset.lower()])
    if not any(_kw_found(text, kw) for kw in target_kws):
        return None  # sinyal asset'i bulunamadı

    for other_asset, other_kws in _ASSET_KEYWORDS.items():
        if other_asset == signal_asset.upper():
            continue
        if any(_kw_found(text, kw) for kw in other_kws):
            return None  # başka asset de var → ambiguous

    return signal_asset.upper()


def _detect_polarity(title: str) -> Polarity:
    """
    Market başlığından polarity tespit eder.

    Öncelik sırası:
    1. "Up or Down" → NORMAL (kesin)
    2. Sadece NORMAL keyword'leri → NORMAL
    3. Sadece INVERTED keyword'leri → INVERTED
    4. Her ikisi veya hiçbiri → AMBIGUOUS
    """
    # "Up or Down" formatı — en kesin sinyal
    if _UP_OR_DOWN_RE.search(title):
        return Polarity.NORMAL

    lower = title.lower()
    has_normal   = any(re.search(p, lower) for p in _NORMAL_PATTERNS)
    has_inverted = any(re.search(p, lower) for p in _INVERTED_PATTERNS)

    if has_normal and not has_inverted:
        return Polarity.NORMAL
    if has_inverted and not has_normal:
        return Polarity.INVERTED
    return Polarity.AMBIGUOUS


def _time_to_resolution_sec(end_time: datetime, now: datetime) -> int:
    """Saniye cinsinden kalan süre (negatif → resolution geçmiş)."""
    # Timezone-naive datetime'ları UTC'ye çevir
    if end_time.tzinfo is None:
        end_time = end_time.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0, int((end_time - now).total_seconds()))


def _check_timing(
    tte_sec: int,
    horizon_minutes: int,
    config: BridgeConfig,
) -> Optional[RejectionReason]:
    """
    Time-to-resolution config window içinde mi kontrol eder.
    None → geçerli; RejectionReason → reddedildi.
    """
    try:
        min_sec, max_sec = config.timing_window(horizon_minutes)
    except ValueError:
        return RejectionReason.UNSUPPORTED_HORIZON  # bilinmeyen horizon → sessiz atla değil, reddet

    if tte_sec < min_sec:
        return RejectionReason.TIMING_TOO_CLOSE
    if tte_sec > max_sec:
        return RejectionReason.TIMING_TOO_FAR
    return None


def _timing_score(
    tte_sec: int,
    horizon_minutes: int,
    config: BridgeConfig,
) -> float:
    """
    Timing kalitesi skoru: 0.0..1.0
    Window'un orta noktasına en yakın → 1.0
    Kenara yakın → 0.0'a yakın
    """
    try:
        min_sec, max_sec = config.timing_window(horizon_minutes)
    except ValueError:
        return 0.5

    midpoint   = (min_sec + max_sec) / 2.0
    half_range = (max_sec - min_sec) / 2.0
    if half_range == 0:
        return 1.0
    distance = abs(tte_sec - midpoint) / half_range
    return float(max(0.0, 1.0 - distance))
