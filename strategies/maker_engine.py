"""
Market Making Engine — Two-sided passive quoting for crypto up/down markets.

Strategy: Place GTC limit orders on BOTH YES and NO sides of a market.
Profit from the bid-ask spread when both sides fill.
Uses Stoikov model for optimal quote placement with inventory management.

Key differences from directional trading:
- NO price bump (passive, not aggressive)
- NO 45-second fill wait (fire-and-forget)
- Quotes refreshed every cycle (cancel old → place new)
- Inventory-aware: overweight side gets reduced allocation
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from loguru import logger

from strategies.stoikov import StoikovExecutor


@dataclass
class StandingOrder:
    """Tracks a single passive order on the book."""
    order_id: str
    market_id: str
    token_id: str
    side: str          # "YES" or "NO"
    price: float
    size: float
    placed_at: float   # epoch


@dataclass
class MarketInventory:
    """Tracks filled inventory per market."""
    yes_shares: float = 0.0
    no_shares: float = 0.0
    yes_cost: float = 0.0
    no_cost: float = 0.0


class MakerEngine:
    """Two-sided quote manager for crypto up/down markets."""

    # ── Configuration ──
    MAX_MARKETS = 3              # max markets to quote simultaneously
    MAX_INVENTORY_PER_SIDE = 15  # max $ per side per market
    MIN_SPREAD_PCT = 0.03        # don't quote if spread < 3% (no room)
    MAX_SPREAD_PCT = 0.40        # don't quote if spread > 40% (illiquid)
    QUOTE_PRICE_MIN = 0.10       # don't bid below 0.10
    QUOTE_PRICE_MAX = 0.90       # don't bid above 0.90
    VOLATILITY_DEFAULT = 0.02    # default 2% volatility for 5m markets

    def __init__(
        self,
        stoikov: StoikovExecutor | None = None,
        gamma: float = 0.15,
    ):
        self.stoikov = stoikov or StoikovExecutor(gamma=gamma)
        self._standing: dict[str, StandingOrder] = {}   # order_id → StandingOrder
        self._inventory: dict[str, MarketInventory] = {}  # market_id → inventory
        self._last_refresh: float = 0.0

    # ── Public API ──

    async def refresh_quotes(
        self,
        markets: list[dict],
        capital: float,
        client,  # PolymarketClient
    ) -> dict:
        """Cancel all stale orders, compute new quotes, place new orders.

        Returns: {"placed": int, "cancelled": int, "skipped": int}
        """
        stats = {"placed": 0, "cancelled": 0, "skipped": 0}

        # Step 1: Cancel all existing standing orders
        cancelled = await self._cancel_all_standing(client)
        stats["cancelled"] = cancelled

        # Step 2: Check which old orders filled (detect fills)
        # This is done by checking if order disappeared from CLOB without cancel
        # For now, fills are detected by position_manager externally

        # Step 3: Select best markets to quote
        candidates = self._select_markets(markets, capital)

        if not candidates:
            logger.info(f"MAKER: No suitable markets (capital=${capital:.2f})")
            return stats

        # Step 4: Allocate capital per market
        per_market = min(capital / len(candidates), self.MAX_INVENTORY_PER_SIDE * 2)

        # Step 5: Compute and place quotes
        for market in candidates:
            result = await self._quote_market(market, per_market / 2, client)
            if result:
                stats["placed"] += result
            else:
                stats["skipped"] += 1

        self._last_refresh = time.time()
        logger.info(
            f"MAKER_REFRESH: placed={stats['placed']} cancelled={stats['cancelled']} "
            f"skipped={stats['skipped']} markets={len(candidates)} capital=${capital:.2f}"
        )
        return stats

    def get_standing_orders(self) -> dict[str, StandingOrder]:
        """Return current standing orders for external tracking."""
        return dict(self._standing)

    def get_total_locked(self) -> float:
        """Total capital locked in standing (unfilled) orders."""
        return sum(o.price * o.size for o in self._standing.values())

    def get_committed_capital(self) -> float:
        """Real USDC currently at risk: standing-order notional plus the
        cost basis of filled-but-unresolved inventory.

        pool_available("maker") only knows about PositionManager's shared
        ledger, which maker fills never enter (on_fill() only updates
        self._inventory in-memory) — so it can never reflect capital this
        engine has already committed to real orders. Callers must subtract
        this from pool_available("maker") before handing capital to
        refresh_quotes(), or the same capital gets re-committed every cycle.
        """
        standing = self.get_total_locked()
        filled = sum(inv.yes_cost + inv.no_cost for inv in self._inventory.values())
        return standing + filled

    # ── Internal ──

    # Crypto keywords — coin harici market YASAK
    CRYPTO_KW = [
        "bitcoin", "ethereum", "solana", "btc", "eth", "sol", "xrp",
        "dogecoin", "doge", "bnb", "hyperliquid", "hype",
    ]

    # Only 5m and 15m windows — fast resolution, wider spreads
    SHORT_WINDOWS = ["5pm", "10pm", "15pm", "20pm", "25pm", "30pm", "35pm", "40pm", "45pm", "50pm", "55pm", "00pm"]

    def _is_short_window(self, question: str) -> bool:
        """Only allow 5-minute and 15-minute markets (not 4h/8h/daily)."""
        q = question.lower()
        # 4h/8h/daily markets have wide time ranges like "4:00PM-8:00PM" or "March 25"
        # 5m markets: "4:30PM-4:35PM", 15m: "4:30PM-4:45PM"
        import re
        # Match time range pattern: H:MMPM-H:MMPM
        match = re.search(r'(\d+):(\d+)(am|pm)\s*-\s*(\d+):(\d+)(am|pm)', q)
        if not match:
            return False
        h1, m1 = int(match.group(1)), int(match.group(2))
        h2, m2 = int(match.group(4)), int(match.group(5))
        # Convert to minutes
        t1 = h1 * 60 + m1
        t2 = h2 * 60 + m2
        duration = t2 - t1
        # Allow 5min and 15min windows only
        return duration in (5, 15)

    def _is_crypto_market(self, question: str) -> bool:
        """Only allow crypto up/down markets with 5m/15m windows."""
        q = question.lower()
        is_crypto = any(kw in q for kw in self.CRYPTO_KW) and "up or down" in q
        return is_crypto and self._is_short_window(question)

    def _minutes_to_close(self, question: str) -> float | None:
        """Market kapanışına kaç dakika kaldığını hesapla."""
        import re
        from datetime import datetime
        from zoneinfo import ZoneInfo
        match = re.search(r'(\d+):(\d+)\s*(am|pm)\s*-\s*(\d+):(\d+)\s*(am|pm)', question.lower())
        if not match:
            return None
        h2, m2, ap2 = int(match.group(4)), int(match.group(5)), match.group(6)
        # Convert to 24h
        if ap2 == 'pm' and h2 != 12:
            h2 += 12
        elif ap2 == 'am' and h2 == 12:
            h2 = 0
        now_et = datetime.now(ZoneInfo("America/New_York"))
        close_minutes = h2 * 60 + m2
        now_minutes = now_et.hour * 60 + now_et.minute
        diff = close_minutes - now_minutes
        if diff < -60:  # next day
            diff += 24 * 60
        return diff

    def _select_markets(self, markets: list[dict], capital: float) -> list[dict]:
        """Select best markets for quoting based on spread and liquidity."""
        scored = []
        for m in markets:
            # STRICT: coin harici market yasak
            question = m.get("question", "")
            if not self._is_crypto_market(question):
                continue

            # STRICT: sadece 20dk içinde kapanacak marketler
            mins_left = self._minutes_to_close(question)
            if mins_left is None or mins_left <= 0 or mins_left > 20:
                continue
            yes_ask = float(m.get("best_ask", 0) or 0)
            yes_bid = float(m.get("best_bid", 0) or 0)
            no_ask = float(m.get("no_best_ask", 0) or 0)

            # Need both token IDs
            if not m.get("yes_token_id") or not m.get("no_token_id"):
                continue

            # Calculate spread
            if yes_ask <= 0 or yes_bid <= 0:
                continue
            spread_pct = (yes_ask - yes_bid) / yes_ask if yes_ask > 0 else 0

            # Filter by spread
            if spread_pct < self.MIN_SPREAD_PCT or spread_pct > self.MAX_SPREAD_PCT:
                continue

            # Filter by price range (don't quote extreme prices)
            mid = (yes_ask + yes_bid) / 2
            if mid < self.QUOTE_PRICE_MIN or mid > self.QUOTE_PRICE_MAX:
                continue

            # Score: prefer wider spreads (more profit) with decent liquidity
            volume = float(m.get("volume", 0) or 0)
            score = spread_pct * min(volume / 10000, 1.0)
            scored.append((score, m))

        # Sort by score descending, take top N
        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored[:self.MAX_MARKETS]]

    async def _quote_market(
        self,
        market: dict,
        capital_per_side: float,
        client,
    ) -> int | None:
        """Place YES + NO bids for a single market. Returns count of orders placed."""
        market_id = market.get("condition_id", "")
        question = market.get("question", "")
        yes_token = market.get("yes_token_id", "")
        no_token = market.get("no_token_id", "")
        yes_ask = float(market.get("best_ask", 0) or 0)
        yes_bid = float(market.get("best_bid", 0) or 0)

        if not yes_token or not no_token or yes_ask <= 0:
            return None

        yes_mid = (yes_ask + yes_bid) / 2 if yes_bid > 0 else yes_ask

        # Get inventory for this market
        inv = self._inventory.get(market_id, MarketInventory())

        # Fetch real NO orderbook for accurate pricing
        no_book = client.get_orderbook(no_token)
        no_ask = float(no_book["best_ask"]) if no_book and no_book.get("best_ask") else 0.0
        no_best_bid = float(no_book["best_bid"]) if no_book and no_book.get("best_bid") else 0.0

        # Compute quotes — uses best_bid to place competitive bids
        yes_bid_price, yes_factor, no_bid_price, no_factor = self.stoikov.maker_quotes(
            yes_mid=yes_mid,
            yes_inventory=inv.yes_shares,
            no_inventory=inv.no_shares,
            volatility=self.VOLATILITY_DEFAULT,
            time_remaining=0.8,
            yes_best_bid=yes_bid,
            no_best_bid=no_best_bid,
        )

        # Safety: our bid must be BELOW the best ask (otherwise we're a taker)
        if yes_bid_price >= yes_ask:
            yes_bid_price = round(yes_ask - 0.01, 2)
        if no_ask > 0 and no_bid_price >= no_ask:
            no_bid_price = round(no_ask - 0.01, 2)

        # Safety: YES_bid + NO_bid must be < 0.95 for profit (min 5% spread)
        if yes_bid_price + no_bid_price >= 0.95:
            logger.info(f"MAKER_SKIP_EXPENSIVE: {question[:40]} | YES={yes_bid_price}+NO={no_bid_price}={yes_bid_price+no_bid_price:.2f} >= 0.95")
            return None

        # Filter: don't place if bid too low
        if yes_bid_price < self.QUOTE_PRICE_MIN:
            yes_factor = 0.0
        if no_bid_price < self.QUOTE_PRICE_MIN:
            no_factor = 0.0

        placed = 0

        # Place YES bid
        if yes_factor > 0 and capital_per_side * yes_factor >= 1.0:
            yes_amount = capital_per_side * yes_factor
            yes_size = round(yes_amount / yes_bid_price, 2) if yes_bid_price > 0 else 0
            if yes_size >= 5:
                result = await client.place_passive_order(
                    token_id=yes_token,
                    price=yes_bid_price,
                    size=yes_size,
                    question=f"MAKER_YES: {question[:50]}",
                )
                if result and result.get("order_id"):
                    self._standing[result["order_id"]] = StandingOrder(
                        order_id=result["order_id"],
                        market_id=market_id,
                        token_id=yes_token,
                        side="YES",
                        price=yes_bid_price,
                        size=yes_size,
                        placed_at=time.time(),
                    )
                    placed += 1

        # Place NO bid
        if no_factor > 0 and capital_per_side * no_factor >= 1.0:
            no_amount = capital_per_side * no_factor
            no_size = round(no_amount / no_bid_price, 2) if no_bid_price > 0 else 0
            if no_size >= 5:
                result = await client.place_passive_order(
                    token_id=no_token,
                    price=no_bid_price,
                    size=no_size,
                    question=f"MAKER_NO: {question[:50]}",
                )
                if result and result.get("order_id"):
                    self._standing[result["order_id"]] = StandingOrder(
                        order_id=result["order_id"],
                        market_id=market_id,
                        token_id=no_token,
                        side="NO",
                        price=no_bid_price,
                        size=no_size,
                        placed_at=time.time(),
                    )
                    placed += 1

        if placed > 0:
            logger.info(
                f"MAKER_QUOTE: {question[:40]} | "
                f"YES_bid={yes_bid_price:.2f} NO_bid={no_bid_price:.2f} | "
                f"orders={placed}"
            )

        return placed

    async def _cancel_all_standing(self, client) -> int:
        """Cancel all standing maker orders.

        cancel_order() returning False conflates two very different cases:
        a real cancel error, and "already matched by the CLOB, nothing left
        to cancel" — the far more common case for a resting GTC order that
        did its job. Discarding the order either way silently dropped a
        real fill: on_fill() (the only thing that updates self._inventory /
        check_paired_profit()) was never called. Check the real order
        status before deleting, same fill-detection pattern already used
        by PositionManager._check_order_filled().
        """
        cancelled = 0
        for order_id, order in list(self._standing.items()):
            if client.cancel_order(order_id):
                cancelled += 1
                del self._standing[order_id]
                continue

            order_data = await client.get_order_status(order_id)
            status = ((order_data or {}).get("status") or "").upper()
            size_matched = float((order_data or {}).get("size_matched", 0) or 0)
            if status in ("MATCHED", "FILLED") or size_matched > 0:
                fill_size = size_matched if size_matched > 0 else order.size
                self.on_fill(order_id, order.price, fill_size)
            else:
                del self._standing[order_id]
        return cancelled

    def on_fill(self, order_id: str, price: float, size: float):
        """Called when a standing order is detected as filled."""
        order = self._standing.pop(order_id, None)
        if not order:
            return

        market_id = order.market_id
        if market_id not in self._inventory:
            self._inventory[market_id] = MarketInventory()

        inv = self._inventory[market_id]
        cost = price * size

        if order.side == "YES":
            inv.yes_shares += size
            inv.yes_cost += cost
        else:
            inv.no_shares += size
            inv.no_cost += cost

        logger.info(
            f"MAKER_FILL: {order.side} {order_id[:16]} | "
            f"price={price:.2f} size={size:.2f} cost=${cost:.2f} | "
            f"inventory: YES={inv.yes_shares:.0f} NO={inv.no_shares:.0f}"
        )

    def check_paired_profit(self, market_id: str) -> float | None:
        """Check if both sides filled — guaranteed profit if YES+NO cost < 1.0."""
        inv = self._inventory.get(market_id)
        if not inv or inv.yes_shares <= 0 or inv.no_shares <= 0:
            return None

        # Paired shares = min of both sides
        paired = min(inv.yes_shares, inv.no_shares)
        yes_avg = inv.yes_cost / inv.yes_shares if inv.yes_shares > 0 else 0
        no_avg = inv.no_cost / inv.no_shares if inv.no_shares > 0 else 0

        # If YES_avg + NO_avg < 1.0, we have guaranteed profit
        total_cost_per_share = yes_avg + no_avg
        if total_cost_per_share < 1.0:
            profit = paired * (1.0 - total_cost_per_share)
            logger.success(
                f"MAKER_PAIRED_PROFIT: {market_id[:20]} | "
                f"paired={paired:.0f} shares | YES_avg={yes_avg:.3f} NO_avg={no_avg:.3f} | "
                f"profit=${profit:.2f}"
            )
            return profit
        return None
