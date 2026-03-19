"""
Kapsamlı sistem testi — bot çalıştırmadan önce her şeyi doğrula.
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

PASS = "PASS"
FAIL = "FAIL"
results = []


def check(name, passed, detail=""):
    status = PASS if passed else FAIL
    results.append((status, name, detail))
    icon = "OK" if passed else "XX"
    print(f"  [{icon}] {name}: {detail}")


# ─────────────────────────────────────────
# TEST 1: Temel importlar
# ─────────────────────────────────────────
print("\n=== TEST 1: Import kontrolü ===")
try:
    from core.polymarket_client import PolymarketClient
    check("PolymarketClient import", True)
except Exception as e:
    check("PolymarketClient import", False, str(e))

try:
    from core.position_manager import PositionManager
    check("PositionManager import", True)
except Exception as e:
    check("PositionManager import", False, str(e))

try:
    from agents.orchestrator import Orchestrator
    check("Orchestrator import", True)
except Exception as e:
    check("Orchestrator import", False, str(e))

try:
    from strategies.arbitrage_engine import ArbitrageEngine
    check("ArbitrageEngine import", True)
except Exception as e:
    check("ArbitrageEngine import", False, str(e))

try:
    from strategies.bayesian import BayesianEstimator
    from strategies.edge_model import EdgeModel
    from strategies.spread_model import SpreadModel
    from strategies.stoikov import StoikovExecutor
    from strategies.monte_carlo import MonteCarloSimulator
    check("Tüm 6 model import", True)
except Exception as e:
    check("Tüm 6 model import", False, str(e))

# ─────────────────────────────────────────
# TEST 2: .env ve config
# ─────────────────────────────────────────
print("\n=== TEST 2: Konfigürasyon ===")
from dotenv import load_dotenv
load_dotenv()

live_enabled = os.getenv("LIVE_TRADING_ENABLED", "false")
check("LIVE_TRADING_ENABLED set", live_enabled == "true", live_enabled)

crypto_only = os.getenv("CRYPTO_UPDOWN_ONLY", "false")
check("CRYPTO_UPDOWN_ONLY=true", crypto_only == "true", crypto_only)

max_hours = os.getenv("MAX_HOURS_TO_CLOSE", "")
check("MAX_HOURS_TO_CLOSE=1.0", float(max_hours) <= 1.0, max_hours)

moonshot = os.getenv("MOONSHOT_ENABLED", "true")
check("MOONSHOT_ENABLED=false (kapalı)", moonshot == "false", moonshot)

# control.json
try:
    with open("data/control.json") as f:
        ctrl = json.load(f)
    check("control.json okundu", True, str(ctrl))
    # Bot şu an çalışmıyor — simulation_running önemli değil ama live_trading durumu
    check("control.json geçerli format", "live_trading" in ctrl, str(ctrl))
except Exception as e:
    check("control.json", False, str(e))

# positions.json
try:
    with open("data/positions.json") as f:
        pos = json.load(f)
    open_count = len(pos.get("positions", {}))
    capital = pos.get("capital", 0)
    check("positions.json okundu", True, f"capital={capital}, açık pos={open_count}")
except Exception as e:
    check("positions.json", False, str(e))

# ─────────────────────────────────────────
# TEST 3: Bayesian model
# ─────────────────────────────────────────
print("\n=== TEST 3: Bayesian Model ===")
from strategies.bayesian import BayesianEstimator
b = BayesianEstimator()

# Nötr sinyal -> prior korunmalı
est = b.estimate(market_price=0.50, spot_change_pct=0.0, volatility=0.01, order_book_imbalance=0.0)
check("Nötr sinyal -> prob ~ 0.50", abs(est.probability - 0.50) < 0.05,
      f"prior=0.50 -> prob={est.probability}")

# Güçlü bullish sinyal -> prob artmalı
est2 = b.estimate(market_price=0.50, spot_change_pct=0.5, volatility=0.01, order_book_imbalance=0.3)
check("Bullish sinyal -> prob > 0.50", est2.probability > 0.50,
      f"spot=+0.5%, OB=+0.3 -> prob={est2.probability}")

# Güçlü bearish sinyal -> prob azalmalı
est3 = b.estimate(market_price=0.50, spot_change_pct=-0.5, volatility=0.01, order_book_imbalance=-0.3)
check("Bearish sinyal -> prob < 0.50", est3.probability < 0.50,
      f"spot=-0.5%, OB=-0.3 -> prob={est3.probability}")

# Sınır testi: prob [0.05, 0.95] arasında kalmalı
est4 = b.estimate(market_price=0.50, spot_change_pct=10.0, volatility=0.001, order_book_imbalance=1.0)
check("Prob sınır testi [0.05, 0.95]", 0.05 <= est4.probability <= 0.95,
      f"prob={est4.probability}")

# ─────────────────────────────────────────
# TEST 4: Edge Model
# ─────────────────────────────────────────
print("\n=== TEST 4: Edge Model ===")
from strategies.edge_model import EdgeModel
em = EdgeModel()

# YES+NO=0.95 -> pozitif edge
e1 = em.single_market_edge(0.48, 0.47)
check("Single-market arb tespiti (0.48+0.47=0.95)", e1 > 0, f"edge={e1:.4f}")

# YES+NO=1.02 -> negatif edge (no arb)
e2 = em.single_market_edge(0.55, 0.47)
check("No arb (0.55+0.47=1.02)", e2 < 0, f"edge={e2:.4f}")

# Cross-market: Bayesian=0.65, market=0.50 -> pozitif
e3 = em.cross_market_edge(0.65, 0.50)
check("Cross-market edge pozitif", e3 > 0, f"edge={e3:.4f}")

check("has_edge(0.05) True", em.has_edge(0.05, min_edge=0.04), "")
check("has_edge(0.03) False", not em.has_edge(0.03, min_edge=0.04), "")

# ─────────────────────────────────────────
# TEST 5: Spread Model
# ─────────────────────────────────────────
print("\n=== TEST 5: Spread Model ===")
from strategies.spread_model import SpreadModel
sm = SpreadModel(window=10, min_z=1.5)

# Tarihsel veri yükle
for i in range(8):
    sm.record("m1", 0.50 + i * 0.001, "m2", 0.50)

# Normal spread -> z-score threshold'un (1.5) altinda olmali
z_normal = sm.z_score("m1", 0.504, "m2", 0.50)  # hafif sapma
check("Normal spread -> dusuk z-score (<2.0)", abs(z_normal) < 2.0, f"z={z_normal:.2f}")

# Aşırı sapma -> yüksek z-score
z_extreme = sm.z_score("m1", 0.60, "m2", 0.50)
check("Aşırı sapma -> yüksek z-score", abs(z_extreme) > 1.5, f"z={z_extreme:.2f}")

# ─────────────────────────────────────────
# TEST 6: Stoikov
# ─────────────────────────────────────────
print("\n=== TEST 6: Stoikov Model ===")
from strategies.stoikov import StoikovExecutor
st = StoikovExecutor(gamma=0.15)

# Sıfır inventory -> reservation price ~ mid
r = st.reservation_price(mid_price=0.50, inventory=0.0, volatility=0.02, time_remaining=0.5)
check("Sıfır inventory -> r ~ mid", abs(r - 0.50) < 0.02, f"r={r}")

# Yüksek inventory -> daha düşük fiyat teklif et (riskten kaçın)
r_high = st.reservation_price(mid_price=0.50, inventory=100.0, volatility=0.05, time_remaining=1.0)
check("Yüksek inventory -> r < mid", r_high < 0.50, f"r={r_high}")

# Sınır testi: [0.01, 0.99]
r_bound = st.reservation_price(0.50, 1000.0, 1.0, 1.0)
check("Sınır testi [0.01, 0.99]", 0.01 <= r_bound <= 0.99, f"r={r_bound}")

# ─────────────────────────────────────────
# TEST 7: Kelly
# ─────────────────────────────────────────
print("\n=== TEST 7: Kelly Criterion ===")
from strategies.kelly_criterion import KellyCriterion
k = KellyCriterion()

# Pozitif edge -> pozisyon alınmalı
size = k.position_size(edge=0.10, price=0.50, capital=100.0)
check("Pozitif edge -> size > 0", size > 0, f"${size:.2f}")

# Sıfır edge -> pozisyon yok
size0 = k.position_size(edge=0.0, price=0.50, capital=100.0)
check("Sıfır edge -> size = 0", size0 == 0, f"${size0:.2f}")

# Max cap: %20'yi geçmemeli
size_big = k.position_size(edge=0.90, price=0.50, capital=100.0)
check("Max cap %20 ($20 on $100)", size_big <= 20.01, f"${size_big:.2f}")

# ─────────────────────────────────────────
# TEST 8: Monte Carlo
# ─────────────────────────────────────────
print("\n=== TEST 8: Monte Carlo ===")
from strategies.monte_carlo import MonteCarloSimulator
mc = MonteCarloSimulator(n_simulations=500)

result = mc.simulate(edge=0.15, capital=100.0, n_trades=50, position_size_pct=0.05)
check("Monte Carlo çalıştı", True, f"WR={result.win_rate:.1%} | E[r]={result.mean_return:+.1%}")
check("Win rate hesaplandı", 0.0 <= result.win_rate <= 1.0, f"{result.win_rate:.3f}")
check("Max drawdown hesaplandı", 0.0 <= result.max_drawdown <= 1.0, f"{result.max_drawdown:.3f}")

# ─────────────────────────────────────────
# TEST 9: Market fetch
# ─────────────────────────────────────────
print("\n=== TEST 9: Market Fetch (async) ===")

async def test_market_fetch():
    client = PolymarketClient()
    try:
        markets = await client.get_active_markets()
        now = datetime.now(timezone.utc)

        updown_keywords = ["bitcoin up or down", "ethereum up or down", "solana up or down",
                           "xrp up or down", "dogecoin up or down", "bnb up or down"]

        # Sadece crypto up/down filtrele
        crypto_updown = [m for m in markets
                         if any(kw in m.get("question", "").lower() for kw in updown_keywords)]

        # Maksimum 1 saat filtrele
        within_1h = []
        for m in crypto_updown:
            end_str = m.get("endDate", "")
            try:
                s = str(end_str).replace("Z", "+00:00")
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                h = (dt - now).total_seconds() / 3600
                if 0 < h <= 1.0:
                    within_1h.append(m)
            except Exception:
                pass

        check("Toplam market > 0", len(markets) > 0, f"{len(markets)} market")
        check("Crypto up/down market > 0", len(crypto_updown) > 0, f"{len(crypto_updown)} market")
        check("1 saatlik adaylar > 0", len(within_1h) > 0, f"{len(within_1h)} market")

        # En yakın 3 adayı göster
        within_1h.sort(key=lambda m: m.get("endDate", ""))
        for m in within_1h[:3]:
            end_str = m.get("endDate", "")
            try:
                dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                h = (dt - now).total_seconds() / 3600
                print(f"     -> h={h:.2f}h | ask={float(m.get('best_ask',0)):.3f} | {m.get('question','')[:55]}")
            except Exception:
                pass

        # Kötü market türleri (golf, seçim vb.) geçiyor mu?
        bad_keywords = ["golf", "election", "nba", "nfl", "president", "trump", "biden"]
        bad_markets = [m for m in within_1h
                       if any(kw in m.get("question", "").lower() for kw in bad_keywords)]
        check("Kötü market türleri filtrelendi", len(bad_markets) == 0,
              f"{len(bad_markets)} kötü market" if bad_markets else "temiz")

    finally:
        await client.session.aclose()

asyncio.run(test_market_fetch())

# ─────────────────────────────────────────
# TEST 10: Orchestrator pre-filter
# ─────────────────────────────────────────
print("\n=== TEST 10: Orchestrator Pre-filter ===")

async def test_prefilter():
    client = PolymarketClient()
    try:
        markets = await client.get_active_markets()
        orc = Orchestrator()
        candidates = orc._pre_filter(markets)

        check("Pre-filter çalıştı", True, f"{len(candidates)} aday")
        check("Adaylar crypto up/down", all(
            any(kw in m.get("question", "").lower()
                for kw in ["bitcoin up or down", "ethereum up or down", "solana up or down",
                           "xrp up or down", "dogecoin up or down", "bnb up or down",
                           "hyperliquid up or down", "hype up or down"])
            for m in candidates
        ), f"hepsi crypto up/down" if candidates else "aday yok")

        # Fiyat aralığı kontrolü
        price_ok = all(0.05 <= float(m.get("best_ask", 0)) <= 0.95 for m in candidates)
        check("Tüm adaylar fiyat aralığında [0.05-0.95]", price_ok, "")

    finally:
        await client.session.aclose()

asyncio.run(test_prefilter())

# ─────────────────────────────────────────
# TEST 11: ArbitrageEngine sinyalleri
# ─────────────────────────────────────────
print("\n=== TEST 11: ArbitrageEngine Sinyaller ===")

async def test_engine():
    client = PolymarketClient()
    try:
        markets = await client.get_active_markets()
        orc = Orchestrator()
        candidates = orc._pre_filter(markets)

        engine = ArbitrageEngine(http_session=client.session)
        signals = await engine.analyze(candidates[:40], capital=100.0)

        check("Engine çalıştı", True, f"{len(signals)} sinyal")

        if signals:
            best = signals[0]
            check("En iyi sinyal: edge > 0", best.edge > 0,
                  f"edge={best.edge:.3f}")
            check("En iyi sinyal: bayesian_prob hesaplandı", 0.05 <= best.bayesian_prob <= 0.95,
                  f"prob={best.bayesian_prob:.3f}")
            check("En iyi sinyal: size > 0", best.size > 0, f"${best.size:.2f}")
            check("En iyi sinyal: market var", bool(best.market), best.market.get("question", "")[:50])
            check("En iyi sinyal: entry_price geçerli", 0.01 <= best.entry_price <= 0.99,
                  f"price={best.entry_price:.4f}")

            print(f"\n     En iyi 3 sinyal:")
            for s in signals[:3]:
                print(f"     -> [{s.signal_type}] {s.market['question'][:50]}")
                print(f"       B={s.bayesian_prob:.3f} | P={s.market_price:.3f} | Edge={s.edge:.3f} | ${s.size:.2f}")
        else:
            check("Sinyal üretildi", False, "0 sinyal — market bulunamadı veya edge yok")

    finally:
        await client.session.aclose()

asyncio.run(test_engine())

# ─────────────────────────────────────────
# TEST 12: Bot çalışmıyor kontrolü
# ─────────────────────────────────────────
print("\n=== TEST 12: Güvenlik Kontrolleri ===")

# control.json okunabilir mi?
try:
    with open("data/control.json") as f:
        ctrl = json.load(f)
    check("control.json okunabilir", True, str(ctrl))
except Exception as e:
    check("control.json okunabilir", False, str(e))

# positions.json temiz mi?
try:
    with open("data/positions.json") as f:
        pos_data = json.load(f)
    open_pos = pos_data.get("positions", {})
    capital = pos_data.get("capital", 0)
    check("positions.json okunabilir", True, f"capital=${capital}, açık={len(open_pos)}")
except Exception as e:
    check("positions.json okunabilir", False, str(e))

# LIVE_TRADING_ENABLED doğrulama
check("LIVE_TRADING_ENABLED kontrolü", os.getenv("LIVE_TRADING_ENABLED") == "true",
      os.getenv("LIVE_TRADING_ENABLED"))

# ─────────────────────────────────────────
# ÖZET
# ─────────────────────────────────────────
print("\n" + "=" * 60)
print("TEST ÖZETI")
print("=" * 60)
passed = sum(1 for r in results if r[0] == PASS)
failed = sum(1 for r in results if r[0] == FAIL)
print(f"  Toplam: {len(results)} test | Geçti: {passed} | Başarısız: {failed}")
print()
if failed > 0:
    print("BAŞARISIZ TESTLER:")
    for status, name, detail in results:
        if status == FAIL:
            print(f"  ✗ {name}: {detail}")
else:
    print("  Tüm testler geçti.")
print()
sys.exit(1 if failed > 0 else 0)
