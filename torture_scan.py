"""
Mr. Torture — Canlı Polymarket Market Tarayıcısı

Polymarket'taki en yüksek hacimli marketleri çekip
Mr. Torture'a göndererek acımasız bir görüş alır.

Kullanım:
    python torture_scan.py              # İlk 20 market
    python torture_scan.py --limit 10   # İlk 10 market
    python torture_scan.py --min-vol 50000  # 50K$+ hacimli marketler
"""

import asyncio
import argparse
import sys
import os
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

from loguru import logger
from core.polymarket_client import PolymarketClient
from agents.torture_agent import MrTortureAgent


VERDICT_COLORS = {
    "BUY":  "\033[1;92m",  # Parlak yeşil
    "HOLD": "\033[1;93m",  # Parlak sarı
    "SELL": "\033[1;91m",  # Parlak kırmızı
}
RESET = "\033[0m"
BOLD  = "\033[1m"


async def scan(limit: int, min_vol: float):
    if not os.getenv("ANTHROPIC_API_KEY"):
        logger.error("ANTHROPIC_API_KEY bulunamadı — .env dosyasını kontrol et.")
        return

    print(f"\n{BOLD}{'='*65}{RESET}")
    print(f"{BOLD} 🔥  MR. TORTURE — POLYMARKET ACIMA SIZ TARAMA  🔥{RESET}")
    print(f"{BOLD}{'='*65}{RESET}\n")

    client = PolymarketClient()
    torture = MrTortureAgent()

    logger.info(f"Polymarket'tan aktif marketler çekiliyor (min hacim: ${min_vol:,.0f})...")
    markets = await client.get_active_markets(min_volume=min_vol)

    if not markets:
        logger.error("Market bulunamadı. API erişimini kontrol et.")
        return

    # Hacme göre sırala ve limitle
    markets = sorted(markets, key=lambda m: float(m.get("volume", 0) or 0), reverse=True)
    markets = markets[:limit]

    logger.info(f"{len(markets)} market analiz edilecek...\n")

    results = []
    for i, market in enumerate(markets, 1):
        question   = market.get("question", "?")
        price      = float(market.get("best_ask", 0.5) or 0.5)
        volume     = float(market.get("volume", 0) or 0)
        end_date   = market.get("end_date_iso", "?")

        print(f"  [{i:02d}/{len(markets)}] {question[:60]}...")

        # Mr. Torture için bağlam paketi
        asset_data = {
            "name": question,
            "details": {
                "current_yes_price": price,
                "implied_probability_pct": f"{price*100:.1f}%",
                "volume_usd": f"${volume:,.0f}",
                "end_date": end_date,
                "market_type": "Polymarket prediction market",
            }
        }
        agents_snapshot = {
            "MarketPrice": {
                "implied_prob": price,
                "note": "Piyasanın bu olaya verdiği olasılık"
            }
        }

        result = await torture.evaluate(asset_data, agents_snapshot)
        results.append((market, result))

    # ── SONUÇLARI YAZDIR ────────────────────────────────────────────────
    print(f"\n\n{BOLD}{'='*65}{RESET}")
    print(f"{BOLD} MR. TORTURE'UN GADDAR RAPORLARI{RESET}")
    print(f"{BOLD}{'='*65}{RESET}\n")

    buy_count  = sum(1 for _, r in results if r and r.get("decision") == "BUY")
    sell_count = sum(1 for _, r in results if r and r.get("decision") == "SELL")
    hold_count = sum(1 for _, r in results if r and r.get("decision") == "HOLD")
    fail_count = sum(1 for _, r in results if not r)

    for market, result in results:
        q      = market.get("question", "?")
        price  = float(market.get("best_ask", 0) or 0)
        volume = float(market.get("volume", 0) or 0)

        print(f"\n{BOLD}▶ {q}{RESET}")
        print(f"  Fiyat: {price:.2f} ({price*100:.0f}% olasılık)  |  Hacim: ${volume:,.0f}")

        if result:
            d = result.get("decision", "HOLD")
            color = VERDICT_COLORS.get(d, "")
            print(f"  {color}KARAR: {d}{RESET}")
            print(f"  Yanlışlıklar : {result.get('inaccuracies', '—')}")
            print(f"  Dar Boğazlar : {result.get('bottlenecks', '—')}")
            print(f"  İyileştirme  : {result.get('improvements', '—')}")
            print(f"  Gerekçe      : {result.get('reasoning', '—')}")
        else:
            print("  ⚠️  Yanıt alınamadı.")
        print(f"  {'-'*60}")

    # ── ÖZET ────────────────────────────────────────────────────────────
    print(f"\n{BOLD}{'='*65}")
    print(f" ÖZET: {buy_count} BUY  |  {sell_count} SELL  |  {hold_count} HOLD  |  {fail_count} HATA")
    print(f"{'='*65}{RESET}\n")

    await client.session.aclose()


def main():
    parser = argparse.ArgumentParser(description="Mr. Torture — Polymarket Tarayıcı")
    parser.add_argument("--limit",   type=int,   default=20,    help="Kaç market analiz edilsin (varsayılan: 20)")
    parser.add_argument("--min-vol", type=float, default=10_000, help="Minimum hacim $ (varsayılan: 10000)")
    args = parser.parse_args()

    asyncio.run(scan(limit=args.limit, min_vol=args.min_vol))


if __name__ == "__main__":
    main()
