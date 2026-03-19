"""
ArbitrageEngine canli runtime kaniti.
Gercek emir atmadan sinyal analizi gosterir.

Calistir:
  python run_arb_scan.py
"""
import asyncio
import os
import sys
from datetime import datetime, timezone
from loguru import logger
from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("POLYMARKET_PRIVATE_KEY", "")
os.environ.setdefault("POLYMARKET_WALLET_ADDRESS", "")

sys.path.insert(0, os.path.dirname(__file__))

from core.polymarket_client import PolymarketClient
from strategies.arbitrage_engine import ArbitrageEngine
from agents.binance_feed import BinanceFeed

KEYWORDS = [
    "bitcoin up or down", "ethereum up or down", "solana up or down",
    "btc up or down", "eth up or down", "sol up or down", "xrp up or down",
    "dogecoin up or down", "doge up or down", "bnb up or down",
    "hyperliquid up or down", "hype up or down",
]

MAX_HOURS = float(os.getenv("MAX_HOURS_TO_CLOSE", 24.0))
MIN_HOURS = float(os.getenv("MIN_HOURS_TO_CLOSE", 0.0))
MIN_EDGE  = float(os.getenv("MIN_EDGE_THRESHOLD", 0.04))
CAPITAL   = 3.59  # positions.json'dan
SAMPLE    = int(os.getenv("ARB_SCAN_SAMPLE", 100))


def _hours_to_close(market: dict):
    end = market.get("endDate") or market.get("end_date_iso") or market.get("endDateIso")
    if not end:
        return None
    try:
        s = str(end).replace("Z", "+00:00")
        if len(s) == 10:
            s += "T23:59:00+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        h = (dt - datetime.now(timezone.utc)).total_seconds() / 3600
        return h if h > 0 else None
    except Exception:
        return None


async def main():
    client  = PolymarketClient()
    binance = BinanceFeed(session=client.session)
    engine  = ArbitrageEngine(
        http_session=client.session,
        binance_feed=binance,
        smart_trader_tracker=None,
    )

    logger.info("Gamma API'den aktif marketler cekiliyor...")
    markets = await client.get_active_markets(min_volume=0)
    logger.info(f"Toplam: {len(markets)} market")

    # Pre-filter (ayni orchestrator filtresi)
    candidates = []
    reject_no_keyword   = 0
    reject_time         = 0
    reject_price        = 0

    for m in markets:
        q = m.get("question", "").lower()
        if not any(k in q for k in KEYWORDS):
            reject_no_keyword += 1
            continue
        h = _hours_to_close(m)
        if h is None or h > MAX_HOURS or h < MIN_HOURS:
            reject_time += 1
            continue
        price = float(m.get("best_ask", 0) or 0)
        if not (0.05 <= price <= 0.95):
            reject_price += 1
            continue
        candidates.append(m)

    logger.info(
        f"On-filtre: {len(candidates)} aday "
        f"(atilan: keyword={reject_no_keyword}, sure={reject_time}, fiyat={reject_price})"
    )

    # Sample for speed
    sample = candidates[:SAMPLE]
    logger.info(f"{len(sample)} market analiz ediliyor (min_edge={MIN_EDGE})...")

    signals = await engine.analyze(sample, capital=CAPITAL)

    yes_count = sum(1 for s in signals if s.direction == "YES")
    no_count  = sum(1 for s in signals if s.direction == "NO")

    print()
    print("=" * 70)
    print("ARBITRAGE ENGINE - RUNTIME KANITI")
    print(f"  Zaman         : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Toplam market : {len(markets)}")
    print(f"  Crypto aday   : {len(candidates)}")
    print(f"  Analiz edilen : {len(sample)}")
    print(f"  Sinyal uretildi: {len(signals)}")
    print(f"  YES / NO      : {yes_count} / {no_count}")
    print("=" * 70)

    if signals:
        print("TOP SINYALLER:")
        for i, sig in enumerate(signals[:10], 1):
            print(
                f"  {i:2}. [{sig.direction}] {sig.market.get('question', '')[:55]}"
            )
            print(
                f"       Bayesian={sig.bayesian_prob:.3f} vs Price={sig.market_price:.3f} "
                f"| Edge={sig.edge:.4f} | ${sig.size:.2f} | Z={sig.z_score:.1f}"
            )
            print(f"       {sig.reasoning}")
    else:
        print("Minimum edge esigi gecen sinyal yok.")
        print("Muhtemel nedenler:")
        print(f"  - Tum marketler near-50/50 (yuksek dinamik fee: ~3%)")
        print(f"  - Binance spot verisi neyral — Bayesian prior'i degistirmiyor")
        print(f"  - Min edge esigi: {MIN_EDGE} (ENV: MIN_EDGE_THRESHOLD)")

    print()
    print("NOT: Bu simulasyon — gercek emir atilmadi.")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
