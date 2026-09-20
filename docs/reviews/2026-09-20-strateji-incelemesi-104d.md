# Günlük Strateji İncelemesi — 2026-09-20 (104. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = `f47c5cc` (#186 dahil, 103. turun tüm
PR'ları — #184/#185/#186 — merge edilmiş). Bu branch (`claude/brave-faraday-
pegpdk`) `origin/main` ile tam eşit başladı, açık fark yoktu.

Konteynerde çalışan bir bot instance'ı yok (`data/status.json`/
`control.json`/`positions.json` bu sandbox'ta yok, ağ erişimi de yok) —
%10 hedefine karşı gerçek zamanlı sermaye ilerlemesi bu oturumdan
doğrulanamıyor; katkı kod/strateji doğruluğu seviyesinde kalıyor.

## Baseline doğrulama
`python3 -m pytest tests/ calibration/tests execution_realism/tests
crypto_directional/tests signal_bridge/tests -q` → **1773 passed, 4
skipped** (103. turun 1769'undan +4, ara PR'ların testleri).

## Bu turda incelenen alan

103. turun "sıradaki tur için notlar" listesi (`operator_layer/`,
`agents/subagents/reviewer_agent.py`, `agents/enhanced_signals.py`,
`agents/hit_rate_tracker.py`, `agents/copytrade.py`) ele alındı, ayrıca
git geçmişinde hiç "daily review" fix commit'i olmayan dosyalar taranarak
(6-model pipeline'ın matematik bileşenleri: `edge_model.py`, `spread_model.py`,
`stoikov.py`, `monte_carlo.py`) kapsam genişletildi.

### Elenen adaylar (canlı etkisi yok veya zaten temiz)
- `agents/hit_rate_tracker.py`, `agents/copytrade.py` — sadece devre dışı
  `agents/signal_agent.py`'nin (mimari dokümanına göre Bayesian ile
  değiştirildi) bağımlıları; `agents/orchestrator.py`/`subagents/*`/
  `main.py` hiçbirinde import edilmiyor.
- `agents/enhanced_signals.py::get_options_signal()`/`get_social_signal()`
  hiçbir zaman `"max_pain"`/`"fear_greed"` anahtarı döndürmüyor, ama
  `research_agent.py`'nin okuduğu bu iki alan (`EnhancedData.max_pain`,
  `.fear_greed_index`) hiçbir yerde tüketilmiyor (grep: sadece kendi
  tanımlandığı yerde okunuyor) — ölü/etkisiz. `strategies/arbitrage_engine.py`
  ayrı bir yoldan `binance_feed.enhanced`'i doğrudan okuyor ama oradaki tüm
  boost'lar zaten kasıtlı olarak DISABLED (yorumlarla belgelenmiş, önceki
  turlarda).
- `strategies/edge_model.py::execution_cost()` (boyut-duyarlı kayma) hiç
  çağrılmıyor — canlı kod `total_cost()` (legacy, sabit kayma) kullanıyor.
  Ama `compute_bet_size()` bahisleri hep ≤$10'da tutuyor
  (`hard_max_bet=4.0`), ve `execution_cost()`'un kendi formülü ≤$10 için
  zaten `total_cost()` ile birebir aynı sonucu veriyor — fark hiçbir zaman
  tetiklenmiyor, etkisiz.
- `agents/subagents/reviewer_agent.py`, `agents/autonomous_engine.py`,
  `core/position_manager.py`, `agents/orchestrator.py`'nin bet-sizing/risk-
  budget fonksiyonları — satır satır okundu, her biri zaten önceki
  turlardan yoğun "BUG:"/fix yorumlarıyla belgelenmiş; bu turda yeni bir
  hata bulunamadı.

### Bulunan ve düzeltilen hata: `strategies/spread_model.py::SpreadModel`

`find_dislocations()` (cross-market z-score dislokasyon dedektörü,
`ArbitrageEngine.analyze()`'de `dislocations = self.spread_model.
find_dislocations(markets)` ile her döngüde çağrılıyor — "cross_market"
sinyal tipini üreten canlı yol) `record()` üzerinden çalışıyor:

```python
def record(self, market1_id, price1, market2_id, price2):
    ...
    spread = price1 - price2
    self._history[key].append(spread)          # ÖNCE ekleniyordu
    return self.z_score(market1_id, price1, market2_id, price2)  # SONRA
```

`z_score()` kendi mu/sigma'sını `self._history[key]`'den hesaplıyor — ama
`append()` zaten çağrılmış olduğu için, test edilen "current" spread
KENDİ baseline'ının (mu/sigma) bir parçası olarak hesaba katılıyordu.
Gerçek bir outlier için bu, mu'yu outlier'a doğru çekiyor VE sigma'yı
outlier'ın kendi sıçramasından şişiriyor — ikisi birden z-score'u
sistematik olarak olduğundan küçük gösteriyor.

Somut ölçüm (`tests/test_spread_model_self_inclusion.py`): 5 gürültüsüz
önceki gözlem (spread ≈0) + gerçek bir 0.20'lik dislokasyon → doğru
hesap (sadece önceki 5 gözleme göre) **z=11.509**, ama bug'lı kod
**z=2.187** döndürüyordu — 5 kata yakın küçültme. `find_dislocations()`
`min_z=1.8` eşiğini kullandığı için, bu düzeyde sistematik küçültme sınırda
gerçek dislokasyonları eşiğin altına itip sinyali hiç üretmeden
kaybettiriyordu — CLAUDE.md'nin "Min edge eşiği" felsefesiyle aynı sınıftan
ama spread-model tarafında, tespit edilmemiş bir hata.

**Düzeltme**: `record()`'da çağrı sırası tersine çevrildi —
`z_score()` önce (sadece önceki geçmişe karşı test), `append()` sonra
(gelecek çağrılar için geçmişe eklenir).

**Test**: `tests/test_spread_model_self_inclusion.py` (2 yeni test):
- `test_z_score_uses_prior_history_not_self` — gerçek bir dislokasyonun
  gürültü bandının çok dışında (>10 sigma) skorlanması gerektiğini
  doğrular. Düzeltme öncesi `git stash` ile FAIL (z sistematik küçük),
  düzeltme sonrası PASS.
- `test_z_score_matches_manual_prior_only_computation` — dönen z-score'un
  elle hesaplanmış, sadece-önceki-geçmiş mu/sigma'sıyla birebir eşleştiğini
  doğrular (regresyon-karşıtı, formülün kendisini kilitler). Düzeltme
  öncesi FAIL (`2.187 != 11.509`), düzeltme sonrası PASS.

### Tam suite
- Düzeltme öncesi (baseline): **1773 passed, 4 skipped**.
- Düzeltme sonrası: **1775 passed, 4 skipped** (+2 yeni test, 0 regresyon).

## Sermaye/performans notu
Bu oturumda çalışan bir bot instance'ı yok — %10 hedefine karşı gerçek
ilerleme doğrulanamıyor. Bu turda bulunan hata, cross-market spread
dislokasyon dedektörünün canlı yolda sistematik olarak konservatif
davranmasına (bazı gerçek fırsatları kaçırmasına) neden oluyordu — yönü
"daha az ama isabetsiz trade" değil "bazı isabetli trade'lerin hiç
üretilmemesi" idi, yani düzeltme risk azaltmaktan çok fırsat kaybını
gideriyor.

## Sonuç
`strategies/spread_model.py::SpreadModel.record()`'daki z-score
self-inclusion hatası düzeltildi ve iki regresyon testiyle doğrulandı
(düzeltme öncesi FAIL, sonrası PASS, tam suite +2/-0).

## Sıradaki tur için notlar
- `strategies/quality_filter.py`, `strategies/orderbook_analyzer.py`,
  `strategies/sum_monitor.py`, `strategies/bond_scanner.py`,
  `strategies/walk_forward.py`, `strategies/maker_engine.py`,
  `agents/latency_arb.py`, `agents/binance_feed.py`,
  `agents/market_index_watcher.py`, `agents/context_fetcher.py` — hâlâ
  git geçmişinde bir "daily review" fix commit'i yok; bu turda kapsam
  dışı kaldı (zaman kısıtı), sıradaki turun adayları.
- `strategies/spread_model.py::find_dislocations()` — aynı horizon'daki
  farklı zaman dilimlerindeki (örn. 8:00-8:05 vs 8:10-8:15 BTC) marketlerin
  fiyat spread'ini karşılaştırıyor; bu iki market bağımsız olaylar olduğu
  için ("spread mean-reverting olmalı" varsayımı altta yatan olasılıkların
  gerçekten benzer olduğunu varsayıyor) stratejinin kendisinin mimari
  sağlamlığı bu turda sorgulanmadı — sadece z-score hesabının matematiksel
  doğruluğu düzeltildi. Büyük bir mimari karar olacağından kullanıcı onayı
  olmadan değiştirilmedi.
- `strategies/stoikov.py::maker_quotes()`/`self.stoikov` — `MakerEngine`
  içinde kullanılıyor ama `DEFAULT_POOLS = {"maker": 0.00, ...}` ("DIRECTIONAL
  ONLY mode") nedeniyle şu anki canlı konfigürasyonda muhtemelen sıfır
  sermaye ile çalışıyor — düşük öncelik, `MAKER_CAPITAL_PCT` gerçekten
  sıfırdan farklı ayarlanırsa tekrar değerlendirilmeli.
