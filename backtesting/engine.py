"""
Backtest Engine - Gecmis crypto up/down marketlerle paper-trading simuelasyonu

METODOLOJI NOTU (kritik):
  Bu bir paper-trading replay simuelasyonudur, gercek backtest degildir.

  BIAS UYARILARI:
    1. ENDPOINT PRICE BIAS: entry = kapanisa yakin bestAsk. Optimistic tahmin.
    2. AI KALDIRILDI: Kapali marketi bugun Claude ile analiz etmek look-ahead bias.
    3. WHALE = SIMUELASYON: Gercek arsiv yok.
    4. CANDLE VERISI YOK: Bayesian neutral prior kullanilir (rsi=50, change=0).

  GERCEK DOGRULAMA:
    Botu simuelasyon modunda calistir, gercek zaman kararlarini logdan izle.
"""
import asyncio, json, random
from loguru import logger
import httpx
from strategies.bayesian import BayesianEstimator
from strategies.kelly_criterion import KellyCriterion

GAMMA_API = "https://gamma-api.polymarket.com"
CRYPTO_KW = [
    "bitcoin up or down", "btc up or down", "ethereum up or down",
    "eth up or down", "solana up or down", "sol up or down",
    "xrp up or down", "dogecoin up or down", "doge up or down", "bnb up or down",
]

class BacktestEngine:
    def __init__(self, days=20, initial_capital=100.0, seed=42):
        self.days = days
        self.capital = initial_capital
        self.initial_capital = initial_capital
        self.bayesian = BayesianEstimator()
        self.kelly = KellyCriterion()
        self.trades: list = []
        self.skipped: list = []
        random.seed(seed)

    async def run(self):
        logger.info(f"Backtest basladi: {self.days} gun | ${self.initial_capital}")
        logger.warning("BIAS: entry=endpoint-price, whale=sim, AI=YOK, candle=neutral")
        markets = await self._fetch_closed_markets()
        logger.info(f"{len(markets)} kapali crypto up/down market.")
        for m in markets[:80]:
            await self._simulate_trade(m)
        self._print_results()

    async def _fetch_closed_markets(self):
        async with httpx.AsyncClient(timeout=20) as s:
            try:
                r = await s.get(f"{GAMMA_API}/markets",
                    params={"closed":"true","limit":400,"order":"closedTime","ascending":"false"})
                r.raise_for_status()
                all_m = r.json()
            except Exception as e:
                logger.error(f"Fetch hatasi: {e}"); return []
        result = []
        for m in all_m:
            q = str(m.get("question","")).lower()
            if not any(kw in q for kw in CRYPTO_KW): continue
            vol = float(m.get("volumeNum") or m.get("volume") or 0)
            if vol < 5000: continue
            rp = m.get("outcomePrices") or []
            if isinstance(rp, str):
                try: rp = json.loads(rp)
                except: continue
            if not rp: continue
            try: yf = float(rp[0])
            except: continue
            if not (yf > 0.9 or yf < 0.1): continue
            result.append(m)
        return result

    def _get_direction_and_entry(self, market, yes_final):
        best_ask = float(market.get("bestAsk") or market.get("best_ask") or 0)
        if 0.05 < best_ask < 0.95:
            yes_price = best_ask
        else:
            yes_price = round(random.uniform(0.55,0.82) if yes_final>0.9 else random.uniform(0.18,0.45), 2)
        # Neutral Bayesian (no historical candle data)
        bayes = self.bayesian.estimate(yes_price, 0.0, 0.01, 0.0, 50.0, 1.0)
        bp = bayes.probability
        no_price = round(1.0 - yes_price, 4)
        ye = bp - yes_price
        ne = (1.0 - bp) - no_price
        if ye >= ne and ye >= 0.04:
            return ("YES", yes_price)
        elif ne > ye and ne >= 0.04:
            return ("NO", no_price)
        return None

    async def _simulate_trade(self, market):
        rp = market.get("outcomePrices") or []
        if isinstance(rp, str):
            try: rp = json.loads(rp)
            except: return
        try: yes_final = float(rp[0])
        except: return
        r = self._get_direction_and_entry(market, yes_final)
        if r is None:
            self.skipped.append({"reason":"no_edge"}); return
        direction, entry = r
        won = (direction=="YES" and yes_final>0.9) or (direction=="NO" and yes_final<0.1)
        resolved = 1.0 if won else 0.0
        edge = max(0.04, abs(resolved - entry) - 0.01)
        size = self.kelly.position_size(edge=edge, price=entry, capital=self.capital)
        if size < 1.0 or self.capital < 1.0:
            self.skipped.append({"reason":"size_too_small"}); return
        shares = size / entry
        pnl = (resolved - entry) * shares
        self.capital += pnl
        self.trades.append({
            "q": str(market.get("question",""))[:55],
            "dir": direction, "entry": round(entry,4),
            "yes_final": round(yes_final,3), "size": round(size,2),
            "pnl": round(pnl,2), "capital": round(self.capital,2), "won": pnl > 0,
        })

    def _print_results(self):
        pnl = self.capital - self.initial_capital
        wins = [t for t in self.trades if t["won"]]
        wr = len(wins)/len(self.trades)*100 if self.trades else 0
        yes_n = sum(1 for t in self.trades if t["dir"]=="YES")
        no_n  = sum(1 for t in self.trades if t["dir"]=="NO")
        skip_no_edge = sum(1 for s in self.skipped if s.get("reason")=="no_edge")
        skip_size    = sum(1 for s in self.skipped if s.get("reason")=="size_too_small")
        logger.info("=" * 55)
        logger.info(f"BACKTEST: bias=endpoint-price | AI=NONE | whale=sim")
        logger.info(f"  Baslangic : ${self.initial_capital:.2f}")
        logger.info(f"  Son       : ${self.capital:.2f}")
        logger.info(f"  PnL       : ${pnl:.2f} ({pnl/self.initial_capital*100:.1f}%)")
        logger.info(f"  Islemler  : {len(self.trades)} | Win: {wr:.1f}%")
        logger.info(f"  YES/NO    : {yes_n}/{no_n}")
        logger.info(f"  Skip      : no_edge={skip_no_edge} size={skip_size}")
        if self.trades:
            best  = max(self.trades, key=lambda x: x["pnl"])
            worst = min(self.trades, key=lambda x: x["pnl"])
            logger.info(f"  Best      : ${best['pnl']:.2f} {best['q']}")
            logger.info(f"  Worst     : ${worst['pnl']:.2f} {worst['q']}")
        logger.info("=" * 55)
        logger.warning("Gercek dogrulama: botu sim modunda calistir.")
