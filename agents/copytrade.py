"""
CopytradeEngine — Crypto up/down 5m/15m marketlerde aktif trader'ları kopyala.

Kendi trader listesi var (Data API'den keşfedildi). SmartTraderTracker'dan bağımsız.
Her 2dk'da bir bu trader'ların son trade'lerini poll eder.
Yeni trade tespit edince aynı yöne $1-2 girer.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger

from core.polymarket_client import PolymarketClient
from core.position_manager import PositionManager

SNAPSHOT_FILE = Path("data/copytrade_snapshot.json")
DATA_API = "https://data-api.polymarket.com"

CRYPTO_KEYWORDS = [
    "bitcoin up or down", "ethereum up or down",
    "btc up or down", "eth up or down",
    "solana up or down", "sol up or down",
    "xrp up or down",
    "dogecoin up or down", "doge up or down",
    "bnb up or down",
    "hype up or down", "hyperliquid up or down",
]

ALLOWED_HORIZONS = (5, 15)

# ── Crypto Up/Down Aktif Trader'lar ─────────────────────────────────────
# Data API trade history'den keşfedildi (2026-03-21).
# Bu trader'lar sadece BTC/ETH/SOL 5m/15m marketlerde aktif.
# Sadece WR > %50 olan trader'lar (2026-03-21 analizi)
# 12 trader analiz edildi, 9'u çöp (%0-33 WR), 3'ü kârlı:
CRYPTO_TRADERS: list[dict] = [
    {"name": "Trusting-Marionberry",    "address": "0x6b1bdf3c115b85083dd41feae4daf7a634531923",  "wr": 0.71, "volume": 531},
    {"name": "Considerate-Racer",       "address": "0xe28feea8eb5e5f909d574a92f860fa751712a9b0",  "wr": 0.67, "volume": 348},
    {"name": "Realistic-Swivel",        "address": "0x2eb5714ff6f20f5f9f7662c556dbef5e1c9bf4d4",  "wr": 0.56, "volume": 6402},
]


class CopytradeEngine:
    """Crypto up/down trader'larının trade'lerini kopyalar."""

    POLL_INTERVAL = 120  # 2 dakika

    def __init__(
        self,
        client: PolymarketClient,
        position_manager: PositionManager,
        max_open: int = 5,
    ):
        self._client = client
        self._pm = position_manager
        self._max_open = max_open
        self._last_poll: float = 0.0
        # Her trader'ın son görülen trade timestamp'i
        self._seen_trades: dict[str, float] = self._load_snapshot()
        self._copied_this_cycle: set[str] = set()

    # ── Public ────────────────────────────────────────────────────────────

    async def check_and_copy(self) -> list[dict]:
        """Her cycle çağrılır. Trader'ları poll et, yeni trade varsa kopyala."""
        now = time.time()
        if now - self._last_poll < self.POLL_INTERVAL:
            return []
        self._last_poll = now
        self._copied_this_cycle.clear()

        new_trades = await self._poll_trader_trades()
        if not new_trades:
            return []

        logger.info(f"COPYTRADE: {len(new_trades)} yeni crypto trade tespit edildi")

        executed = []
        for trade in new_trades:
            try:
                result = await self._execute_copy(trade)
                if result:
                    executed.append(result)
            except Exception as e:
                logger.debug(f"COPYTRADE exec hata: {e}")

        self._save_snapshot()
        return executed

    # ── Trade polling ─────────────────────────────────────────────────────

    async def _poll_trader_trades(self) -> list[dict]:
        """Her trader'ın son trade'lerini çek, yenilerini döndür."""
        new_trades = []

        for trader in CRYPTO_TRADERS:
            try:
                resp = await self._client.session.get(
                    f"{DATA_API}/trades",
                    params={"user": trader["address"], "limit": 20},
                    timeout=10,
                )
                if resp.status_code != 200:
                    continue

                trades = resp.json()
                if not isinstance(trades, list):
                    continue

                last_seen = self._seen_trades.get(trader["address"], 0.0)
                max_ts = last_seen

                for t in trades:
                    ts = float(t.get("timestamp", 0) or 0)
                    if ts <= last_seen:
                        continue

                    # Sadece crypto up/down market mi?
                    title = (t.get("title", "") or "").lower()
                    if not any(kw in title for kw in CRYPTO_KEYWORDS):
                        continue

                    # Horizon kontrolü
                    question = t.get("title", "") or ""
                    horizon = self._parse_horizon(question)
                    if horizon not in ALLOWED_HORIZONS:
                        continue

                    side = (t.get("side", "") or "").upper()
                    outcome = (t.get("outcome", "") or "").upper()

                    # Direction belirleme
                    if side == "BUY" and outcome == "YES":
                        direction = "YES"
                    elif side == "BUY" and outcome == "NO":
                        direction = "NO"
                    elif side == "SELL" and outcome == "YES":
                        direction = "NO"  # YES satmak = bearish
                    elif side == "SELL" and outcome == "NO":
                        direction = "YES"  # NO satmak = bullish
                    else:
                        continue

                    new_trades.append({
                        "trader": trader["name"],
                        "condition_id": t.get("conditionId", ""),
                        "direction": direction,
                        "size": float(t.get("size", 0) or 0),
                        "price": float(t.get("price", 0) or 0),
                        "question": question,
                        "timestamp": ts,
                    })

                    max_ts = max(max_ts, ts)

                if max_ts > last_seen:
                    self._seen_trades[trader["address"]] = max_ts

            except Exception as e:
                logger.debug(f"COPYTRADE poll {trader['name']}: {e}")

        return new_trades

    # ── Execution ─────────────────────────────────────────────────────────

    async def _execute_copy(self, trade: dict) -> dict | None:
        cid = trade["condition_id"]
        trader = trade["trader"]
        direction = trade["direction"]
        question = trade["question"]

        if not cid:
            return None

        # Aynı cycle'da aynı market'e girme
        if cid in self._copied_this_cycle:
            return None

        # Zaten pozisyonumuz var mı?
        if self._pm.has_position(cid):
            return None

        # Max pozisyon kontrolü
        if self._pm.open_position_count() >= self._max_open:
            return None

        # Capital kontrolü
        capital = self._pm.available_capital()
        if capital < 1.0:
            return None

        # Bugünün marketi mi?
        now_et = datetime.now(ZoneInfo("America/New_York"))
        today_str = now_et.strftime("%B %d").replace(" 0", " ").lower()
        if today_str not in question.lower():
            return None

        # Market bilgisini çek — token_id lazım
        market = await self._find_market(cid)
        if not market:
            logger.debug(f"COPYTRADE: market bulunamadı {cid[:16]}")
            return None

        # Bet size: yüksek hacimli trader → $2, düşük → $1
        bet_size = 2.0 if trade["size"] > 10 else 1.0
        bet_size = min(bet_size, capital)

        # Token ID ve fiyat
        if direction == "YES":
            token_id = market.get("yes_token_id", "")
        else:
            token_id = market.get("no_token_id", "")

        if not token_id:
            return None

        book = self._client.get_orderbook(token_id)
        price = book["best_ask"] if book and book.get("best_ask") else 0.50

        logger.info(
            f"COPYTRADE_EXEC: {trader} → {direction} | {question[:55]} | "
            f"${bet_size:.2f} @ {price:.3f}"
        )

        order = await self._client.place_order(
            market_id=cid,
            outcome=direction,
            amount=bet_size,
            price=price,
            token_id=token_id,
            question=question,
        )

        if order:
            order["outcome"] = direction
            order["token_id"] = token_id
            order["source"] = "copytrade"
            order["copied_from"] = trader
            self._pm.add_position(cid, order, question)
            self._copied_this_cycle.add(cid)
            logger.success(
                f"COPYTRADE_OK: {trader} kopyalandı → {direction} "
                f"{question[:55]} | ${bet_size:.2f}"
            )
            return order
        else:
            logger.warning(f"COPYTRADE_FAIL: {question[:55]}")
            return None

    # ── Market lookup ─────────────────────────────────────────────────────

    async def _find_market(self, condition_id: str) -> dict | None:
        """Aktif marketlerden condition_id ile bul."""
        try:
            markets = await self._client.get_active_markets(min_volume=0)
            for m in markets:
                if m.get("condition_id") == condition_id:
                    return m
        except Exception:
            pass
        return None

    # ── Horizon parser ────────────────────────────────────────────────────

    @staticmethod
    def _parse_horizon(question: str) -> int | None:
        import re
        pattern = r"(\d{1,2}):(\d{2})(AM|PM)\s*-\s*(\d{1,2}):(\d{2})(AM|PM)"
        match = re.search(pattern, question, re.IGNORECASE)
        if not match:
            return None

        h1, m1, p1 = int(match.group(1)), int(match.group(2)), match.group(3).upper()
        h2, m2, p2 = int(match.group(4)), int(match.group(5)), match.group(6).upper()

        if p1 == "PM" and h1 != 12: h1 += 12
        if p1 == "AM" and h1 == 12: h1 = 0
        if p2 == "PM" and h2 != 12: h2 += 12
        if p2 == "AM" and h2 == 12: h2 = 0

        diff = (h2 * 60 + m2) - (h1 * 60 + m1)
        if diff <= 0:
            diff += 24 * 60
        return diff

    # ── Snapshot ──────────────────────────────────────────────────────────

    def _load_snapshot(self) -> dict[str, float]:
        try:
            if SNAPSHOT_FILE.exists():
                data = json.loads(SNAPSHOT_FILE.read_text())
                if isinstance(data, dict) and all(isinstance(v, (int, float)) for v in data.values()):
                    return data
        except Exception:
            pass
        return {}

    def _save_snapshot(self) -> None:
        try:
            SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
            SNAPSHOT_FILE.write_text(json.dumps(self._seen_trades))
        except Exception as e:
            logger.debug(f"COPYTRADE snapshot: {e}")
