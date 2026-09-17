"""
Latency Arbitrage Engine — Binance/Bitstamp WS fiyat spike → Polymarket instant order.

Strateji (0x8dxd cüzdanının kullandığı):
Binance spot fiyatı hareket eder → Polymarket henüz reprice etmeden doğru tarafı al.
Ortalama pencere: 2.7 saniye (Q1 2026).

Akış:
1. Binance/Bitstamp WebSocket'ten real-time trade stream al
2. Her coin için rolling 5-saniye VWAP hesapla
3. VWAP değişimi eşiği aşarsa (>0.08%) → spike tespit
4. Aktif Polymarket 5m/15m crypto market bul
5. Spike yönüne göre YES/NO token al
6. LiveGate minimal kontrol (lock + capital + position limit)
"""
from __future__ import annotations

import asyncio
import json
import time
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from loguru import logger

# WebSocket library
try:
    import websockets
    _WEBSOCKETS_AVAILABLE = True
except ImportError:
    _WEBSOCKETS_AVAILABLE = False

# Fallback: websocket-client (sync, used by existing ws_feed.py)
try:
    import websocket as websocket_client
    _WS_CLIENT_AVAILABLE = True
except ImportError:
    _WS_CLIENT_AVAILABLE = False


# ── Config ────────────────────────────────────────────────────────────────

@dataclass
class LatencyArbConfig:
    """Latency arbitrage parametreleri."""
    # Spike detection
    spike_threshold_pct: float = 0.08    # %0.08 = 80 bps minimum price change
    spike_window_sec: float = 5.0        # Rolling pencere (saniye)
    cooldown_sec: float = 30.0           # Aynı coin için spike arası min bekleme
    # Order
    bet_size: float = 2.0                # Sabit bet (Kelly yerine — hız kritik)
    max_open_positions: int = 5
    max_orders_per_hour: int = 6         # Latency arb daha agresif olabilir
    # Safety
    min_capital: float = 5.0             # Bu altında trade yapma
    max_spread_pct: float = 0.10         # Orderbook spread > %10 → skip
    # WS source priority
    use_binance: bool = True             # Binance WS (hızlı ama TR'de VPN gerekli)
    use_bitstamp: bool = True            # Bitstamp WS (fallback)


# ── Data types ────────────────────────────────────────────────────────────

@dataclass
class Trade:
    price: float
    qty: float
    ts: float  # epoch seconds


@dataclass
class SpikeSignal:
    symbol: str           # "BTCUSDT"
    direction: str        # "UP" or "DOWN"
    change_pct: float     # e.g. 0.12 = +0.12%
    vwap_before: float
    vwap_after: float
    detected_at: float    # epoch


# ── Symbol mapping ────────────────────────────────────────────────────────

COIN_KEYWORDS = {
    "BTCUSDT": "bitcoin",
    "ETHUSDT": "ethereum",
    "SOLUSDT": "solana",
    "XRPUSDT": "xrp",
    "DOGEUSDT": "dogecoin",
    "BNBUSDT": "bnb",
}

BITSTAMP_WS_PAIRS = {
    "BTCUSDT": "btcusd",
    "ETHUSDT": "ethusd",
    "SOLUSDT": "solusd",
    "XRPUSDT": "xrpusd",
    "DOGEUSDT": "dogeusd",
    "BNBUSDT": "bnbusd",
}

BINANCE_WS_STREAMS = {
    "BTCUSDT": "btcusdt@aggTrade",
    "ETHUSDT": "ethusdt@aggTrade",
    "SOLUSDT": "solusdt@aggTrade",
    "XRPUSDT": "xrpusdt@aggTrade",
    "DOGEUSDT": "dogeusdt@aggTrade",
    "BNBUSDT": "bnbusdt@aggTrade",
}


class LatencyArbEngine:
    """Real-time spike detection → instant Polymarket order.

    Orchestrator tarafından başlatılır, arka planda sürekli çalışır.
    Spike tespit edince doğrudan emir verir (30sn cycle beklemez).
    """

    def __init__(
        self,
        client,               # PolymarketClient
        position_manager,     # PositionManager
        process_lock=None,
        config: LatencyArbConfig | None = None,
    ):
        self.client = client
        self.position_manager = position_manager
        self._process_lock = process_lock
        self.config = config or LatencyArbConfig()

        # Rolling trade buffer per symbol (last N seconds of trades)
        self._trade_buffer: dict[str, deque[Trade]] = {
            sym: deque(maxlen=2000) for sym in COIN_KEYWORDS
        }
        # VWAP tracking
        self._vwap: dict[str, float] = {}      # current VWAP per symbol
        self._last_spike: dict[str, float] = {} # last spike timestamp per symbol

        # Active market cache (refreshed periodically)
        self._active_markets: list[dict] = []
        self._markets_last_refresh: float = 0.0

        # Order tracking
        self._order_timestamps: list[float] = []
        self._recently_ordered: dict[str, float] = {}  # market_id → epoch (dedup lock)
        self._running = False
        self._ws_connected = False

        # Recent spikes buffer — arb engine tarafından okunur
        self._recent_spikes: deque[SpikeSignal] = deque(maxlen=50)

        # Stats
        self._spikes_detected: int = 0
        self._orders_placed: int = 0
        self._orders_skipped: int = 0

    # ── Public API ────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start latency arb engine. Runs forever in background."""
        if self._running:
            return
        self._running = True
        logger.info("LATENCY_ARB: Engine başlatılıyor...")

        # Start market refresh loop
        asyncio.create_task(self._market_refresh_loop())

        # Start WS price feeds
        if self.config.use_binance:
            asyncio.create_task(self._binance_ws_loop())
        if self.config.use_bitstamp:
            asyncio.create_task(self._bitstamp_ws_loop())

        logger.info(
            f"LATENCY_ARB: Aktif | spike={self.config.spike_threshold_pct}% "
            f"window={self.config.spike_window_sec}s bet=${self.config.bet_size}"
        )

    def stop(self) -> None:
        self._running = False
        logger.info("LATENCY_ARB: Durduruldu.")

    def get_stats(self) -> dict:
        return {
            "running": self._running,
            "ws_connected": self._ws_connected,
            "spikes_detected": self._spikes_detected,
            "orders_placed": self._orders_placed,
            "orders_skipped": self._orders_skipped,
            "active_markets": len(self._active_markets),
            "vwap": {s: round(v, 2) for s, v in self._vwap.items() if v > 0},
        }

    # ── Binance WebSocket ─────────────────────────────────────────────────

    async def _binance_ws_loop(self) -> None:
        """Connect to Binance aggTrade stream for all symbols."""
        if not _WEBSOCKETS_AVAILABLE:
            logger.warning("LATENCY_ARB: websockets library not installed, Binance WS disabled")
            return

        streams = "/".join(BINANCE_WS_STREAMS.values())
        url = f"wss://stream.binance.com:9443/stream?streams={streams}"

        # Reverse map: stream name → symbol
        stream_to_sym = {v: k for k, v in BINANCE_WS_STREAMS.items()}

        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    self._ws_connected = True
                    logger.info(f"LATENCY_ARB: Binance WS bağlandı ({len(BINANCE_WS_STREAMS)} stream)")

                    async for raw_msg in ws:
                        if not self._running:
                            break
                        try:
                            msg = json.loads(raw_msg)
                            stream = msg.get("stream", "")
                            data = msg.get("data", {})
                            symbol = stream_to_sym.get(stream)
                            if not symbol:
                                continue

                            price = float(data.get("p", 0))
                            qty = float(data.get("q", 0))
                            ts = data.get("T", 0) / 1000.0  # ms → sec

                            if price > 0 and qty > 0:
                                self._on_trade(symbol, price, qty, ts or time.time())
                        except Exception:
                            pass

            except Exception as e:
                self._ws_connected = False
                logger.warning(f"LATENCY_ARB: Binance WS hata: {e}")

            if self._running:
                logger.info("LATENCY_ARB: Binance WS reconnect 5s...")
                await asyncio.sleep(5)

    # ── Bitstamp WebSocket (fallback) ─────────────────────────────────────

    async def _bitstamp_ws_loop(self) -> None:
        """Connect to Bitstamp live_trades stream."""
        if not _WEBSOCKETS_AVAILABLE:
            logger.warning("LATENCY_ARB: websockets library not installed, Bitstamp WS disabled")
            return

        url = "wss://ws.bitstamp.net"
        reverse_map = {f"live_trades_{pair}": sym for sym, pair in BITSTAMP_WS_PAIRS.items()}

        while self._running:
            try:
                async with websockets.connect(url, ping_interval=30, ping_timeout=10) as ws:
                    logger.info("LATENCY_ARB: Bitstamp WS bağlandı")

                    # Subscribe to all pairs
                    for pair in BITSTAMP_WS_PAIRS.values():
                        await ws.send(json.dumps({
                            "event": "bts:subscribe",
                            "data": {"channel": f"live_trades_{pair}"}
                        }))

                    async for raw_msg in ws:
                        if not self._running:
                            break
                        try:
                            msg = json.loads(raw_msg)
                            if msg.get("event") != "trade":
                                continue
                            channel = msg.get("channel", "")
                            symbol = reverse_map.get(channel)
                            if not symbol:
                                continue
                            data = msg.get("data", {})
                            price = float(data.get("price", 0))
                            qty = float(data.get("amount", 0))
                            if price > 0 and qty > 0:
                                self._on_trade(symbol, price, qty, time.time())
                        except Exception:
                            pass

            except Exception as e:
                logger.warning(f"LATENCY_ARB: Bitstamp WS hata: {e}")

            if self._running:
                logger.info("LATENCY_ARB: Bitstamp WS reconnect 5s...")
                await asyncio.sleep(5)

    # ── Trade processing & spike detection ────────────────────────────────

    def _on_trade(self, symbol: str, price: float, qty: float, ts: float) -> None:
        """Process incoming trade tick. Detect spikes."""
        buf = self._trade_buffer.get(symbol)
        if buf is None:
            return

        buf.append(Trade(price=price, qty=qty, ts=ts))

        # Clean old trades outside window
        cutoff = ts - self.config.spike_window_sec
        while buf and buf[0].ts < cutoff:
            buf.popleft()

        if len(buf) < 3:
            return

        # Calculate VWAP for window
        total_val = sum(t.price * t.qty for t in buf)
        total_qty = sum(t.qty for t in buf)
        if total_qty <= 0:
            return

        current_vwap = total_val / total_qty
        old_vwap = self._vwap.get(symbol, 0)
        self._vwap[symbol] = current_vwap

        if old_vwap <= 0:
            return

        # Detect spike
        change_pct = ((current_vwap - old_vwap) / old_vwap) * 100

        if abs(change_pct) >= self.config.spike_threshold_pct:
            # Cooldown check
            last_spike_ts = self._last_spike.get(symbol, 0)
            if ts - last_spike_ts < self.config.cooldown_sec:
                return

            self._last_spike[symbol] = ts
            self._spikes_detected += 1

            direction = "UP" if change_pct > 0 else "DOWN"
            spike = SpikeSignal(
                symbol=symbol,
                direction=direction,
                change_pct=round(change_pct, 4),
                vwap_before=round(old_vwap, 4),
                vwap_after=round(current_vwap, 4),
                detected_at=ts,
            )

            logger.info(
                f"SPIKE_DETECTED: {symbol} {direction} {change_pct:+.3f}% | "
                f"VWAP {old_vwap:.2f} → {current_vwap:.2f} | "
                f"trades={len(buf)} window={self.config.spike_window_sec}s"
            )

            # Spike'ı buffer'a ekle — arb engine okuyacak
            self._recent_spikes.append(spike)

    # ── Public: arb engine tarafından okunur ────────────────────────────

    def get_spike_boost(self, coin: str, max_age_sec: float = 30.0) -> float:
        """Coin için son spike'ın Bayesian boost değerini döndür.

        Returns: -0.02 to +0.02 arası boost.
        Spike yoksa veya eskiyse 0.0.
        """
        now = time.time()
        coin_lower = coin.lower()

        # Coin → symbol mapping (reverse). The caller (arbitrage_engine.py)
        # always passes sym.replace("USDT", "").lower(), e.g. "btc" for
        # BTCUSDT — NOT the human keyword ("bitcoin"). Matching against the
        # keyword via substring ("btc" in "bitcoin") is false for BTC, since
        # "btc" is not a contiguous substring of "bitcoin" (it only works
        # for coins whose keyword happens to start with the symbol prefix,
        # like "eth" in "ethereum"), so BTC — the flagship, most-traded
        # coin — silently always got 0.0 boost. Match against the symbol's
        # own prefix instead, which is exactly what the caller passes.
        target_sym = None
        for sym in COIN_KEYWORDS:
            if sym.replace("USDT", "").lower() == coin_lower:
                target_sym = sym
                break
        if not target_sym:
            return 0.0

        # En son spike'ı bul
        best_spike: SpikeSignal | None = None
        for spike in reversed(self._recent_spikes):
            if spike.symbol != target_sym:
                continue
            if now - spike.detected_at > max_age_sec:
                break  # Eski spike'lar — deque sıralı
            best_spike = spike
            break

        if not best_spike:
            return 0.0

        # Boost: spike büyüklüğüne orantılı, max ±0.02
        # 0.08% spike → 0.008 boost, 0.20% spike → 0.02 boost (cap)
        raw_boost = best_spike.change_pct / 100.0  # pct → fraction
        boost = max(-0.02, min(0.02, raw_boost * 10))  # 10x amplify, cap ±0.02

        return round(boost, 4)

    # ── Market matching ───────────────────────────────────────────────────

    def _find_market_for_spike(self, spike: SpikeSignal) -> dict | None:
        """Find the best active Polymarket market for this spike."""
        coin_kw = COIN_KEYWORDS.get(spike.symbol, "").lower()
        if not coin_kw:
            return None

        now_et = datetime.now(ZoneInfo("America/New_York"))
        best_market = None
        best_mins_left = float("inf")

        for m in self._active_markets:
            q = m.get("question", "").lower()
            if coin_kw not in q:
                continue
            if "up or down" not in q:
                continue

            # Check if market is in tradeable window
            mins_left = self._minutes_to_end(m.get("question", ""))
            if mins_left is None or mins_left <= 0 or mins_left > 15:
                continue

            # Already have position?
            cid = m.get("condition_id", "")
            if self.position_manager.has_position(cid):
                continue

            # Prefer market closest to closing (more price movement = more certainty)
            if mins_left < best_mins_left:
                best_mins_left = mins_left
                best_market = m

        return best_market

    def _can_trade(self, spike: SpikeSignal, market: dict) -> bool:
        """Quick safety checks. Speed > thoroughness here."""
        now = time.time()
        cid = market.get("condition_id", "")

        # ── DEDUP: aynı markete 5dk içinde tekrar girme ──
        last_order_time = self._recently_ordered.get(cid, 0.0)
        if now - last_order_time < 300:  # 5 dakika cooldown
            logger.debug(f"SPIKE_SKIP: dedup lock {cid[:16]} ({now - last_order_time:.0f}s ago)")
            self._orders_skipped += 1
            return False

        # ── DEDUP: aynı coin'e 5dk içinde tekrar girme ──
        coin_kw = COIN_KEYWORDS.get(spike.symbol, "").lower()
        for ordered_cid, ordered_time in self._recently_ordered.items():
            if now - ordered_time >= 300:
                continue
            # Check if same coin
            for m in self._active_markets:
                if m.get("condition_id") == ordered_cid:
                    if coin_kw and coin_kw in m.get("question", "").lower():
                        logger.debug(f"SPIKE_SKIP: same coin dedup {coin_kw}")
                        self._orders_skipped += 1
                        return False
                    break

        # Capital check
        capital = self.position_manager.available_capital()
        if capital < self.config.min_capital:
            logger.debug(f"SPIKE_SKIP: capital ${capital:.2f} < ${self.config.min_capital}")
            self._orders_skipped += 1
            return False

        if capital < self.config.bet_size:
            logger.debug(f"SPIKE_SKIP: capital ${capital:.2f} < bet ${self.config.bet_size}")
            self._orders_skipped += 1
            return False

        # Position limit
        open_count = self.position_manager.open_position_count()
        if open_count >= self.config.max_open_positions:
            logger.debug(f"SPIKE_SKIP: max positions {open_count}/{self.config.max_open_positions}")
            self._orders_skipped += 1
            return False

        # Rate limit
        cutoff = time.time() - 3600
        recent = [t for t in self._order_timestamps if t > cutoff]
        self._order_timestamps = recent
        if len(recent) >= self.config.max_orders_per_hour:
            logger.debug(f"SPIKE_SKIP: rate limit {len(recent)}/{self.config.max_orders_per_hour}")
            self._orders_skipped += 1
            return False

        return True

    # ── Market refresh ────────────────────────────────────────────────────

    async def _market_refresh_loop(self) -> None:
        """Refresh active markets every 60 seconds."""
        while self._running:
            try:
                markets = await self.client.get_active_markets(min_volume=0)
                # Filter to crypto up/down only
                filtered = []
                for m in markets:
                    q = m.get("question", "").lower()
                    if "up or down" not in q:
                        continue
                    if not any(kw in q for kw in COIN_KEYWORDS.values()):
                        continue
                    filtered.append(m)
                self._active_markets = filtered
                self._markets_last_refresh = time.time()
                logger.debug(f"LATENCY_ARB: {len(filtered)} aktif crypto market cached")
            except Exception as e:
                logger.debug(f"LATENCY_ARB: market refresh hatası: {e}")
            await asyncio.sleep(60)

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _minutes_to_end(question: str) -> float | None:
        """Parse market question and calculate minutes until market end time."""
        import re
        from datetime import datetime
        from zoneinfo import ZoneInfo

        # Extract end time: "6:30PM-6:45PM" → "6:45PM"
        pattern = re.compile(
            r'(\d{1,2}:\d{2}\s*(?:AM|PM))\s*[-–]\s*(\d{1,2}:\d{2}\s*(?:AM|PM))',
            re.IGNORECASE,
        )
        match = pattern.search(question)
        if not match:
            return None

        end_str = match.group(2).strip().upper()
        now_et = datetime.now(ZoneInfo("America/New_York"))

        try:
            end_time = datetime.strptime(end_str, "%I:%M%p").time()
            end_dt = now_et.replace(
                hour=end_time.hour, minute=end_time.minute, second=0, microsecond=0
            )
            diff = (end_dt - now_et).total_seconds() / 60.0
            return diff
        except Exception:
            return None
