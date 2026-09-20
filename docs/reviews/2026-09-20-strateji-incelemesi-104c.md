# Günlük Strateji İncelemesi — 2026-09-20 (104. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = `f47c5cc` (#184/#185/#186 hepsi merge edilmiş
— 103. turun üç paralel oturumu: docs-only 103, web_server merge-order fix
103b, ve sim-mode risk-budget ceiling fix). Bu branch (`claude/brave-
faraday-97q4wu`) `origin/main` ile tam eşit başladı, açık farkı yoktu.

Konteynerde çalışan bir bot instance'ı yok (`data/status.json`/
`control.json`/`positions.json` bu sandbox'ta yok, ağ erişimi canlı
Polymarket/Anthropic API'lerine değil) — %10 hedefine karşı gerçek zamanlı
sermaye ilerlemesi bu oturumdan doğrulanamıyor. Katkı kod/strateji
doğruluğu seviyesinde kalıyor.

## Baseline doğrulama
`pip install -r requirements.txt` + `python3 -m pytest tests/
calibration/tests execution_realism/tests crypto_directional/tests
signal_bridge/tests -q` → **1773 passed, 4 skipped** (103b'nin 1771'inden
+2 — aca6b39'un risk-budget ceiling testi main'e merge olduğu için beklenen
artış).

`data/autonomous_state.json` yan etkisi (bilinen davranış) `git checkout --
data/autonomous_state.json` ile geri alındı.

## Bu turda incelenen alan

103. ve 103b. turların "sıradaki tur için notlar" bölümünün işaret ettiği,
daha önce tek tek derinlemesine taranmamış multi-agent subagent dosyaları
ele alındı: **`agents/subagents/{reviewer_agent,research_agent,
coordinator,signal_agent_v2,orderflow_agent}.py`**, **`agents/
whale_tracker.py`**, **`agents/top_trader_signal.py`**, **`control_plane/
live_gate.py`** (11-nokta gate) ve **`strategies/maker_engine.py`**'nin
pool-capital entegrasyonu.

### Satır satır okunan, hatasız çıkan alanlar
- **`agents/subagents/reviewer_agent.py`** (455 satır) — Claude API review +
  rule-based fallback tamamen okundu. `trade_number` ile eşleştirme,
  `suggested_size_pct` clamp'i, `VETO`'nun `approved` property'sinde dahil
  edilmesi (autonomous engine'in REVIEWER_VETO×0.25 dalı için) — hepsi
  önceki turlarda düzeltilmiş ve doğru duruyor. Yeni hata yok.
- **`agents/subagents/research_agent.py`** (379 satır) — whale/smart/regime
  fetch mantığı, `_extract_regime()`'in BULLISH/BEARISH→UP/DOWN mapping'i
  (91. turda düzeltilmiş) doğru. Yeni hata yok.
- **`agents/subagents/coordinator.py`** (462 satır) — PARALLEL→MERGE→
  SEQUENTIAL pipeline, COIN_LIMIT sonrası `approved_signals` filtresi,
  REDUCE'un `sig.size`'a değil `bet_size`'a post-floor uygulanması (52.
  turda düzeltilmiş) doğru. Yeni hata yok.
- **`agents/subagents/signal_agent_v2.py`** (300 satır) — `_compute_confluence()`
  ve `_detect_risk_flags()` satır satır okundu, risk flag string'leri
  (`REGIME_OVEREXTENDED`, `COUNTER_REGIME_NO/YES`, `WHALE_OPPOSITION`,
  `RSI_OVERBOUGHT/OVERSOLD`) reviewer_agent'in beklediği string'lerle
  birebir eşleşiyor. Yeni hata yok.
- **`agents/subagents/orderflow_agent.py`** (575 satır, hiçbir commit'te adı
  geçmiyor — tamamen ilk kez taranan dosya) — OBI/CVD/walls/VWAP/EMA-cross/
  HA-streak hesapları ve işaret kuralları (pozitif=bullish tutarlılığı) tek
  tek doğrulandı, `BIAS_WEIGHTS`/`TOTAL_WEIGHT` tutarlı, velocity'nin bilinçli
  olarak yön oyuna girmemesi (yorum satırında gerekçeli) doğru uygulanmış.
  Yeni hata yok.
- **`agents/whale_tracker.py`** — `research_agent.py`'nin beklediği
  `WhaleData` alan adlarıyla (`direction`, `large_buys`, `whale_alignment`
  vb.) birebir eşleşiyor; side+outcome birleşimi (yön tersine dönme hatası
  daha önce düzeltilmiş) doğru.
- **`agents/top_trader_signal.py`** — `conditionId` okuma ve side+outcome
  birleşimi (83. satırdaki yorum önceki bir düzeltmeyi belgeliyor) doğru,
  `ArbitrageEngine`'e gerçekten `top_trader=self.top_trader` ile
  bağlandığı doğrulandı.
- **`control_plane/live_gate.py`** (229 satır, 11-nokta gate) — tamamı
  okundu, `_check_readiness()` (TINY_PILOT_CANDIDATE + tazelik) dahil her
  kontrol doğru; `agents/orchestrator.py`'nin iki `check_live_gate()`
  çağrısı (satır 1106, 1393) tüm parametreleri doğru dolduruyor.
- **`strategies/maker_engine.py`** pool-capital entegrasyonu — `docs/
  architecture.md`'nin "maker gets most capital" yorumuyla (orchestrator.py
  satır 868) çelişir gibi görünen `DEFAULT_POOLS = {"maker": 0.00, "bond":
  0.00, "directional": 1.00}` aslında dosyanın kendi yorumunda da açıkça
  "DIRECTIONAL ONLY mode" olarak belgelenmiş, `MAKER_ENABLED`/`BOND_ENABLED`
  de ayrıca `"false"` varsayılanla (yorum: "KAPALI") kapalı — çift
  kilitli, kasıtlı bir devre-dışı durumu, hata değil.

### Doğrulanan, düzeltme gerektirmeyen ölü kod (103. turun açık sorularını kapatıyor)
- **`calibration/{calibrator,probability_mapper,edge_estimator,
  decision_policy}.py`** — `grep -rln` ile doğrulandı: bu dört dosya
  canlı kod tabanında (`agents/`, `strategies/`, `core/`, `main.py`)
  **hiçbir yerden import edilmiyor**; tek importer'ları `_gen_artifacts.py`
  ve `_gen_snapshot.py` (offline rapor/snapshot script'leri). Sadece
  `calibration.types` (dataclass tanımları) `agents/orchestrator.py`
  tarafından kullanılıyor. 103. turun "muhtemelen ölü kod, teyit edilmeli"
  notu kesinleşti — bu 4 dosya gerçekten canlı karara hiç girmiyor.
- **`agents/hit_rate_tracker.py`**, **`agents/copytrade.py`** — `grep -rn`
  ile doğrulandı: `agents/orchestrator.py` veya `main.py`'den hiç import
  edilmiyorlar (103b'nin listelediği iki dosya). Ölü kod, CLAUDE.md'nin
  sadelik kuralı gereği dokunulmadı.

## Yeni bulgu (düzeltilmeyen): `agents/enhanced_signals.py`'nin tüm çıktısı confluence/risk-flag/reviewer'a hiç ulaşmıyor

`EnhancedSignals` sınıfı her cycle'da gerçek dış API çağrıları yapıyor
(Deribit options, blockchain.info + CryptoCompare on-chain whale,
CryptoCompare social sentiment, OKX/Bybit/Kraken multi-exchange orderflow —
`docs/architecture.md`/CLAUDE.md'nin "ResearchAgent → whale + smart_trader +
regime + on-chain + sentiment" listesindeki "on-chain" ve "sentiment"
bacakları bu sınıf). Ama zinciri takip edince:

- `research_agent.py::get_market_context()` bu veriyi `ctx["put_call_ratio"]`,
  `ctx["max_pain"]`, `ctx["multi_exchange_imbalance"]`, `ctx["fear_greed"]`,
  `ctx["social_sentiment"]` olarak paketliyor.
- `signal_agent_v2.py`/`coordinator.py`'nin re-enrich adımı bunlardan sadece
  **`fear_greed`** ve **`social_sentiment`**'i `EnrichedSignal`'e yazıyor
  (`put_call_ratio`/`implied_vol`/`multi_exchange_imbalance` için
  `EnrichedSignal`'de alan bile yok — tamamen atılıyor).
- `EnrichedSignal.fear_greed` ve `.social_sentiment` ise `_compute_confluence()`
  ve `_detect_risk_flags()`'te (`grep -n "sig\.\(fear_greed\|social_sentiment\)"`
  → tek eşleşme, atama satırının kendisi) **hiç okunmuyor**, ve
  `to_review_summary()` (Claude reviewer'a giden prompt) da bu iki alanı
  hiç yazdırmıyor.

Sonuç: `EnhancedSignals`'ın 4 kaynağından (options/on-chain/social/multi-
exchange) tamamı — reviewer_agent'in `orderflow_agent.py` üzerinden gelen
(farklı, doğru bağlanmış) order-flow sinyalinden ayrı olarak — confluence
score'a, risk flag'lere veya Claude reviewer promptuna **sıfır** katkı
yapıyor; sadece boşa dış API çağrısı (rate-limit/gecikme riski) üretiyor.
Ayrıca `EnhancedSignals` kendi içinde de eksik: `get_options_signal()`
hiçbir zaman `"max_pain"` anahtarı döndürmüyor (sadece `pcr`/`iv`) ve
`get_social_signal()` hiçbir zaman `"fear_greed"` anahtarı döndürmüyor
(sadece `score`) — yani `research_agent.py`'nin okumaya çalıştığı
`opts.get("max_pain")`/`social.get("fear_greed")` zaten kaynağında hep
`None`; gerçek bir max-pain fiyat seviyesi veya fear&greed index'i bu
sınıfta hiç hesaplanmıyor.

**Neden bu turda düzeltilmedi**: Önceki turlarda düzeltilen "computed ama
hiç tüketilmiyor" sınıfı hatalar (ML_BOOST, edge wiring, bid_sum ceiling)
tek bir değeri zaten var olan bir yere bağlamaktan ibaretti — "olması
gereken" davranış başka bir yerde (docstring, komşu kod) zaten netti.
Burada düzeltme, `_compute_confluence()`'a 3-4 yeni ağırlıklı oy eklemeyi
(hangi ağırlık, hangi eşik?) ve `get_options_signal()`/`get_social_signal()`'e
gerçek max-pain/fear-greed hesaplaması yazmayı gerektiriyor — CLAUDE.md'nin
"Karmaşık Görevler (3+ adım): Başlamadan önce planı yaz ve onayla" kuralına
giren, canlı stratejinin sizing/veto davranışını değiştirecek bir tasarım
kararı (103b'nin onay-kuyruğu/doğrudan-emir çelişkisini aynı gerekçeyle
otonom değiştirmemesiyle aynı sınıf). Kullanıcı onayı olmadan bu turda
değiştirilmedi — sadece belgeleniyor.

## Sonuç — bu turda kod değişikliği YOK
Multi-agent subagent katmanı (`reviewer_agent`, `research_agent`,
`coordinator`, `signal_agent_v2`, `orderflow_agent`), `whale_tracker.py`,
`top_trader_signal.py`, `live_gate.py` (11-nokta gate) ve `maker_engine.py`
pool entegrasyonu satır satır incelendi; pozisyon boyutlandırma, yön, risk
gate'leme veya sermaye muhasebesini bozan **gerçek** bir canlı-karar hatası
bulunamadı. 103. turun açık bıraktığı iki ölü-kod sorusu (calibration/*,
hit_rate_tracker.py/copytrade.py) kesinleştirildi. Yeni bir mimari bulgu
(`enhanced_signals.py`'nin tamamının canlı karara sıfır etkisi) tespit
edildi ama kapsamı büyük bir tasarım kararı olduğu için değiştirilmedi.

## Tam suite
Baseline (oturum başı): **1773 passed, 4 skipped**. Bu turdan sonra da
aynı: **1773 passed, 4 skipped** (0 regresyon, 0 yeni test — kod
değişikliği yok).

## Sermaye/performans notu
Bu oturumda çalışan bir bot instance'ı yok, %10 hedefine karşı gerçek
ilerleme bu oturumdan doğrulanamıyor. Bu turun katkısı negatif/belgesel
sonuçlar: multi-agent subagent katmanının (orderflow_agent.py hariç
tamamı önceden taranmıştı) hatasız olduğu ve enhanced_signals.py'nin
tamamen etkisiz olduğu doğrulandı — ikincisi, gelecekte biri bu sinyalleri
gerçekten sizing'e bağlamaya karar verirse önce ele alınması gereken bir
temel niteliğinde.

## Sıradaki tur için notlar
- **`agents/enhanced_signals.py` → confluence/risk-flag wiring** (yukarıda
  detaylandırıldı) kullanıcı kararı gerektiren bir tasarım sorusu: bu
  sinyaller gerçekten `_compute_confluence()`/`_detect_risk_flags()`'e mi
  bağlanmalı (hangi ağırlık/eşiklerle, ve önce `get_options_signal()`/
  `get_social_signal()`'in eksik `max_pain`/`fear_greed` hesaplarının
  tamamlanması gerekiyor), yoksa sınıfın kendisi (gereksiz dış API
  çağrılarını durdurmak için) mi kaldırılmalı? Otonom olarak
  değiştirilmedi.
- `agents/subagents/*.py`, `agents/whale_tracker.py`,
  `agents/top_trader_signal.py`, `control_plane/live_gate.py`,
  `strategies/maker_engine.py` artık tamamen taranmış ve temiz — kısa
  vadede tekrar bakmak düşük getirili olur.
- `calibration/*` (calibrator/probability_mapper/edge_estimator/
  decision_policy) ve `agents/hit_rate_tracker.py`/`agents/copytrade.py`
  bu turda kesin ölü kod olarak doğrulandı — bir sonraki tur bunları
  tekrar "yeni keşif" olarak raporlamamalı.
- Hâlâ derinlemesine taranmamış adaylar: `shadow_runner/{runner,replay,
  reporting,validation}.py` (sadece shadow/paper mi besliyor yoksa canlı
  karara mı sızıyor netleştirilmeli), `strategies/stoikov.py` (maker_engine
  kullanıyor ama maker varsayılan kapalı — düşük öncelik), `agents/
  smart_trader_tracker.py`'nin kendi veri kaynağı (103b'de sadece
  orchestrator entegrasyonu kontrol edildi, kendi hesaplama mantığı değil).
