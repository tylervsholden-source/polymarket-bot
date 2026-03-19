"""
Orderbook Depth Analyzer — L2 likidite, spread, imbalance analizi.

CLOB orderbook'tan:
- Spread (bid-ask aralığı)
- Depth imbalance (alım/satım gücü oranı)
- Liquidity score (slippage tahmini)
- Wall detection (büyük emirler)
"""
from __future__ import annotations

from dataclasses import dataclass
from loguru import logger


@dataclass
class OrderbookAnalysis:
    """Orderbook depth analiz sonucu."""
    spread: float = 0.0            # bid-ask spread (0.01 = 1 cent)
    spread_pct: float = 0.0        # spread yüzdesi
    mid_price: float = 0.0         # orta fiyat
    bid_depth: float = 0.0         # toplam bid hacmi ($)
    ask_depth: float = 0.0         # toplam ask hacmi ($)
    imbalance: float = 0.0         # -1.0 (tüm satış) to +1.0 (tüm alış)
    liquidity_score: float = 0.0   # 0-1 arası (1 = çok likit)
    slippage_5: float = 0.0        # $5 emir için tahmini slippage
    slippage_10: float = 0.0       # $10 emir için tahmini slippage
    top_bid_wall: float = 0.0      # en büyük bid emri ($)
    top_ask_wall: float = 0.0      # en büyük ask emri ($)
    bid_levels: int = 0            # kaç farklı fiyat seviyesi
    ask_levels: int = 0
    tradeable: bool = True         # spread çok genişse False


class OrderbookAnalyzer:
    """CLOB L2 orderbook verisi ile derinlik analizi yapar."""

    def __init__(self, max_spread_pct: float = 0.10):
        self.max_spread_pct = max_spread_pct  # spread > %10 ise tradeable=False

    def analyze(self, book) -> OrderbookAnalysis:
        """py-clob-client OrderBook objesini analiz et.

        Args:
            book: clob.get_order_book() dönüşü (asks, bids attributes)

        Returns:
            OrderbookAnalysis dataclass
        """
        result = OrderbookAnalysis()

        if not book:
            result.tradeable = False
            return result

        asks = book.asks if hasattr(book, "asks") else []
        bids = book.bids if hasattr(book, "bids") else []

        if not asks and not bids:
            result.tradeable = False
            return result

        # Parse price/size pairs
        ask_levels = sorted(
            [(float(a.price), float(a.size)) for a in asks],
            key=lambda x: x[0]  # ascending by price
        )
        bid_levels = sorted(
            [(float(b.price), float(b.size)) for b in bids],
            key=lambda x: -x[0]  # descending by price (best bid first)
        )

        result.ask_levels = len(ask_levels)
        result.bid_levels = len(bid_levels)

        if not ask_levels or not bid_levels:
            result.tradeable = False
            return result

        best_ask = ask_levels[0][0]
        best_bid = bid_levels[0][0]

        # Spread
        result.spread = best_ask - best_bid
        result.mid_price = (best_ask + best_bid) / 2
        result.spread_pct = result.spread / result.mid_price if result.mid_price > 0 else 0

        if result.spread_pct > self.max_spread_pct:
            result.tradeable = False

        # Depth — toplam $ hacmi (top 10 level)
        for price, size in bid_levels[:10]:
            notional = price * size
            result.bid_depth += notional
            if notional > result.top_bid_wall:
                result.top_bid_wall = notional

        for price, size in ask_levels[:10]:
            notional = price * size
            result.ask_depth += notional
            if notional > result.top_ask_wall:
                result.top_ask_wall = notional

        # Imbalance: +1 = all bids (bullish), -1 = all asks (bearish)
        total = result.bid_depth + result.ask_depth
        if total > 0:
            result.imbalance = (result.bid_depth - result.ask_depth) / total
        else:
            result.imbalance = 0.0

        # Liquidity score: 0-1 (based on depth and spread)
        # $100+ depth = good, <$10 = bad
        depth_score = min(1.0, total / 100.0)
        spread_score = max(0.0, 1.0 - result.spread_pct * 10)  # 10% spread = 0
        result.liquidity_score = depth_score * 0.6 + spread_score * 0.4

        # Slippage estimation (walk the book)
        result.slippage_5 = self._estimate_slippage(ask_levels, 5.0)
        result.slippage_10 = self._estimate_slippage(ask_levels, 10.0)

        return result

    def _estimate_slippage(self, levels: list[tuple[float, float]], order_size: float) -> float:
        """Walk the ask book to estimate slippage for a given order size.

        Returns: average fill price - best ask (0 = no slippage)
        """
        if not levels:
            return 0.0

        best_price = levels[0][0]
        remaining = order_size
        total_cost = 0.0

        for price, size in levels:
            available = price * size  # $ available at this level
            fill = min(remaining, available)
            total_cost += fill  # at this price level
            remaining -= fill
            if remaining <= 0:
                break

        if order_size <= 0:
            return 0.0

        avg_price = total_cost / (order_size - remaining) if (order_size - remaining) > 0 else best_price
        return max(0.0, avg_price - best_price)

    def get_signal_boost(self, analysis: OrderbookAnalysis) -> float:
        """Orderbook analizinden sinyal boost'u hesapla.

        Returns: -0.03 to +0.03 arası boost
        - Pozitif imbalance (daha çok alım) = YES boost
        - Negatif imbalance (daha çok satım) = NO boost
        """
        if not analysis.tradeable or analysis.liquidity_score < 0.2:
            return 0.0

        # Imbalance-based boost, liquidity-weighted
        boost = analysis.imbalance * 0.03 * analysis.liquidity_score
        return max(-0.03, min(0.03, boost))
