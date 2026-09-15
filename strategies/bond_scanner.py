"""
Bond Scanner — Finds near-certain outcomes across ALL Polymarket markets.

Strategy: Buy YES tokens priced at 0.93-0.97 (or NO tokens at 0.93-0.97).
These represent "nearly certain" outcomes. Hold to resolution for 3-7% return.

Example: "Will the sun rise tomorrow?" YES @ 0.95 → buy, wait, collect $1.00.
Return = (1.00 - 0.95) / 0.95 = 5.3% per trade.

Key: Scan ALL markets (not just crypto up/down). Politics, sports outcomes,
scheduled events with near-certain results.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from loguru import logger


@dataclass
class BondOpportunity:
    """A near-certain market opportunity."""
    condition_id: str
    question: str
    side: str            # "YES" or "NO" — which to buy
    token_id: str
    price: float         # entry price (0.93-0.97)
    expected_yield: float  # (1.0 - price) / price
    volume: float
    end_date: str
    days_to_resolve: float


class BondScanner:
    """Scans ALL Polymarket markets for near-certain (bond-like) outcomes."""

    # ── Configuration ──
    PROB_THRESHOLD = 0.93   # minimum probability to consider
    MAX_PRICE = 0.97        # don't buy above this (too little return)
    MIN_VOLUME = 10_000     # minimum market volume ($)
    MIN_DAYS = 0.5          # at least 12 hours to resolve
    MAX_DAYS = 14           # max 14 days to resolve
    MAX_POSITIONS = 3       # max concurrent bond positions
    MIN_YIELD = 0.03        # minimum 3% yield

    async def scan(self, client) -> list[BondOpportunity]:
        """Scan ALL active markets for bond opportunities.

        Returns opportunities sorted by yield (highest first).
        """
        try:
            markets = await client.get_all_active_markets(min_volume=self.MIN_VOLUME)
        except Exception as e:
            logger.error(f"BOND_SCAN: Market fetch failed: {e}")
            return []

        opportunities = []
        now = datetime.now(timezone.utc)

        for m in markets:
            opp = self._evaluate_market(m, now)
            if opp:
                opportunities.append(opp)

        # Sort by yield descending
        opportunities.sort(key=lambda o: o.expected_yield, reverse=True)

        if opportunities:
            logger.info(
                f"BOND_SCAN: {len(opportunities)} opportunities found "
                f"(from {len(markets)} markets)"
            )
            for o in opportunities[:5]:
                logger.info(
                    f"  BOND: {o.question[:50]} | {o.side} @ {o.price:.3f} | "
                    f"yield={o.expected_yield:.1%} | {o.days_to_resolve:.1f}d"
                )

        return opportunities

    def _evaluate_market(self, market: dict, now: datetime) -> BondOpportunity | None:
        """Check if a market qualifies as a bond opportunity."""
        yes_price = float(market.get("best_ask", 0) or 0)
        yes_bid = float(market.get("best_bid", 0) or 0)
        volume = float(market.get("volume", 0) or 0)
        question = market.get("question", "")
        condition_id = market.get("condition_id", "")
        yes_token = market.get("yes_token_id", "")
        no_token = market.get("no_token_id", "")
        end_date = market.get("end_date_iso", "") or market.get("endDateIso", "")

        if not condition_id or not question:
            return None

        # Volume filter
        if volume < self.MIN_VOLUME:
            return None

        # Check days to resolve
        days = self._days_to_resolve(end_date, now)
        if days is None or days < self.MIN_DAYS or days > self.MAX_DAYS:
            return None

        # Check YES side: high probability outcome (price near 1.0)
        if (self.PROB_THRESHOLD <= yes_price <= self.MAX_PRICE
                and yes_token):
            yld = (1.0 - yes_price) / yes_price
            if yld >= self.MIN_YIELD:
                return BondOpportunity(
                    condition_id=condition_id,
                    question=question,
                    side="YES",
                    token_id=yes_token,
                    price=yes_price,
                    expected_yield=yld,
                    volume=volume,
                    end_date=end_date,
                    days_to_resolve=days,
                )

        # Check NO side: YES is very cheap → NO is near-certain
        # NO price ~ 1 - YES_ask. If YES_ask <= 0.07, NO is ~0.93+
        # get_all_active_markets() never populates no_best_ask (unlike the
        # directional get_active_markets() path), so this estimate is always
        # synthetic. FIX (matching arbitrage_engine.py's FIX-3): bare
        # 1.0 - yes_price ignores the bid/ask spread (real NO_ask ≈ 1 -
        # YES_bid, not 1 - YES_ask) and was already proven too optimistic
        # there — same +0.02 conservative pad applied here.
        if yes_price <= (1.0 - self.PROB_THRESHOLD) and no_token:
            no_price_est = round(1.0 - yes_price + 0.02, 4)  # was 1.0 - yes_price (too optimistic)
            if self.PROB_THRESHOLD <= no_price_est <= self.MAX_PRICE:
                yld = (1.0 - no_price_est) / no_price_est
                if yld >= self.MIN_YIELD:
                    return BondOpportunity(
                        condition_id=condition_id,
                        question=question,
                        side="NO",
                        token_id=no_token,
                        price=no_price_est,
                        expected_yield=yld,
                        volume=volume,
                        end_date=end_date,
                        days_to_resolve=days,
                    )

        return None

    def _days_to_resolve(self, end_date: str, now: datetime) -> float | None:
        """Calculate days until market resolves."""
        if not end_date:
            return None
        try:
            s = str(end_date).replace("Z", "+00:00")
            if len(s) == 10:
                s += "T23:59:00+00:00"
            end_dt = datetime.fromisoformat(s)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            delta = (end_dt - now).total_seconds()
            return delta / 86400 if delta > 0 else None
        except Exception:
            return None

    @staticmethod
    def calc_yield(price: float) -> float:
        """Calculate expected yield for a bond purchase."""
        if price <= 0 or price >= 1.0:
            return 0.0
        return (1.0 - price) / price

    @staticmethod
    def calc_annualized_yield(price: float, days: float) -> float:
        """Annualize the yield based on holding period."""
        raw = BondScanner.calc_yield(price)
        if days <= 0:
            return 0.0
        return raw * (365.0 / days)
