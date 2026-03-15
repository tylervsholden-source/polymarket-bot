"""
On-Chain Whale Watcher

İzlenen sinyaller:
  1. Whale Alert API — $500K+ BTC transferleri
     - Bilinmeyen cüzdan → borsa : SELL baskısı (bearish)
     - Borsa → bilinmeyen cüzdan : Çekim = holding (bullish)
     - Cüzdan → cüzdan ($100M+)  : Satoshi/mega-whale hareketi → DANGER
  2. Exchange Netflow (son 1 saat) — borsaya giren - çıkan
     - Pozitif netflow (giriş fazla) → satış baskısı
     - Negatif netflow (çıkış fazla) → birikim sinyali

API:
  Whale Alert free tier: https://whale-alert.io (kayıt gerekli, ücretsiz)
  .env: WHALE_ALERT_API_KEY=your_key

Sinyal formatı (btc_arb_agent tarafından okunur):
  {
    "alert_level": "DANGER" | "BEARISH" | "BULLISH" | "NEUTRAL",
    "message": "açıklama",
    "exchange_netflow": float,   # son 1 saat, BTC cinsinden
    "large_transfer_usd": float, # en büyük son transfer
    "timestamp": "HH:MM:SS",
  }
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import httpx
from loguru import logger

from core.dashboard import dashboard as _dash

WHALE_ALERT_URL = "https://api.whale-alert.io/v1/transactions"

# Bilinen büyük borsa adresi etiketleri (Whale Alert'ın "to"/"from" label'ları)
EXCHANGE_LABELS = {
    "binance", "coinbase", "kraken", "bitfinex", "huobi", "okx", "bybit",
    "kucoin", "gate", "gemini", "bitstamp", "upbit", "bithumb",
}

# Satoshi / genesis bloğu cüzdanları (ilk 50 blok)
SATOSHI_ADDRESSES = {
    "1A1zP1eP5QGefi2DMPTfTL5SLmv7Divf"  # genesis coinbase
    # gerçekte bu çıktılar harcanamaz, sembolik
}


class OnchainWatcher:
    """
    Arka planda çalışır, periyodik olarak Whale Alert'ı sorgular.
    Güncel sinyal `self.signal` dict'inde tutulur.
    BtcArbAgent bu dict'i okur.
    """

    def __init__(self):
        self.api_key = os.getenv("WHALE_ALERT_API_KEY", "")
        self.min_value_usd = float(os.getenv("ONCHAIN_MIN_VALUE_USD", 500_000))      # $500K+
        self.danger_threshold = float(os.getenv("ONCHAIN_DANGER_USD", 100_000_000))  # $100M = tehlike
        self.check_interval = int(os.getenv("ONCHAIN_CHECK_INTERVAL", 60))           # saniye

        # Paylaşılan sinyal — BtcArbAgent tarafından okunur
        self.signal: dict = {
            "alert_level": "NEUTRAL",
            "message": "İzleniyor",
            "exchange_netflow_btc": 0.0,
            "large_transfer_usd": 0.0,
            "timestamp": "—",
        }

        self._seen_tx_ids: set[str] = set()  # Aynı tx'i tekrar işleme
        self._exchange_inflow_btc = 0.0      # Son 1 saat borsaya giren BTC
        self._exchange_outflow_btc = 0.0     # Son 1 saat borsadan çıkan BTC

        if not self.api_key:
            logger.warning(
                "WHALE_ALERT_API_KEY bulunamadı. "
                "Ücretsiz anahtar için: https://whale-alert.io/signup"
            )

    async def run(self):
        """Arka plan döngüsü."""
        logger.info(f"OnchainWatcher başlatıldı (her {self.check_interval}s)")
        while True:
            try:
                await self._check()
            except Exception as e:
                logger.warning(f"OnchainWatcher hata: {e}")
            await asyncio.sleep(self.check_interval)

    async def _check(self):
        if not self.api_key:
            return

        transactions = await self._fetch_transactions()
        if not transactions:
            return

        new_txs = [t for t in transactions if t.get("id") not in self._seen_tx_ids]
        for tx in new_txs:
            self._seen_tx_ids.add(tx.get("id", ""))

        if not new_txs:
            return

        alert_level, message, max_usd = self._analyze(new_txs)

        netflow = self._exchange_inflow_btc - self._exchange_outflow_btc
        if abs(netflow) > 1000:  # 1000 BTC+ netflow güçlü sinyal
            if netflow > 0 and alert_level == "NEUTRAL":
                alert_level = "BEARISH"
                message += f" | Exchange netflow: +{netflow:,.0f} BTC (satış baskısı)"
            elif netflow < 0 and alert_level == "NEUTRAL":
                alert_level = "BULLISH"
                message += f" | Exchange netflow: {netflow:,.0f} BTC (birikim)"

        now = datetime.now().strftime("%H:%M:%S")
        self.signal = {
            "alert_level": alert_level,
            "message": message,
            "exchange_netflow_btc": round(netflow, 2),
            "large_transfer_usd": max_usd,
            "timestamp": now,
        }

        if alert_level != "NEUTRAL":
            log_fn = logger.warning if alert_level == "DANGER" else logger.info
            log_fn(f"ONCHAIN {alert_level}: {message}")

        # Dashboard güncelle
        _dash.update("onchain", **self.signal)

    def _analyze(self, transactions: list) -> tuple[str, str, float]:
        alert_level = "NEUTRAL"
        message = "Normal aktivite"
        max_usd = 0.0

        for tx in transactions:
            usd_amount = float(tx.get("amount_usd", 0) or 0)
            btc_amount = float(tx.get("amount", 0) or 0)
            symbol = tx.get("symbol", "").upper()

            if symbol != "BTC":
                continue

            from_label = (tx.get("from", {}) or {}).get("owner_type", "")
            to_label = (tx.get("to", {}) or {}).get("owner_type", "")
            from_owner = (tx.get("from", {}) or {}).get("owner", "").lower()
            to_owner = (tx.get("to", {}) or {}).get("owner", "").lower()

            if usd_amount > max_usd:
                max_usd = usd_amount

            # Exchange netflow güncelle
            to_is_exchange = to_label == "exchange" or any(ex in to_owner for ex in EXCHANGE_LABELS)
            from_is_exchange = from_label == "exchange" or any(ex in from_owner for ex in EXCHANGE_LABELS)

            if to_is_exchange:
                self._exchange_inflow_btc += btc_amount
            if from_is_exchange:
                self._exchange_outflow_btc += btc_amount

            # Tehlike seviyesi belirleme
            if usd_amount >= self.danger_threshold:
                alert_level = "DANGER"
                who = from_owner or "bilinmeyen cüzdan"
                dest = to_owner or "bilinmeyen cüzdan"
                message = (
                    f"MEGA WHALE: ${usd_amount/1e6:.0f}M BTC hareketi "
                    f"{who} → {dest}"
                )

            elif usd_amount >= self.min_value_usd:
                if to_is_exchange and alert_level not in ("DANGER",):
                    alert_level = "BEARISH"
                    message = (
                        f"${usd_amount/1e6:.1f}M BTC borsaya girdi "
                        f"({to_owner or 'exchange'}) — satış baskısı"
                    )
                elif from_is_exchange and alert_level not in ("DANGER", "BEARISH"):
                    alert_level = "BULLISH"
                    message = (
                        f"${usd_amount/1e6:.1f}M BTC borsadan çekildi "
                        f"({from_owner or 'exchange'}) — birikim sinyali"
                    )

        return alert_level, message, max_usd

    async def _fetch_transactions(self) -> list:
        params = {
            "api_key": self.api_key,
            "min_value": int(self.min_value_usd),
            "limit": 100,
            "currency": "btc",
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(WHALE_ALERT_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            return data.get("transactions", [])
