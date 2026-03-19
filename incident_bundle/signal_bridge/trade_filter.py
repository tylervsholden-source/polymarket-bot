"""
signal_bridge/trade_filter.py

Son trade gating katmanı — fiyat/likidite/edge filtreleri.

Filtre sırası
-------------
1. Confidence    : signal.confidence < config.min_confidence → LOW_CONFIDENCE
2. Liquidity     : candidate.liquidity < config.min_liquidity → LOW_LIQUIDITY
3. Spread        : side-aware spread > config.max_spread → HIGH_SPREAD
                   YES intent → best_ask_yes - best_bid_yes
                   NO intent  → best_ask_no  - best_bid_no
4. Edge          : confidence - ask_price - fee < config.min_edge_after_fee → NEGATIVE_EDGE

Edge hesabı:
    edge = signal.confidence - ask_price - config.assumed_taker_fee_pct
    ask_price = best_ask_yes (YES) veya best_ask_no (NO)

KALİBRASYON NOTU:
    signal.confidence, modelin ham predict_proba çıktısıdır.
    Bu değer, kalibre edilmiş olay olasılığından farklı olabilir.
    İdeal durumda: calibrated_prob = platt_scale(confidence) veya isotonic regresyon.
    Mevcut implementasyon, confidence'ı kalibre edilmiş olasılık proxy'si olarak
    kullanır. Gerçek canlı performans verisi ile kalibrasyon analizi yapılmalı
    (Phase 5: calibration audit).

Tüm filtreler geçilirse → TradeIntent.mapped_side korunur (YES veya NO).
Herhangi bir filtre başarısız → mapped_side=REJECT, rejection_reason set.

Giriş: route() çıktısı (TradeIntent with mapped_side YES or NO).
       REJECT gelen intent doğrudan döner — yeniden değerlendirilmez.
"""
from __future__ import annotations

from dataclasses import replace

from signal_bridge.bridge_config import BridgeConfig, DEFAULT_CONFIG
from signal_bridge.types import (
    RejectionReason,
    TradeSide,
    TradeIntent,
)


def apply_filters_with_candidate(
    intent: TradeIntent,
    candidate,          # PolymarketCandidate — circular import önlemek için Any
    config: BridgeConfig = DEFAULT_CONFIG,
) -> TradeIntent:
    """
    Nihai trade filtreleri — candidate bilgisi ile.

    Parametreler
    ------------
    intent    : route() çıktısı
    candidate : MarketMatchResult.candidate (PolymarketCandidate)
    config    : BridgeConfig

    Döndürür
    --------
    TradeIntent — filtre sonucuna göre güncellenmiş.

    Spread ölçümü: side-aware.
      YES intent → YES tarafı spread (best_ask_yes - best_bid_yes)
      NO intent  → NO tarafı spread  (best_ask_no  - best_bid_no)
    """
    # Zaten REJECT ise tekrar filtreleme
    if intent.mapped_side == TradeSide.REJECT:
        return intent

    signal    = intent.signal
    ask_price = intent.ask_price

    # 1. Confidence filtresi
    if signal.confidence < config.min_confidence:
        return replace(
            intent,
            mapped_side=TradeSide.REJECT,
            rejection_reason=RejectionReason.LOW_CONFIDENCE,
            pricing_ok=False,
            rationale=(
                f"REJECT: confidence={signal.confidence:.3f} < "
                f"min_confidence={config.min_confidence:.3f}"
            ),
        )

    # 2. Likidite filtresi
    if candidate.liquidity < config.min_liquidity:
        return replace(
            intent,
            mapped_side=TradeSide.REJECT,
            rejection_reason=RejectionReason.LOW_LIQUIDITY,
            pricing_ok=False,
            rationale=(
                f"REJECT: liquidity={candidate.liquidity:.1f} < "
                f"min_liquidity={config.min_liquidity:.1f}"
            ),
        )

    # 3. Spread filtresi — side-aware
    if intent.mapped_side == TradeSide.YES:
        spread = candidate.best_ask_yes - candidate.best_bid_yes
        spread_label = "YES-spread"
    else:
        spread = candidate.best_ask_no - candidate.best_bid_no
        spread_label = "NO-spread"

    if spread > config.max_spread:
        return replace(
            intent,
            mapped_side=TradeSide.REJECT,
            rejection_reason=RejectionReason.HIGH_SPREAD,
            pricing_ok=False,
            rationale=(
                f"REJECT: {spread_label}={spread:.4f} > max_spread={config.max_spread:.4f}"
            ),
        )

    # 4. Edge filtresi
    # NOT: confidence, ham classifier çıktısıdır; kalibre edilmiş olasılık değil.
    # Phase 5 kalibrasyon analizi tamamlanana kadar proxy olarak kullanılır.
    edge = signal.confidence - ask_price - config.assumed_taker_fee_pct
    if edge < config.min_edge_after_fee:
        return replace(
            intent,
            mapped_side=TradeSide.REJECT,
            rejection_reason=RejectionReason.NEGATIVE_EDGE,
            pricing_ok=False,
            expected_edge=edge,
            rationale=(
                f"REJECT: edge={edge:.4f} < "
                f"min_edge_after_fee={config.min_edge_after_fee:.4f} "
                f"(conf={signal.confidence:.3f}, ask={ask_price:.4f}, "
                f"fee={config.assumed_taker_fee_pct:.3f})"
            ),
        )

    # Tüm filtreler geçildi
    return replace(
        intent,
        expected_edge=edge,
        pricing_ok=True,
        rationale=(
            f"{intent.rationale} | edge={edge:.4f} | {spread_label}={spread:.4f} | "
            f"liq={candidate.liquidity:.0f}"
        ),
    )
