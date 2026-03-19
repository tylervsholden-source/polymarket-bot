"""
BTC WebSocket Arbitraj Ajanı

Strateji:
  1. Binance WebSocket → BTC/USDT fiyatını gerçek zamanlı izle
  2. ±BTC_MOVE_THRESHOLD_PCT (varsayılan %1.5) hareket algıla
  3. Polymarket'teki BTC Up/Down marketlerini tara (max 2 saat kapanışa kalan)
  4. Piyasa fiyatı henüz güncellenmemişse → 30 saniyelik pencerede emir ver
  5. Sabit $BTC_ARB_SIZE pozisyon (varsayılan $25)

Arbitraj mantığı:
  - BTC +%1.5 yükseldi → "BTC Up Today?" marketinde YES fiyatı henüz düşük
    → Beklenen olasılık artmıştır, edge varsa BUY
  - BTC -%1.5 düştü → "BTC Down Today?" veya threshold marketlerinde YES ucuz
    → Benzer mantık

Gereksinim: websockets kütüphanesi (pip install websockets)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Optional

import httpx
from loguru import logger
from core.dashboard import dashboard as _dash

BINANCE_WS = "wss://stream.binance.com:9443/ws/btcusdt@ticker"
GAMMA_API = "https://gamma-api.polymarket.com"

# BTC ile ilgili market soru kalıpları
BTC_PATTERNS = [
    r"btc",
    r"bitcoin",
]


def _is_btc_market(question: str) -> bool:
    q = question.lower()
    return any(re.search(p, q) for p in BTC_PATTERNS)


def _estimate_new_prob(old_price: float, btc_move_pct: float, is_up_market: bool) -> float:
    """
    BTC hareketi sonrası tahmini yeni olasılık.
    Basit lineer heuristik — 1% hareket ≈ 0.08-0.15 olasılık kayması.

    up_market=True  → BTC yükselince YES olasılığı artar
    up_market=False → BTC düşünce YES olasılığı artar (down market)
    """
    if is_up_market:
        delta = btc_move_pct * 0.10   # %1 = +0.10 olasılık
    else:
        delta = -btc_move_pct * 0.10  # down market: ters etki

    new_prob = old_price + delta
    return max(0.01, min(0.99, new_prob))


class BtcArbAgent:
    def __init__(self, client, position_manager, onchain_watcher=None):
        self.client = client           # PolymarketClient
        self.position_manager = position_manager
        self.onchain = onchain_watcher  # OnchainWatcher (opsiyonel)

        self.enabled = os.getenv("BTC_ARB_ENABLED", "true").lower() == "true"
        self.move_threshold = float(os.getenv("BTC_MOVE_THRESHOLD_PCT", 1.5)) / 100
        self.arb_size = float(os.getenv("BTC_ARB_SIZE", 25))
        self.min_edge = float(os.getenv("BTC_ARB_MIN_EDGE", 0.07))
        self.max_hours_to_close = float(os.getenv("BTC_ARB_MAX_HOURS", 2.0))

        self._last_btc_price: Optional[float] = None
        self._last_trigger_time: float = 0
        self._cooldown_seconds: float = 120  # Aynı yönde 2dk cooldown

        logger.info(
            f"BTC Arb Agent: threshold=%{self.move_threshold*100:.1f} "
            f"size=${self.arb_size} min_edge={self.min_edge}"
        )

    def _is_live_trading(self) -> bool:
        try:
            import json as _json
            ctrl_file = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data", "control.json",
            )
            with open(ctrl_file) as f:
                return _json.load(f).get("live_trading", False)
        except Exception:
            return False

    async def run(self):
        """Ana WebSocket döngüsü — sürekli çalışır, hata durumunda yeniden bağlanır."""
        if not self.enabled:
            logger.info("BTC Arb devre dışı (BTC_ARB_ENABLED=false)")
            return

        while True:
            try:
                await self._connect_and_listen()
            except Exception as e:
                logger.warning(f"BTC WebSocket bağlantı kesildi: {e} — 10s sonra yeniden bağlanıyor")
                await asyncio.sleep(10)

    async def _connect_and_listen(self):
        try:
            import websockets
        except ImportError:
            logger.error("websockets kurulu değil. `pip install websockets` çalıştır.")
            await asyncio.sleep(60)
            return

        logger.info("Binance BTC WebSocket bağlantısı kuruluyor...")
        async with websockets.connect(BINANCE_WS, ping_interval=20) as ws:
            logger.success("Binance BTC WebSocket bağlandı.")
            async for raw in ws:
                try:
                    data = json.loads(raw)
                    price = float(data.get("c", 0))  # "c" = last price
                    if price > 0:
                        await self._on_price(price)
                except Exception as e:
                    logger.debug(f"BTC ticker parse hatası: {e}")

    async def _on_price(self, current_price: float):
        """Her yeni fiyat tick'inde çağrılır."""
        if self._last_btc_price is None:
            self._last_btc_price = current_price
            return

        move = (current_price - self._last_btc_price) / self._last_btc_price

        # Dashboard: BTC fiyatını sürekli güncelle
        _dash.update("btc_arb", btc_price=current_price, status="izliyor")

        if abs(move) >= self.move_threshold:
            now = time.time()
            if now - self._last_trigger_time < self._cooldown_seconds:
                return  # Cooldown aktif

            direction = "UP" if move > 0 else "DOWN"
            logger.info(
                f"BTC ARB TETİKLENDİ: {self._last_btc_price:,.0f} → {current_price:,.0f} "
                f"(%{move*100:+.2f}) — {direction}"
            )
            _dash.update("btc_arb",
                btc_price=current_price,
                last_move_pct=round(move * 100, 2),
                status="TETİKLENDİ",
            )

            self._last_trigger_time = now
            self._last_btc_price = current_price  # Referansı güncelle

            # Asyncio task olarak çalıştır — WebSocket döngüsünü bloklamasın
            asyncio.create_task(self._execute_arb(move, current_price))
        else:
            # Yavaş hareketlerde referansı kayarak güncelle
            self._last_btc_price = self._last_btc_price * 0.99 + current_price * 0.01

    async def _execute_arb(self, btc_move: float, btc_price: float):
        """BTC hareketi sonrası Polymarket'te arbitraj fırsatı ara ve gir."""
        try:
            # Onchain DANGER sinyali varsa emir verme
            if self.onchain and self.onchain.signal.get("alert_level") == "DANGER":
                msg = self.onchain.signal.get("message", "")
                logger.warning(f"BTC arb DURDURULDU — Onchain DANGER sinyali: {msg}")
                return

            markets = await self._fetch_btc_markets()
            if not markets:
                logger.debug("BTC arb: uygun market bulunamadı.")
                return

            placed = 0
            for market in markets:
                if placed >= 2:  # Tek tetiklemede max 2 pozisyon
                    break

                market_id = market.get("condition_id") or market.get("conditionId", "")
                if not market_id or self.position_manager.has_position(market_id):
                    continue

                question = market.get("question", "").lower()
                is_up_market = any(w in question for w in ["up", "above", "over", "high", "bull", "rise", "yüksel"])
                is_down_market = any(w in question for w in ["down", "below", "under", "low", "bear", "fall", "düş"])

                if not is_up_market and not is_down_market:
                    continue

                market_price = float(market.get("best_ask") or market.get("bestAsk") or 0)
                if not 0.05 <= market_price <= 0.95:
                    continue

                # Tahmini yeni olasılık
                new_prob = _estimate_new_prob(market_price, btc_move, is_up_market)
                edge = new_prob - market_price

                logger.info(
                    f"BTC arb fırsatı: {market['question'][:55]} | "
                    f"fiyat={market_price:.2f} → tahmini={new_prob:.2f} edge={edge:.2f}"
                )

                if edge < self.min_edge:
                    logger.debug(f"Edge yetersiz: {edge:.2f} < {self.min_edge}")
                    continue

                token_ids = market.get("clobTokenIds") or []
                token_id = token_ids[0] if token_ids else None

                if not self._is_live_trading():
                    logger.debug(f"BTC arb SIM (live_trading=false): {market['question'][:50]}")
                    continue

                order = await self.client.place_order(
                    market_id=market_id,
                    outcome="YES",
                    amount=self.arb_size,
                    price=market_price,
                    token_id=token_id,
                    question=market.get("question", ""),
                )

                if order:
                    self.position_manager.add_position(market_id, order, market["question"])
                    if market_id in self.position_manager.data.get("positions", {}):
                        self.position_manager.data["positions"][market_id]["btc_arb"] = True
                        self.position_manager.data["positions"][market_id]["btc_move_pct"] = round(btc_move * 100, 2)
                        self.position_manager._save()
                    placed += 1
                    logger.success(
                        f"BTC ARB EMRİ: {market['question'][:50]} | "
                        f"${self.arb_size} @ {market_price:.2f} | BTC %{btc_move*100:+.1f} | BTC=${btc_price:,.0f}"
                    )
                    _dash.update("btc_arb",
                        status="EMİR VERİLDİ",
                        last_trade=f"{market['question'][:30]} @ {market_price:.2f}",
                    )
                    _dash.add_decision(
                        agent="BtcArb",
                        market=market["question"],
                        action="BUY",
                        size=self.arb_size,
                        price=market_price,
                        edge=round(edge, 3),
                        result="EMİR",
                    )

        except Exception as e:
            logger.error(f"BTC arb execute hatası: {e}")

    async def _fetch_btc_markets(self) -> list[dict]:
        """Polymarket'ten aktif BTC marketlerini çek (kapanışa ≤ 2 saat kalan)."""
        try:
            async with httpx.AsyncClient(timeout=10) as session:
                resp = await session.get(
                    f"{GAMMA_API}/markets",
                    params={
                        "active": "true",
                        "closed": "false",
                        "limit": 100,
                        "order": "volume24hr",
                        "ascending": "false",
                    },
                )
                resp.raise_for_status()
                all_markets = resp.json()

            result = []
            now = asyncio.get_event_loop().time()

            for m in all_markets:
                if not _is_btc_market(m.get("question", "")):
                    continue

                vol = float(m.get("volume", 0) or 0)
                if vol < 1000:
                    continue

                # Kapanışa kalan süre
                end_date = m.get("endDateIso") or m.get("endDate") or ""
                hours = self._hours_to_close(end_date)
                if hours is None or hours > self.max_hours_to_close or hours < 0.1:
                    continue

                # Normalize
                best_ask = m.get("bestAsk") or m.get("best_ask")
                if best_ask is not None:
                    m["best_ask"] = float(best_ask)
                m["condition_id"] = m.get("conditionId") or m.get("condition_id", "")

                result.append(m)

            logger.debug(f"BTC marketleri bulundu: {len(result)}")
            return result

        except Exception as e:
            logger.error(f"BTC market listesi alınamadı: {e}")
            return []

    @staticmethod
    def _hours_to_close(end_date: str) -> Optional[float]:
        if not end_date:
            return None
        try:
            from datetime import datetime, timezone
            s = str(end_date).replace("Z", "+00:00")
            if len(s) == 10:
                s += "T23:59:00+00:00"
            end_dt = datetime.fromisoformat(s)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            delta = end_dt - datetime.now(timezone.utc)
            return max(delta.total_seconds() / 3600, 0)
        except Exception:
            return None
