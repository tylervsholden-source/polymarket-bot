"""
signal_bridge/signal_router.py

DirectionalSignal + MarketMatchResult → TradeIntent

Yönlendirme tablosu
-------------------
Direction | Polarity | TradeSide
----------+----------+----------
UP        | NORMAL   | YES
UP        | INVERTED | NO
DOWN      | NORMAL   | NO
DOWN      | INVERTED | YES
NO_TRADE  | *        | REJECT

Eşleşme reddedilmişse (rejection_reason set) → REJECT, rejection_reason korunur.
AMBIGUOUS polarity → REJECT (AMBIGUOUS_WORDING).

Bu dosya yalnızca yönlendirme yapar — fiyat/edge/likidite filtresi trade_filter.py'dedir.
"""
from __future__ import annotations

from signal_bridge.types import (
    Direction,
    MarketMatchResult,
    DirectionalSignal,
    Polarity,
    RejectionReason,
    TradeSide,
    TradeIntent,
)

# Yönlendirme tablosu — tuple key → TradeSide
_ROUTING: dict[tuple[Direction, Polarity], TradeSide] = {
    (Direction.UP,   Polarity.NORMAL):   TradeSide.YES,
    (Direction.UP,   Polarity.INVERTED): TradeSide.NO,
    (Direction.DOWN, Polarity.NORMAL):   TradeSide.NO,
    (Direction.DOWN, Polarity.INVERTED): TradeSide.YES,
}


def route(
    signal: DirectionalSignal,
    match: MarketMatchResult,
) -> TradeIntent:
    """
    Sinyal + eşleşme → TradeIntent üretir.

    Ret senaryoları (mapped_side=REJECT):
    - signal.direction == NO_TRADE
    - match.rejection_reason set (eşleştirme zaten reddedilmiş)
    - match.polarity == AMBIGUOUS

    Başarılı yönlendirme:
    - mapped_side YES veya NO
    - token_id ilgili token ID'si
    - ask_price ilgili tarafın ask fiyatı
    - rationale açıklaması dolu
    """
    candidate = match.candidate

    # 1. NO_TRADE sinyali
    if signal.direction == Direction.NO_TRADE:
        return TradeIntent(
            signal=signal,
            market_id=candidate.market_id,
            mapped_side=TradeSide.REJECT,
            confidence=signal.confidence,
            rationale="Signal direction is NO_TRADE — no position opened.",
            rejection_reason=RejectionReason.NO_TRADE_SIGNAL,
        )

    # 2. Eşleşme reddedilmişse → rejection_reason'ı koru
    if match.rejection_reason is not None:
        return TradeIntent(
            signal=signal,
            market_id=candidate.market_id,
            mapped_side=TradeSide.REJECT,
            confidence=signal.confidence,
            rationale=f"Market match rejected: {match.rejection_reason.value}",
            rejection_reason=match.rejection_reason,
        )

    # 3. AMBIGUOUS polarity
    if match.polarity == Polarity.AMBIGUOUS:
        return TradeIntent(
            signal=signal,
            market_id=candidate.market_id,
            mapped_side=TradeSide.REJECT,
            confidence=signal.confidence,
            rationale="Market polarity is AMBIGUOUS — cannot determine YES/NO side.",
            rejection_reason=RejectionReason.AMBIGUOUS_WORDING,
        )

    # 4. Yönlendirme tablosu
    side = _ROUTING.get((signal.direction, match.polarity))
    if side is None:
        # Teorik olarak buraya düşmemeli ama defensive
        return TradeIntent(
            signal=signal,
            market_id=candidate.market_id,
            mapped_side=TradeSide.REJECT,
            confidence=signal.confidence,
            rationale=(
                f"Unhandled routing: direction={signal.direction.value}, "
                f"polarity={match.polarity.value}"
            ),
            rejection_reason=RejectionReason.AMBIGUOUS_WORDING,
        )

    # 5. Token ve fiyat
    if side == TradeSide.YES:
        token_id  = candidate.yes_token_id
        ask_price = candidate.best_ask_yes
    else:
        token_id  = candidate.no_token_id
        ask_price = candidate.best_ask_no

    rationale = (
        f"{signal.direction.value} + {match.polarity.value} → {side.value} "
        f"| asset={match.matched_asset} "
        f"| tte={match.time_to_resolution_sec}s "
        f"| score={match.match_score:.2f} "
        f"| conf={signal.confidence:.3f}"
    )

    return TradeIntent(
        signal=signal,
        market_id=candidate.market_id,
        mapped_side=side,
        confidence=signal.confidence,
        rationale=rationale,
        rejection_reason=None,
        time_to_resolution_sec=match.time_to_resolution_sec,
        ask_price=ask_price,
        timing_ok=True,
        pricing_ok=True,
        token_id=token_id,
    )
