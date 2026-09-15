# Günlük Strateji İncelemesi — 2026-09-15 (48. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Kapsam
Bugün açık olan #79 (47. tur, `strategies/maker_engine.py`'de
`_cancel_all_standing()`'in eşleşen emirleri fill kaydı olmadan düşürmesini
düzeltiyor) ile çakışmamak için `maker_engine.py`'ye dokunulmadı. Son ~25
turda derinlemesine incelenen dosyalar (`agents/orchestrator.py`,
`core/position_manager.py`, `strategies/arbitrage_engine.py`, vb.) tekrar
taranmadı; bunun yerine daha önce hiç değiştirilmemiş, sermaye/risk ile
ilgili dosyalara odaklanıldı: `strategies/kelly_criterion.py`,
`strategies/quality_filter.py`, `strategies/bond_scanner.py`,
`strategies/spread_model.py`, `strategies/edge_model.py`,
`strategies/bayesian.py`, `strategies/monte_carlo.py`, `strategies/stoikov.py`,
`strategies/orderbook_analyzer.py`, `strategies/sum_monitor.py`,
`agents/smart_trader_tracker.py`, `agents/kalshi_arb.py`,
`agents/latency_arb.py`, `agents/subagents/signal_agent_v2.py`,
`control_plane/*.py` (live_gate, process_lock, reentry_guard,
entry_window_guard, expiry_guard) ve `agents/market_index_watcher.py`.

Bu taramada birkaç "ölü kod" durumu da gözlemlendi (örn.
`OrderbookAnalyzer._estimate_slippage()`'in sonucu hiçbir yerde okunmuyor;
`LatencyArbEngine`'in gerçek emir verme yolu (`_find_market_for_spike`/
`_can_trade`) hiç çağrılmıyor; `SPIKE_BOOST`/`SUM_MONITOR` boost'ları
`arbitrage_engine.py` içinde bilinçli olarak devre dışı bırakılmış) — bunlar
canlı para hareketini etkilemediği için bu turun kapsamına alınmadı.
Gerçek, canlı emir yoluna bağlı ve daha önce hiç düzeltilmemiş olan
`strategies/bond_scanner.py`'de bulunan hata bu turun konusu oldu.

## Bulgu — BondScanner NO tarafı, gerçek orderbook yerine "çok iyimser" sentetik fiyat kullanıyordu (arbitrage_engine.py'de zaten düzeltilmiş aynı hata)

`strategies/arbitrage_engine.py::_evaluate_market()`, gerçek NO orderbook'u
yoksa (`market.get("no_best_ask")` boşsa) NO fiyatını sentetik olarak tahmin
eder ve bunu açıkça belgeler:

```python
# FIX-3: NO SYNTHETIC PRICING FIX — add spread estimate to avoid inflation
no_price_ask = round(1.0 - yes_price + 0.02, 4)  # was 1.0 - yes_price (too optimistic)
```

Yani `1.0 - yes_price` (YES_ask'tan NO_ask tahmini) prodüksiyonda test
edilip **"çok iyimser"** bulunmuş ve +0.02 sabit pay eklenerek düzeltilmiş —
çünkü doğru özdeşlik `NO_ask ≈ 1 - YES_bid`'dir, `1 - YES_ask` değil; YES'in
bid-ask spread'i göz ardı edildiğinde NO'nun gerçek maliyeti sistematik
olarak düşük tahmin edilir.

`strategies/bond_scanner.py::BondScanner._evaluate_market()` **aynı
sentetik-fiyat durumuna** sahiptir — `BondScanner.scan()`,
`client.get_all_active_markets()` çağırır; bu fonksiyon (yönlü tarama yolu
`get_active_markets()`'in aksine) marketlere hiçbir zaman `no_best_ask`
eklemez (`core/polymarket_client.py::get_all_active_markets()` →
`_normalize_markets()`, `no_best_ask`'ı hiç set etmiyor; bu alan yalnızca
`agents/orchestrator.py`'nin yönlü döngüsünde ayrı bir adımda dolduruluyor).
Yani `BondScanner` NO tarafı için **her zaman** yalnızca YES_ask'a dayalı bir
tahmine muhtaçtır — ama düzeltme öncesi kod, `arbitrage_engine.py`'de zaten
"çok iyimser" olduğu kanıtlanmış çıplak formülü, +0.02 payı olmadan
kullanıyordu:

```python
if yes_price <= (1.0 - self.PROB_THRESHOLD) and no_token:
    no_price_est = 1.0 - yes_price  # approximate NO price
    if self.PROB_THRESHOLD <= no_price_est <= self.MAX_PRICE:
        yld = (1.0 - no_price_est) / no_price_est
        if yld >= self.MIN_YIELD:
            return BondOpportunity(..., price=no_price_est, ...)
```

**Somut senaryo:** `yes_price = 0.03` (gerçekçi bir "NO neredeyse kesin"
piyasası). Düzeltme öncesi: `no_price_est = 1.0 - 0.03 = 0.97` —
`BondScanner`'ın kendi kabul bandının (`PROB_THRESHOLD=0.93` ile
`MAX_PRICE=0.97`) tam üst sınırında, `yld = (1-0.97)/0.97 ≈ %3.09` —
`MIN_YIELD=0.03`'ün hemen üzerinde. Scanner bunu "neredeyse kesin, %3+
getirili" bir NO fırsatı olarak raporluyor ve `Orchestrator._bond_cycle()`
bunun için `client.place_passive_order(price=0.97, ...)` ile **gerçek** bir
GTC alım emri veriyor (`agents/orchestrator.py:1293-1298`, bu turun kapsamı
dışında bırakılan ama çağrı zincirini gösteren dosya). Aynı +0.02 payı
uygulandığında `no_price_est = 0.99` çıkar — bu `MAX_PRICE`'ın (0.97) üzerinde
olduğundan fırsat tamamen reddedilir: bu denli uç bir piyasada (YES @ 0.03)
gerçek NO_ask'ın 0.97'nin altında olması pek olası değildir, tıpkı
`arbitrage_engine.py`'nin aynı durumda tespit ettiği gibi.

Bu, `BondScanner`'ın kendi 3 nokta sermaye/risk kuralından birini (min. %3
"neredeyse kesin" getiri eşiği, `strategies/bond_scanner.py` docstring'i:
"Buy YES tokens priced at 0.93-0.97 ... Return = 3-7% per trade") fiilen
delik hale getiriyor: fiyat tahmini sistematik olarak iyimser olduğundan,
gerçekte eşiği geçmeyecek (ya da negatif olabilecek) fırsatlar "geçti"
sayılıp gerçek sermaye ile emir açılıyor.

## Düzeltme
`strategies/bond_scanner.py::BondScanner._evaluate_market()`'in NO tarafı
sentetik fiyat tahminine, `arbitrage_engine.py`'nin FIX-3'ü ile birebir aynı
+0.02 konservatif pay eklendi (`no_price_est = round(1.0 - yes_price + 0.02, 4)`).
Dar kapsamlı, sadece bu tek satırı ve çevresindeki yanlış-yönlendiren
yorumları değiştiriyor (minimal fix ilkesi) — `_bond_cycle()`'ın guard'larına
veya emir/fill mantığına dokunulmadı.

## Test
`tests/test_bond_scanner_no_side_synthetic_price.py` (yeni, 2 test):
1. `yes_price=0.03` → düzeltme öncesi kodda `BondOpportunity(side="NO",
   price=0.97, ...)` döndüğü (yanlışlıkla kabul edildiği) doğrulanıyor —
   düzeltme sonrası `None` (reddedildi) bekleniyor.
2. `yes_price=0.055` → padlanmış tahminle (`0.965`) hâlâ meşru bir NO
   fırsatı bulunabildiği doğrulanıyor (fix'in gerçek fırsatları da
   engellemediğini gösteren sağlık kontrolü).

Düzeltme öncesi (mevcut `main`'e karşı doğrulandı): **2/2 test FAIL** —
ilk test `opp is None` beklerken `price=0.97` ile dolu bir `BondOpportunity`
aldı; ikinci test padlanmamış `price=0.945` aldı (beklenen `0.965` değil).

Düzeltme sonrası: `pytest tests/test_bond_scanner_no_side_synthetic_price.py -v`
→ **2 passed**.

Tam suite: `pytest tests/ -q` → **738 passed, 2 skipped, 0 failed**
(önceki turdan sonraki 736'ya bu turun 2 yeni testi eklendi, regresyon yok).
Test koşusunun `data/autonomous_state.json`'da bıraktığı yan etki commit
öncesi `git checkout --` ile geri alındı.

## Sonraki tur için not — bir dizi sinyal modülü hesaplanıyor ama hiçbir gerçek karara bağlanmıyor (bu turda düzeltilmedi, kapsam dışı)

Bu turdaki tarama sırasında, canlı `ArbitrageEngine`/`Orchestrator` yoluna
bağlı olduğu iddia edilen birkaç "akıllı" modülün aslında sonuçlarının hiç
tüketilmediği gözlemlendi:

- `strategies/orderbook_analyzer.py::_estimate_slippage()` — `slippage_5`/
  `slippage_10` alanlarını hesaplıyor (üstelik matematiği de bozuk: `fill`
  dolar cinsinden biriktirildiği için `avg_price` her zaman `1.0`'a
  sadeleşiyor) ama bu iki alan `arbitrage_engine.py`'de hiç okunmuyor —
  sadece `.tradeable`/`.imbalance` kullanılıyor.
- `agents/latency_arb.py::_find_market_for_spike()` / `_can_trade()` —
  modülün kendi docstring'i "spike tespit edince doğrudan emir verir" dese
  de bu iki metod kod tabanının hiçbir yerinden çağrılmıyor; sadece
  `get_spike_boost()` üzerinden pasif bir Bayesian ipucu üretiliyor, o da
  `arbitrage_engine.py`'de bilinçli olarak devre dışı (`# FIX: SPIKE boost
  DISABLED`).
- `agents/subagents/research_agent.py::_extract_global_indices()` —
  `market_watcher.get("sp500", 0.0)` çağırıyor ama `MarketIndexWatcher`'ın
  `get()` metodu yok (`AttributeError`, `except Exception: return {}` ile
  sessizce yutuluyor) — sonuçta `ResearchResult.global_indices` her zaman
  boş dict, ve zaten hiçbir yerde okunmuyor.

Bunların hiçbiri şu an gerçek para hareketini etkilemiyor (tükettikleri
alanlar başka hiçbir yerde okunmadığı için), bu yüzden bu turun "somut,
gerçekleşebilir senaryo" barını geçmiyorlar — ama kod tabanında "bağlı
görünüp aslında bağlı olmayan" modül sayısı artıyor (44. turun MakerEngine
fill-reconciliation notuyla aynı aile). İleride biri bu alanları gerçekten
bir karara bağlarsa (örn. slippage tahminini Kelly boyutlandırmaya sokmak),
önce `_estimate_slippage()`'in matematiğinin düzeltilmesi gerekecek.
