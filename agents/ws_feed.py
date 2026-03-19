"""
WebSocket Real-Time Price Feed — Bitstamp live trades.

Runs in a background thread, continuously updates price cache.
BinanceFeed can optionally use this for instant spot prices instead of REST polling.
"""
from __future__ import annotations

import json
import threading
import time
from loguru import logger

try:
    import websocket
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False

BITSTAMP_WS = "wss://ws.bitstamp.net"

# Symbol mapping: internal symbol → Bitstamp pair
_WS_PAIRS: dict[str, str] = {
    "BTCUSDT": "btcusd",
    "ETHUSDT": "ethusd",
    "SOLUSDT": "solusd",
    "XRPUSDT": "xrpusd",
    "DOGEUSDT": "dogeusd",
    "BNBUSDT": "bnbusd",
}


class RealtimeFeed:
    """Background WebSocket feed for real-time price updates."""

    def __init__(self):
        self._prices: dict[str, float] = {}      # symbol → last price
        self._timestamps: dict[str, float] = {}   # symbol → last update time
        self._trade_counts: dict[str, int] = {}   # symbol → trade count since start
        self._ws: websocket.WebSocketApp | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._connected = False
        self._reverse_map: dict[str, str] = {}    # channel → symbol

    def start(self, symbols: set[str] | None = None) -> bool:
        """Start WebSocket feed in background thread."""
        if not _WS_AVAILABLE:
            logger.warning("websocket-client not installed, WS feed disabled")
            return False

        if self._running:
            return True

        if symbols is None:
            symbols = set(_WS_PAIRS.keys())

        # Build reverse mapping: channel → symbol
        self._reverse_map = {}
        subscribe_pairs = []
        for sym in symbols:
            pair = _WS_PAIRS.get(sym)
            if pair:
                channel = f"live_trades_{pair}"
                self._reverse_map[channel] = sym
                subscribe_pairs.append(pair)
                self._trade_counts[sym] = 0

        if not subscribe_pairs:
            return False

        self._running = True
        self._thread = threading.Thread(target=self._run, args=(subscribe_pairs,), daemon=True)
        self._thread.start()
        logger.info(f"WS feed started: {len(subscribe_pairs)} pairs ({', '.join(subscribe_pairs)})")
        return True

    def stop(self) -> None:
        """Stop the WebSocket feed."""
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        self._connected = False
        logger.info("WS feed stopped")

    def get_price(self, symbol: str) -> float | None:
        """Get latest real-time price for symbol. Returns None if no data."""
        return self._prices.get(symbol)

    def get_age_seconds(self, symbol: str) -> float:
        """How old is the last price update (seconds)."""
        ts = self._timestamps.get(symbol, 0)
        if ts == 0:
            return float("inf")
        return time.time() - ts

    def get_stats(self) -> dict:
        """Get feed statistics."""
        return {
            "connected": self._connected,
            "prices": dict(self._prices),
            "ages": {s: round(self.get_age_seconds(s), 1) for s in self._prices},
            "trade_counts": dict(self._trade_counts),
        }

    def _run(self, pairs: list[str]) -> None:
        """WebSocket event loop (runs in background thread)."""
        while self._running:
            try:
                self._ws = websocket.WebSocketApp(
                    BITSTAMP_WS,
                    on_message=self._on_message,
                    on_open=lambda ws: self._on_open(ws, pairs),
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self._ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                logger.warning(f"WS feed error: {e}")

            if self._running:
                self._connected = False
                logger.info("WS feed reconnecting in 5s...")
                time.sleep(5)

    def _on_open(self, ws, pairs: list[str]) -> None:
        self._connected = True
        for pair in pairs:
            ws.send(json.dumps({
                "event": "bts:subscribe",
                "data": {"channel": f"live_trades_{pair}"}
            }))
        logger.info(f"WS connected: subscribed to {len(pairs)} pairs")

    def _on_message(self, ws, message: str) -> None:
        try:
            data = json.loads(message)
            if data.get("event") != "trade":
                return

            channel = data.get("channel", "")
            symbol = self._reverse_map.get(channel)
            if not symbol:
                return

            price = float(data["data"]["price"])
            self._prices[symbol] = price
            self._timestamps[symbol] = time.time()
            self._trade_counts[symbol] = self._trade_counts.get(symbol, 0) + 1

        except Exception:
            pass

    def _on_error(self, ws, error) -> None:
        logger.debug(f"WS error: {error}")

    def _on_close(self, ws, close_status_code, close_msg) -> None:
        self._connected = False
        logger.debug(f"WS closed: {close_status_code} {close_msg}")
