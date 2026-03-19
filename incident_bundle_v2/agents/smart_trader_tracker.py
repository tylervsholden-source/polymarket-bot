"""
SmartTraderTracker — Polymarket'ın en başarılı trader'larının pozisyonlarını takip eder.

Strateji: Yüksek PnL/Volume verimliliğine sahip top 10 trader seçildi.
Onların aldığı pozisyon = güçlü "bilgi avantajı" sinyali.

Çalışma mantığı:
- Her 5 dakikada bir tüm trader'ların açık pozisyonlarını çeker ve cache'ler
- Bir market analiz edilirken bu cache'e bakılır
- Smart money alıyorsa → bayesian_prob hafifçe artar (+0.05'e kadar)
- Smart money satıyorsa → bayesian_prob hafifçe düşer

Leaderboard API: https://data-api.polymarket.com/v1/leaderboard
Positions API:   https://data-api.polymarket.com/positions?user=<address>
"""
from __future__ import annotations

import asyncio
import time
from loguru import logger

DATA_API = "https://data-api.polymarket.com"
LEADERBOARD_URL = f"{DATA_API}/v1/leaderboard"
POSITIONS_URL   = f"{DATA_API}/positions"

# Top 10 trader — PnL/Volume verimliliğine göre sıralanmış (Mart 2026 verisi)
# Seçim kriteri: yüksek mutlak PnL + yüksek PnL/Volume oranı = gerçek edge sahibi
TOP_TRADERS: list[dict] = [
    {"name": "BabaTrump",      "address": "0x2bf64b86b64c315d879571b07a3b76629e467cd0", "efficiency": 0.57},
    {"name": "RepTrump",       "address": "0x863134d00841b2e200492805a01e1e2f5defaa53", "efficiency": 0.54},
    {"name": "Theo4",          "address": "0x56687bf447db6ffa42ffe2204a05edaa20f55839", "efficiency": 0.51},
    {"name": "BetTom42",       "address": "0x885783760858e1bd5dd09a3c3f916cfa251ac270", "efficiency": 0.50},
    {"name": "alexmulti",      "address": "0xd0c042c08f755ff940249f62745e82d356345565", "efficiency": 0.49},
    {"name": "mikatrade77",    "address": "0x23786fdad0073692157c6d7dc81f281843a35fcb", "efficiency": 0.47},
    {"name": "Jenzigo",        "address": "0x16f91db2592924cfed6e03b7e5cb5bb1e32299e3", "efficiency": 0.42},
    {"name": "FeatherLeather", "address": "0xd25c72ac0928385610611c8148803dc717334d20", "efficiency": 0.40},
    {"name": "majorexploiter", "address": "0x019782cab5d844f02bafb71f512758be78579f3c", "efficiency": 0.39},
    {"name": "Michie",         "address": "0xed2239a9150c3920000d0094d28fa51c7db03dd0", "efficiency": 0.36},
]

# Leaderboard'dan dinamik güncelleme için (aylık)
LEADERBOARD_REFRESH_INTERVAL = 86400  # 24 saat


class SmartTraderTracker:
    """Top Polymarket trader'larının pozisyonlarını 5dk cache ile takip eder."""

    POSITION_REFRESH_INTERVAL = 300  # 5 dakika

    def __init__(self, session=None):
        import httpx
        self.session = session or httpx.AsyncClient(timeout=12)
        self._positions: dict[str, dict[str, float]] = {}  # condition_id → {trader_name: net}
        self._last_refresh: float = 0.0
        self._last_leaderboard: float = 0.0
        self._traders: list[dict] = list(TOP_TRADERS)  # başlangıçta statik liste

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def refresh(self) -> None:
        """Gerekirse leaderboard ve pozisyonları güncelle."""
        now = time.time()

        # Aylık: leaderboard'dan top trader'ları güncelle
        if now - self._last_leaderboard > LEADERBOARD_REFRESH_INTERVAL:
            await self._refresh_leaderboard()
            self._last_leaderboard = now

        # 5dk'da bir: açık pozisyonları çek
        if now - self._last_refresh > self.POSITION_REFRESH_INTERVAL:
            await self._refresh_all_positions()
            self._last_refresh = now

    def get_signal(self, condition_id: str) -> dict:
        """
        Bir market için smart money sinyali döner.

        Returns:
            signal      : -1.0 (hepsi short) → +1.0 (hepsi long)
            buyers      : long olan trader isimleri
            sellers     : short olan trader isimleri
            total_traders: kaç trader bu markette pozisyon tutuyor
        """
        market_pos = self._positions.get(condition_id, {})
        if not market_pos:
            return {"signal": 0.0, "buyers": [], "sellers": [], "total_traders": 0}

        buyers:  list[str] = []
        sellers: list[str] = []
        weighted_long  = 0.0
        weighted_short = 0.0

        for trader in self._traders:
            name = trader["name"]
            net  = market_pos.get(name, 0.0)
            if net > 0:
                buyers.append(name)
                weighted_long  += trader["efficiency"]
            elif net < 0:
                sellers.append(name)
                weighted_short += trader["efficiency"]

        total = weighted_long + weighted_short
        signal = (weighted_long - weighted_short) / total if total > 0 else 0.0

        return {
            "signal":        round(signal, 3),
            "buyers":        buyers,
            "sellers":       sellers,
            "total_traders": len(market_pos),
        }

    # ------------------------------------------------------------------ #
    # Leaderboard güncellemesi
    # ------------------------------------------------------------------ #

    async def _refresh_leaderboard(self) -> None:
        """Polymarket leaderboard'dan son 1 aylık top 20 trader'ı çek."""
        try:
            resp = await self.session.get(
                LEADERBOARD_URL,
                params={
                    "category":   "OVERALL",
                    "timePeriod": "MONTH",
                    "orderBy":    "PNL",
                    "limit":      20,
                    "offset":     0,
                },
            )
            if resp.status_code != 200:
                return
            data = resp.json()
            traders = data if isinstance(data, list) else data.get("data", [])
            if not traders:
                return

            # Mevcut listeyi güncelle (adresi bilinenleri koru, yenileri ekle)
            known_addresses = {t["address"] for t in self._traders}
            added = 0
            for entry in traders:
                addr = entry.get("proxyWallet", "")
                if not addr or addr in known_addresses:
                    continue
                pnl = float(entry.get("pnl", 0) or 0)
                vol = float(entry.get("vol", 1) or 1)
                efficiency = round(pnl / vol, 3) if vol > 0 else 0.0
                if efficiency < 0.20:  # %20'nin altındaki verimliliği dikkate alma
                    continue
                self._traders.append({
                    "name":       entry.get("userName") or addr[:10],
                    "address":    addr,
                    "efficiency": efficiency,
                })
                known_addresses.add(addr)
                added += 1

            if added:
                logger.info(f"SmartTrader: leaderboard'dan {added} yeni trader eklendi (toplam {len(self._traders)})")

        except Exception as e:
            logger.debug(f"SmartTrader leaderboard güncelleme hatası: {e}")

    # ------------------------------------------------------------------ #
    # Pozisyon yenileme
    # ------------------------------------------------------------------ #

    async def _refresh_all_positions(self) -> None:
        """Tüm trader'ların pozisyonlarını paralel çek."""
        tasks = [self._fetch_positions(t) for t in self._traders]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        ok = sum(1 for r in results if r is True)
        logger.debug(f"SmartTrader: {ok}/{len(self._traders)} trader pozisyonu güncellendi.")

    async def _fetch_positions(self, trader: dict) -> bool:
        """Tek trader'ın açık pozisyonlarını çek, cache'e yaz."""
        from datetime import datetime, timezone
        try:
            resp = await self.session.get(
                POSITIONS_URL,
                params={
                    "user":  trader["address"],
                    "limit": 500,
                },
            )
            if resp.status_code != 200:
                return False

            positions = resp.json()
            if not isinstance(positions, list):
                return False

            now = datetime.now(timezone.utc)
            added = 0
            for pos in positions:
                cid = pos.get("conditionId") or pos.get("market") or pos.get("condition_id")
                if not cid:
                    continue

                # Sadece aktif (kapanmamış) marketler
                end_date_str = pos.get("endDate", "")
                if end_date_str:
                    try:
                        s = str(end_date_str).replace("Z", "+00:00")
                        if len(s) == 10:
                            s += "T23:59:00+00:00"
                        end_dt = datetime.fromisoformat(s)
                        if end_dt.tzinfo is None:
                            end_dt = end_dt.replace(tzinfo=timezone.utc)
                        if end_dt < now:
                            continue  # Kapanmış market
                    except Exception:
                        pass

                size    = float(pos.get("size", 0) or 0)
                if size < 0.001:
                    continue
                outcome = str(pos.get("outcome", "") or "").upper()
                # YES = long (+), NO = effectively short (-)
                net = size if outcome == "YES" else -size
                if cid not in self._positions:
                    self._positions[cid] = {}
                self._positions[cid][trader["name"]] = net
                added += 1

            if added:
                logger.debug(f"SmartTrader {trader['name']}: {added} aktif pozisyon bulundu")
            return True

        except Exception as e:
            logger.debug(f"SmartTrader {trader['name']} pozisyon hatası: {e}")
            return False
