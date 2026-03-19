"""
Global Piyasa Endeksi İzleyici

Her 5 dakikada bir yfinance üzerinden endeks fiyatlarını çeker:
  ^IXIC  — NASDAQ Composite
  ^GSPC  — S&P 500
  ^N225  — Nikkei 225 (Japonya)
  ^GDAXI — DAX (Almanya)
  000001.SS — Shanghai Composite (Çin)
  ^HSI   — Hang Seng (Hong Kong)
  BTC-USD — Bitcoin (yedek, Binance varken kullanılmaz)

get_context(question) → soru ile ilgili endeks verisini döner,
signal_agent promptuna eklenir.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

from loguru import logger

# ── Endeks tanımları ────────────────────────────────────────────────────────

INDICES = {
    # ── Forex ──────────────────────────────────────────────────────────────
    "USDTRY":   {"ticker": "USDTRY=X",  "keywords": ["usdtry", "usd/try", "turkish lira", "dolar", "lira", "kur", "tl"]},
    "EURTRY":   {"ticker": "EURTRY=X",  "keywords": ["eurtry", "eur/try", "euro", "avro", "€"]},
    "EURUSD":   {"ticker": "EURUSD=X",  "keywords": ["eurusd", "eur/usd", "euro dollar", "dxy"]},
    "GBPUSD":   {"ticker": "GBPUSD=X",  "keywords": ["gbpusd", "gbp", "sterling", "pound"]},
    # ── Emtia ──────────────────────────────────────────────────────────────
    "GOLD":     {"ticker": "GC=F",      "keywords": ["gold", "altın", "xau", "ons altın", "gc"]},
    "SILVER":   {"ticker": "SI=F",      "keywords": ["silver", "gümüş", "xag"]},
    "OIL":      {"ticker": "CL=F",      "keywords": ["oil", "petrol", "crude", "wti", "brent"]},
    # ── Hisse Endeksleri ───────────────────────────────────────────────────
    "NASDAQ":   {"ticker": "^IXIC",     "keywords": ["nasdaq", "tech", "qqq", "apple", "google", "meta", "amazon", "nvidia", "microsoft"]},
    "S&P500":   {"ticker": "^GSPC",     "keywords": ["s&p", "sp500", "spy", "us stock", "dow", "wall street", "american stock"]},
    "BIST100":  {"ticker": "XU100.IS",  "keywords": ["bist", "borsa istanbul", "turkey", "turkish", "türkiye", "xu100", "thyao", "eregl", "garan"]},
    "NIKKEI":   {"ticker": "^N225",     "keywords": ["nikkei", "japan", "japanese", "yen", "tokyo", "nky"]},
    "DAX":      {"ticker": "^GDAXI",    "keywords": ["dax", "germany", "german", "deutsche", "frankfurt", "euro stock"]},
    "SHANGHAI": {"ticker": "000001.SS", "keywords": ["shanghai", "china", "chinese", "csi", "a-share", "yuan", "renminbi"]},
    "HANGSENG": {"ticker": "^HSI",      "keywords": ["hang seng", "hong kong", "hsi", "hkex"]},
}

REFRESH_INTERVAL = 300  # 5 dakika


class MarketIndexWatcher:
    """Singleton — tüm modüller bu nesneyi import eder."""

    def __init__(self):
        # ticker → {"price": float, "change_pct": float, "name": str, "updated": str}
        self._data: dict[str, dict] = {}
        self._running = False

    # ── Arka plan döngüsü ────────────────────────────────────────────────────

    async def run(self):
        self._running = True
        logger.info("MarketIndexWatcher baslatildi (her 5 dk)")
        while self._running:
            await self._refresh()
            await asyncio.sleep(REFRESH_INTERVAL)

    async def _refresh(self):
        try:
            import yfinance as yf

            tickers = [v["ticker"] for v in INDICES.values()]
            names   = list(INDICES.keys())

            data = await asyncio.to_thread(
                lambda: yf.download(
                    tickers,
                    period="2d",
                    interval="1d",
                    progress=False,
                    auto_adjust=True,
                )
            )

            updated = datetime.now().strftime("%H:%M")
            new_data: dict[str, dict] = {}

            for name, meta in INDICES.items():
                ticker = meta["ticker"]
                try:
                    # Multi-ticker download: Close sütununda ticker kolonu
                    close_col = ("Close", ticker) if isinstance(data.columns, object) and hasattr(data.columns, "levels") else "Close"
                    closes = data[close_col].dropna()
                    if len(closes) < 1:
                        continue
                    price_today = float(closes.iloc[-1])
                    price_prev  = float(closes.iloc[-2]) if len(closes) >= 2 else price_today
                    change_pct  = ((price_today - price_prev) / price_prev * 100) if price_prev else 0.0
                    new_data[name] = {
                        "price":      round(price_today, 2),
                        "change_pct": round(change_pct, 2),
                        "updated":    updated,
                    }
                except Exception:
                    continue

            # ── Türev: Gram Altın (TRY) ────────────────────────────────────────
            if "GOLD" in new_data and "USDTRY" in new_data:
                gold_oz_usd = new_data["GOLD"]["price"]
                usdtry      = new_data["USDTRY"]["price"]
                gram_try    = (gold_oz_usd / 31.1035) * usdtry
                new_data["GRAM_ALTIN"] = {
                    "price":      round(gram_try, 2),
                    "change_pct": new_data["GOLD"]["change_pct"],
                    "updated":    updated,
                }

            if new_data:
                self._data = new_data
                summary = " | ".join(
                    f"{n}: {d['change_pct']:+.2f}%" for n, d in new_data.items()
                )
                logger.info(f"Endeksler guncellendi — {summary}")

        except Exception as e:
            logger.warning(f"MarketIndexWatcher refresh hatasi: {e}")

    # ── Context üretici ──────────────────────────────────────────────────────

    def get_context(self, question: str) -> Optional[str]:
        """
        Market sorusuyla ilgili endeksleri döner.
        Hiç eşleşme yoksa None döner (prompt değişmez).
        """
        if not self._data:
            return None

        q = question.lower()
        matched: list[str] = []

        for name, meta in INDICES.items():
            if name not in self._data:
                continue
            if any(kw in q for kw in meta["keywords"]):
                d = self._data[name]
                sign  = "+" if d["change_pct"] >= 0 else ""
                arrow = "↑" if d["change_pct"] >= 0 else "↓"
                matched.append(
                    f"  {name:<10}: {d['price']:>10,.2f}  {arrow}{sign}{d['change_pct']:.2f}%  "
                    f"(gunc: {d['updated']})"
                )

        # Eğer ilgili endeks bulunamazsa tüm açık endeksleri ekle (genel market soruları için)
        if not matched and any(kw in q for kw in ["market", "stock", "equity", "index", "borsa", "piyasa"]):
            for name, d in self._data.items():
                sign  = "+" if d["change_pct"] >= 0 else ""
                arrow = "↑" if d["change_pct"] >= 0 else "↓"
                matched.append(
                    f"  {name:<10}: {d['price']:>10,.2f}  {arrow}{sign}{d['change_pct']:.2f}%"
                )

        if not matched:
            return None

        return "\n".join(matched)

    def all_summary(self) -> str:
        """Dashboard için kısa özet."""
        if not self._data:
            return "Bekleniyor..."
        parts = []
        for name, d in self._data.items():
            sign = "+" if d["change_pct"] >= 0 else ""
            parts.append(f"{name} {sign}{d['change_pct']:.1f}%")
        return " | ".join(parts)


# Singleton
market_watcher = MarketIndexWatcher()
