"""
24 saatlik paper run raporu.
Gercek emir atmaz; her market icin sinyal analizi yapar ve ozet istatistik uretir.

Calistir:
  python paper_run_report.py
"""
import asyncio
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

from dotenv import load_dotenv
from loguru import logger

load_dotenv()
os.environ.setdefault("POLYMARKET_PRIVATE_KEY", "")
os.environ.setdefault("POLYMARKET_WALLET_ADDRESS", "")
logger.remove()
logger.add(sys.stderr, level="WARNING")   # gürültüyü kapat, sadece özet göster

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

MIN_EDGE   = float(os.getenv("MIN_EDGE_THRESHOLD", 0.04))
MAX_HOURS  = float(os.getenv("MAX_HOURS_TO_CLOSE", 24.0))


def _read_capital() -> float:
    """positions.json'dan capital oku. CLI arg ile override edilebilir."""
    import argparse, json as _json, pathlib
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--capital", type=float, default=None)
    args, _ = parser.parse_known_args()
    if args.capital is not None:
        return args.capital
    try:
        p = pathlib.Path(__file__).parent / "data" / "positions.json"
        return float(_json.loads(p.read_text())["capital"])
    except Exception:
        return 1.0


CAPITAL = _read_capital()


def _hours_to_close(market: dict):
    end = market.get("endDate") or market.get("endDateIso", "")
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
    engine.min_edge = MIN_EDGE

    print(f"\n{'='*70}")
    print(f"24H PAPER RUN RAPORU — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")

    # Tum aktif marketleri cek
    markets = await client.get_active_markets(min_volume=0)
    print(f"\nGamma API          : {len(markets)} aktif market")

    # Pre-filter
    candidates = []
    reject_keyword = reject_time = reject_price = 0
    for m in markets:
        q = m.get("question", "").lower()
        if not any(k in q for k in KEYWORDS):
            reject_keyword += 1
            continue
        h = _hours_to_close(m)
        if h is None or h > MAX_HOURS:
            reject_time += 1
            continue
        price = float(m.get("best_ask", 0) or 0)
        if not (0.05 <= price <= 0.95):
            reject_price += 1
            continue
        candidates.append(m)

    print(f"Crypto up/down     : {len(candidates) + reject_time} bulundu")
    print(f"  -> 0-24h, fiyat filtresi geciyor : {len(candidates)}")
    print(f"  -> Atilan (sure >24h veya bitti) : {reject_time}")
    print(f"  -> Atilan (fiyat <0.05 / >0.95)  : {reject_price}")

    if not candidates:
        print("\nAday market yok — rapor tamamlanamadi.")
        return

    # ArbitrageEngine analiz
    print(f"\nArbitrageEngine analiz ediliyor ({len(candidates)} market)...")
    signals = await engine.analyze(candidates, capital=CAPITAL)

    # --- Istatistikler ---
    yes_signals  = [s for s in signals if s.direction == "YES"]
    no_signals   = [s for s in signals if s.direction == "NO"]
    edges        = [s.edge for s in signals]
    sizes        = [s.size for s in signals]

    reject_count = len(candidates) - len(signals)

    # Ret nedeni analizi (engine icinde filtrelenen)
    # Reddi anlamak icin kaba olasılik: edge < MIN_EDGE veya capital yetersiz
    by_asset: dict[str, list] = defaultdict(list)
    for s in signals:
        q = s.market.get("question", "").lower()
        for kw in ["bitcoin", "btc", "ethereum", "eth", "solana", "sol",
                   "xrp", "dogecoin", "doge", "bnb", "hyperliquid", "hype"]:
            if kw in q:
                asset = kw.upper()
                by_asset[asset].append(s)
                break

    print(f"\n{'-'*70}")
    print("SINYAL OZETI")
    print(f"{'-'*70}")
    print(f"  Analiz edilen    : {len(candidates)}")
    print(f"  Sinyal gecti     : {len(signals)}")
    print(f"  Reddedilen       : {reject_count}  (edge < {MIN_EDGE} veya capital yetersiz)")
    print(f"  YES sinyali      : {len(yes_signals)}")
    print(f"  NO sinyali       : {len(no_signals)}")
    if edges:
        print(f"  Edge min/ort/max : {min(edges):.4f} / {sum(edges)/len(edges):.4f} / {max(edges):.4f}")
        print(f"  Size min/ort/max : ${min(sizes):.2f} / ${sum(sizes)/len(sizes):.2f} / ${max(sizes):.2f}")
        print(f"  Toplam allokasyon: ${sum(sizes):.2f} / ${CAPITAL:.2f} kapital")

    print(f"\n{'-'*70}")
    print("VARLIK BAZLI DAGILIM")
    print(f"{'-'*70}")
    for asset, sigs in sorted(by_asset.items()):
        directions = [s.direction for s in sigs]
        avg_edge   = sum(s.edge for s in sigs) / len(sigs)
        print(f"  {asset:<12}: {len(sigs):2} sinyal  "
              f"YES={directions.count('YES')} NO={directions.count('NO')}  "
              f"ort_edge={avg_edge:.4f}")

    if signals:
        print(f"\n{'-'*70}")
        print("TOP 10 SINYAL")
        print(f"{'-'*70}")
        for i, sig in enumerate(signals[:10], 1):
            print(
                f"  {i:2}. [{sig.direction}] {sig.market.get('question','')[:52]}"
            )
            print(
                f"       Bayesian={sig.bayesian_prob:.3f}  Price={sig.market_price:.3f}  "
                f"Edge={sig.edge:.4f}  Kelly=${sig.size:.2f}"
            )

    print(f"\n{'-'*70}")
    print("RED DAGILIMI (tahmin)")
    print(f"{'-'*70}")
    # Analiz icin: tum adayları manuel gectir
    near50 = sum(1 for m in candidates if abs(float(m.get("best_ask", 0.5) or 0.5) - 0.5) < 0.08)
    extreme = sum(1 for m in candidates if float(m.get("best_ask", 0) or 0) > 0.80 or float(m.get("best_ask", 0) or 0) < 0.20)
    mid = len(candidates) - near50 - extreme
    print(f"  Near-50/50 (yuksek fee ~3%)  : {near50}")
    print(f"  Mid range (0.20-0.80)         : {mid}")
    print(f"  Extreme (>0.80 / <0.20)       : {extreme}")
    print(f"  -> Reddedilen {reject_count} adayin cogu near-50/50 bolgesi")

    print(f"\n{'-'*70}")
    print("KAPITAL DURUMU")
    print(f"{'-'*70}")
    print(f"  positions.json capital : ${CAPITAL:.2f}")
    print(f"  Simule allokasyon      : ${sum(sizes):.2f}")
    print(f"  Kalan                  : ${CAPITAL - sum(sizes):.2f}")
    print(f"  NOT: Gercek USDC bakiye = CLOB balance (bot startup'ta sync eder)")

    print(f"\n{'='*70}")
    print("NOT: Bu simulasyon — gercek emir atilmadi.")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    asyncio.run(main())
