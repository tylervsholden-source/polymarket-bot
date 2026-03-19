"""
Canli market tarama — gercek emir atmadan sinyal analizi.
ON-FILTRELEME ile hizli tarama:
  1. Tum aktif marketleri cek (API)
  2. On-filtre: son 24h kapaniyor + fiyat 0.35-0.65 arasi
  3. Sadece on-filtreden gecelere AI cagir (~10-20 market)
"""
import asyncio
import os
from datetime import datetime, timezone
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

from agents.signal_agent import SignalAgent
from agents.whale_tracker import WhaleTracker
from core.polymarket_client import PolymarketClient
from strategies.quality_filter import QualityFilter
from strategies.kelly_criterion import KellyCriterion

INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", 1000))
MAX_HOURS = float(os.getenv("MAX_HOURS_TO_CLOSE", 24.0))
PRICE_MIN = float(os.getenv("QUALITY_PRICE_MIN", 0.35))
PRICE_MAX = float(os.getenv("QUALITY_PRICE_MAX", 0.65))  # on-filtre daha dar

# Crypto keyword filtresi — sadece bu kelimeleri içeren marketler analiz edilir
_CRYPTO_KEYWORDS = [
    "bitcoin", "btc",
    "ethereum", "eth",
    "solana", "sol",
    "ripple", "xrp",
    "dogecoin", "doge",
    "bnb", "binance",
    "hyperliquid", "hype",
]


def is_crypto_market(market: dict) -> bool:
    """Market başlığında crypto keyword var mı?"""
    text = (market.get("question", "") + " " + market.get("description", "")).lower()
    return any(kw in text for kw in _CRYPTO_KEYWORDS)


def hours_to_close(market: dict) -> float | None:
    # endDate has full ISO with time, end_date_iso is date-only
    end_date = market.get("endDate") or market.get("end_date_iso") or market.get("endDateIso")
    if not end_date:
        return None
    try:
        s = str(end_date).replace("Z", "+00:00")
        # date-only "YYYY-MM-DD" → treat as end of day UTC
        if len(s) == 10:
            s += "T23:59:00+00:00"
        end_dt = datetime.fromisoformat(s)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)
        delta = end_dt - datetime.now(timezone.utc)
        return max(delta.total_seconds() / 3600, 0)
    except Exception:
        return None


def pre_filter(markets: list) -> list:
    """Hizli on-filtre — AI cagirmadan once."""
    result = []
    for m in markets:
        if not is_crypto_market(m):
            continue
        price = float(m.get("best_ask", 0) or 0)
        if not (PRICE_MIN <= price <= PRICE_MAX):
            continue
        h = hours_to_close(m)
        if h is None or h > MAX_HOURS or h < 0.25:
            continue
        result.append(m)
    return result


async def main():
    client = PolymarketClient()
    signal_agent = SignalAgent()
    whale_tracker = WhaleTracker()
    quality_filter = QualityFilter()
    kelly = KellyCriterion()

    logger.info("Aktif marketler cekiliyor...")
    markets = await client.get_active_markets(min_volume=1000)
    logger.info(f"{len(markets)} aktif market bulundu.")

    candidates = pre_filter(markets)
    logger.info(f"On-filtre sonrasi: {len(candidates)} market AI analizine giriyor.")

    if not candidates:
        logger.warning("Hicbir market on-filtreyi gecemedi.")
        print("\nBugün uygun market yok. MAX_HOURS veya fiyat araligini genisleterek tekrar dene.")
        return

    passed = []
    filtered_reasons: list[dict] = []

    for i, market in enumerate(candidates, 1):
        q = market.get("question", "")[:60]
        price = float(market.get("best_ask", 0))
        h = hours_to_close(market) or 0

        logger.info(f"[{i}/{len(candidates)}] {q}  price={price:.2f}  kapanisa={h:.1f}h")

        try:
            whale_data = await whale_tracker.get_activity(market["condition_id"])
            signal = await signal_agent.analyze(market, whale_data)
            if signal is None:
                filtered_reasons.append({"q": q, "reason": "AI sinyal yok", "price": price})
                continue

            ai_prob = signal["probability"]
            edge = ai_prob - price
            confidence = signal.get("confidence", "?")

            ok, reason = quality_filter.check(market, whale_data, signal)

            if ok:
                size = kelly.position_size(edge=edge, price=price, capital=INITIAL_CAPITAL)
                passed.append({
                    "question": q,
                    "price": price,
                    "ai_prob": ai_prob,
                    "edge": round(edge, 3),
                    "confidence": confidence,
                    "whale": whale_data.get("whale_alignment", "?"),
                    "hours": round(h, 1),
                    "size": round(size, 2),
                    "reasoning": signal.get("reasoning", "")[:120],
                })
                logger.success(
                    f"  GECTI | edge={edge:.2f} conf={confidence} "
                    f"whale={whale_data.get('whale_alignment')} size=${size:.2f}"
                )
            else:
                filtered_reasons.append({"q": q, "reason": reason, "price": price, "edge": round(edge, 3)})
                logger.debug(f"  REDDEDILDI: {reason}")

        except Exception as e:
            logger.error(f"  Hata: {e}")
            filtered_reasons.append({"q": q, "reason": str(e)[:60], "price": price})

    # ── Ozet raporu ──────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("CANLI MARKET TARAMA SONUCLARI")
    print(f"  Toplam aktif    : {len(markets)}")
    print(f"  On-filtre gecen : {len(candidates)}  (0-{MAX_HOURS:.0f}h, fiyat {PRICE_MIN}-{PRICE_MAX})")
    print(f"  AI analiz yapilan: {len(candidates)}")
    print(f"  Filtreden gecen : {len(passed)}")
    print("=" * 70)

    if passed:
        print(f"\nALINACAK FIRSAT ({len(passed)} adet):")
        print("-" * 70)
        total_alloc = 0
        for t in sorted(passed, key=lambda x: -x["edge"]):
            total_alloc += t["size"]
            print(f"  {t['question']}")
            print(
                f"    Fiyat={t['price']:.2f} | AI={t['ai_prob']:.2f} | Edge={t['edge']:.2f} "
                f"| {t['confidence']} | Whale={t['whale']} | Kapanisa={t['hours']}h"
            )
            print(f"    Pozisyon: ${t['size']:.2f}  |  Gerekce: {t['reasoning']}")
            print()
        print(f"  TOPLAM ALLOKASYON: ${total_alloc:.2f} / ${INITIAL_CAPITAL:.2f}")
    else:
        print("\nBugun icin uygun market bulunamadi.")

    if filtered_reasons:
        print(f"\nREDDEDILEN — NEDENLER ({len(filtered_reasons)} market):")
        reasons: dict[str, int] = {}
        for f in filtered_reasons:
            key = f["reason"].split(":")[0]
            reasons[key] = reasons.get(key, 0) + 1
        for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"  {count:3d}x  {reason}")

    print("=" * 70)
    print("NOT: Bu simulasyon — gercek emir atilmadi.")


asyncio.run(main())
