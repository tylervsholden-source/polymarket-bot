"""
Mean Reversion Strategy — Paone analizi (@paonx_eth) temelli iki Polymarket inefficiency:

Inefficiency 1 (Extreme Reversion):
  YES ≥ 88c veya YES ≤ 12c → piyasa tarihsel olarak %19 ihtimalle orta değere döner.
  Model: 6% → 800% kazanç, 13% → 500% kazanç, 81% → kayıp → net +13% per cycle (~83 saat).

Inefficiency 2 (Magnet Reversion):
  YES ≈ 75c veya YES ≈ 25c → piyasa tarihsel olarak %54 ihtimalle 50c'a döner.
  Model: 54% → 2x, 46% → kayıp → net +8% per cycle (~32 saat).
"""
from __future__ import annotations

from dataclasses import dataclass

from loguru import logger


# ── Filtre parametreleri ─────────────────────────────────────────────────────

EXTREME_TRIGGER_HIGH = 0.88    # YES ≥ bu değer → NO'u al (extreme high)
EXTREME_TRIGGER_LOW  = 0.12    # YES ≤ bu değer → YES'i al (extreme low)
EXTREME_MAX_ENTRY    = 0.15    # Entry fiyatı bu değerin altında olmalı
EXTREME_MIN_ENTRY    = 0.03    # 3c altı = ölü market, atla (tweet: "3-15c")

MAGNET_HIGH_MIN = 0.72         # YES 72-78c arası → magnet zone (high)
MAGNET_HIGH_MAX = 0.78
MAGNET_LOW_MIN  = 0.22         # YES 22-28c arası → magnet zone (low)
MAGNET_LOW_MAX  = 0.28

MIN_VOLUME = 5_000             # Market minimum $5k hacim (likidite)
MIN_END_HOURS = 4              # Market en az 4 saat kalsın (reversion için süre)

# ── Kelly parametreleri ──────────────────────────────────────────────────────
# Inefficiency 1: p=0.19, avg_win=5x → half-Kelly ≈ 1.3% of capital
# Inefficiency 2: p=0.54, avg_win=2x → half-Kelly ≈ 4% of capital
EXTREME_KELLY_FRACTION = 0.013
MAGNET_KELLY_FRACTION  = 0.04

MIN_BET  = 1.00   # Minimum tek emir (Polymarket min $1)
MAX_BET  = 2.00   # Maximum tek emir ($)


@dataclass
class MRSignal:
    market: dict
    mr_type: str       # "extreme" | "magnet"
    outcome: str       # "YES" | "NO"
    token_id: str
    entry_price: float
    target_price: float
    edge: float
    size: float
    reasoning: str


class MeanReversionStrategy:
    """
    Tüm aktif Polymarket marketlerini tarar ve mean reversion fırsatlarını döner.
    Ayrı pozisyon havuzu — 5dk crypto up/down limitine dahil değil.
    """

    def scan(self, markets: list[dict], capital: float, skip_ids: set[str]) -> list[MRSignal]:
        """
        markets   : get_active_markets() çıktısı (normalize edilmiş)
        capital   : kullanılabilir sermaye ($)
        skip_ids  : zaten pozisyon olan market condition_id'leri
        """
        signals: list[MRSignal] = []

        for m in markets:
            cid = m.get("condition_id", "")
            if not cid or cid in skip_ids:
                continue

            volume = float(m.get("volume") or m.get("volumeNum") or 0)
            if volume < MIN_VOLUME:
                continue

            yes_price = float(m.get("best_ask") or m.get("bestAsk") or 0)
            if yes_price <= 0 or yes_price >= 1:
                continue

            no_price = round(1.0 - yes_price, 4)
            yes_tid = m.get("yes_token_id")
            no_tid  = m.get("no_token_id")

            # ── Inefficiency 1: Extreme Reversion ───────────────────────────
            if yes_price >= EXTREME_TRIGGER_HIGH and EXTREME_MIN_ENTRY <= no_price <= EXTREME_MAX_ENTRY and no_tid:
                # YES çok yüksek → NO al (3-15c arası, tweet stratejisi)
                edge = self._extreme_edge(no_price)
                if edge > 0:
                    size = self._size(capital, EXTREME_KELLY_FRACTION)
                    signals.append(MRSignal(
                        market=m,
                        mr_type="extreme",
                        outcome="NO",
                        token_id=no_tid,
                        entry_price=no_price,
                        target_price=0.50,
                        edge=edge,
                        size=size,
                        reasoning=f"YES={yes_price:.2f} extreme high → NO@{no_price:.2f} mean reversion",
                    ))

            elif yes_price <= EXTREME_TRIGGER_LOW and EXTREME_MIN_ENTRY <= yes_price <= EXTREME_MAX_ENTRY and yes_tid:
                # YES çok düşük → YES al (3-15c arası)
                edge = self._extreme_edge(yes_price)
                if edge > 0:
                    size = self._size(capital, EXTREME_KELLY_FRACTION)
                    signals.append(MRSignal(
                        market=m,
                        mr_type="extreme",
                        outcome="YES",
                        token_id=yes_tid,
                        entry_price=yes_price,
                        target_price=0.50,
                        edge=edge,
                        size=size,
                        reasoning=f"YES={yes_price:.2f} extreme low → YES@{yes_price:.2f} mean reversion",
                    ))

            # ── Inefficiency 2: Magnet Reversion (75c/25c → 50c) ────────────
            elif MAGNET_HIGH_MIN <= yes_price <= MAGNET_HIGH_MAX and no_tid:
                # YES ≈ 75c → NO al (25c'dan 50c'a)
                edge = 0.08  # Tarihi beklenti
                size = self._size(capital, MAGNET_KELLY_FRACTION)
                signals.append(MRSignal(
                    market=m,
                    mr_type="magnet",
                    outcome="NO",
                    token_id=no_tid,
                    entry_price=no_price,
                    target_price=0.50,
                    edge=edge,
                    size=size,
                    reasoning=f"YES={yes_price:.2f} near 75c magnet → NO@{no_price:.2f} target 50c",
                ))

            elif MAGNET_LOW_MIN <= yes_price <= MAGNET_LOW_MAX and yes_tid:
                # YES ≈ 25c → YES al (25c'dan 50c'a)
                edge = 0.08
                size = self._size(capital, MAGNET_KELLY_FRACTION)
                signals.append(MRSignal(
                    market=m,
                    mr_type="magnet",
                    outcome="YES",
                    token_id=yes_tid,
                    entry_price=yes_price,
                    target_price=0.50,
                    edge=edge,
                    size=size,
                    reasoning=f"YES={yes_price:.2f} near 25c magnet → YES@{yes_price:.2f} target 50c",
                ))

        # Edge'e göre sırala
        signals.sort(key=lambda s: s.edge, reverse=True)
        if signals:
            logger.info(f"[MeanReversion] {len(signals)} fırsat bulundu "
                        f"(extreme={sum(1 for s in signals if s.mr_type=='extreme')}, "
                        f"magnet={sum(1 for s in signals if s.mr_type=='magnet')})")
        return signals

    # ── Yardımcı metodlar ────────────────────────────────────────────────────

    def _extreme_edge(self, entry_price: float) -> float:
        """
        Tarihi veriye dayalı beklenen kazanç (Inefficiency 1).
        6% → 800% kazanç, 13% → 500% kazanç, 81% → -100%
        """
        win_prob  = 0.19
        # 50c hedef: (0.50/entry - 1) return
        avg_return = (0.50 / max(entry_price, 0.01)) - 1
        expected = win_prob * avg_return - 0.81
        return round(max(0, expected), 4)

    def _size(self, capital: float, kelly_fraction: float) -> float:
        bet = capital * kelly_fraction
        return round(max(MIN_BET, min(MAX_BET, bet)), 2)
