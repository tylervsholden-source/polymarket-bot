# Günlük Strateji İncelemesi — 2026-09-20 (104. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = `f47c5cc` (#186/#185/#184 merge edilmiş, 103.
turun `core/web_server.py::_send_status()` merge-sırası düzeltmesi dahil).
Bu branch (`claude/brave-faraday-otjbf3`) `origin/main` ile tam eşit
başladı, açık farkı yoktu.

Konteynerde çalışan bir bot instance'ı yok (`data/status.json`,
`data/control.json`, `data/positions.json` mevcut değil; `.env` yok; ağ
erişimi canlı Polymarket/Anthropic API'lerine değil) — %10 hedefine karşı
gerçek zamanlı sermaye ilerlemesi bu oturumdan doğrulanamıyor. Katkı,
103b'nin notlarında bırakıldığı yerden kod/strateji doğruluğu seviyesinde
devam etti.

## Baseline doğrulama
- `pip install -r requirements.txt` çalıştırıldı.
- `python3 -m pytest tests/ calibration/tests execution_realism/tests signal_bridge/tests crypto_directional/tests -q`
  → **1773 passed, 4 skipped** (103b'nin 1771'inden +2 — muhtemelen ana
  dalda 103b sonrası küçük bir ek; bu oturumda kod değişikliği yapılmadan
  önceki gerçek başlangıç durumu).
- `data/autonomous_state.json` yan etkisi (bilinen davranış) test
  çalıştırması sonrası `git checkout -- data/autonomous_state.json` ile
  geri alındı.

## Bu turda incelenen alanlar

103b'nin "sıradaki tur için notlar" bölümünün işaret ettiği adaylardan
üçü ele alındı: **`agents/subagents/reviewer_agent.py`**,
**`agents/subagents/research_agent.py`** ve (bunlara bağlı olarak)
**`agents/subagents/coordinator.py`**'nin PHASE 2/3 birleştirme mantığı;
ayrıca daha önce hiçbir turda ismen geçmemiş, gerçekten canlı sinyal
oylamasına giren **`agents/subagents/orderflow_agent.py`** (11
indikatörlük order-flow bias motoru) satır satır okundu.

### `agents/subagents/reviewer_agent.py` — satır satır okundu, hata yok
`ReviewDecision.approved` (VETO'yu da kapsıyor, autonomous_engine'in
`REVIEWER_VETO` dalına ulaşması için kasıtlı), `_parse_claude_response()`'un
`trade_number` ile eşleştirmesi (pozisyonel zip yerine), `suggested_size_pct`
clamp'i, JSON-parse-hata fallback'i, rule-based fallback (`_rule_based_review`)
— hepsi önceki turların ("FIX-A", "Unclamped >1.0" vb.) bıraktığı yorumlarla
tutarlı ve doğru çalışıyor. Yeni bir hata bulunamadı.

### `agents/subagents/research_agent.py` — satır satır okundu, `_extract_regime()` doğrulandı
`get_market_regime()`'in gerçek anahtarları (`regime`, `strength`,
`btc_4h_pct`, `eth_4h_pct` — `agents/binance_feed.py:1611`) ile
`_extract_regime()`'in okuduğu anahtarlar karşılaştırıldı: birebir uyuşuyor
(önceki bir turda düzeltilmiş "regime hep NEUTRAL kalıyordu" hatası hâlâ
düzeltilmiş durumda). Whale/smart-trader/enhanced fetch'lerinin paralel
`asyncio.gather(..., return_exceptions=True)` hata izolasyonu doğru.

### `agents/subagents/coordinator.py` — REDUCE/VETO boyut uygulama zinciri doğrulandı
`_re_enrich_signals()`'ın research context'i sinyale doğru yazdığı, ve
"52. tur" yorumunun iddia ettiği gibi `sig.size`'ın REDUCE için
önceden küçültülmediği (`agents/orchestrator.py`'nin
`apply_risk_size_multiplier()` ile `compute_bet_size()` SONRASI, tek
seferlik uyguladığı) `agents/orchestrator.py:985-997` satırlarında
doğrulandı — `_auto_size_mult` (autonomous_engine) ile
`review_decision.suggested_size_pct` (REDUCE) ayrı, çakışmayan iki çarpan
olarak uygulanıyor; çift-küçültme (double-dampening) yok. `ReviewVerdict.VETO`
→ `size_mult=min(size_mult,0.25)` dalı da STREAK_FILTER'ın önceden verdiği
SKIP'i eziyor mu diye kontrol edildi — `action != ActionType.SKIP` guard'ı
her iki dalda da (HIGH/CRITICAL risk ve REVIEWER_VETO) mevcut, SKIP
korunuyor. Hata yok.

### `agents/subagents/orderflow_agent.py` — 11-indikatörlü order-flow bias motoru, ilk kez tam okundu
Bu dosya önceki turlarda (15-88 arası, en son 88. turda) ismen geçmiş ama
104. tur talimatının önerdiği listede değildi; `signal_agent_v2.py`'nin oy
mekanizmasına 2.5 ağırlıkla giren (en yüksek ağırlıklardan biri) ve
`ORDERFLOW_OPPOSITION` risk flag'ini üreten canlı bir bileşen olduğu için
derinlemesine incelendi:

- `calc_obi`/`calc_cvd`/`calc_walls`/`calc_vwap_dev`/`calc_ema_cross`/
  `calc_ha_streak`/`calc_trade_velocity` fonksiyonları tek tek doğrulandı.
  Binance `isBuyerMaker` → `is_buy = not isBuyerMaker` dönüşümü doğru
  (maker alıcıysa taker satıcıdır → satış; ters çevirme doğru yönde).
- `compute_bias_score()`'daki ölçek sabitleri (`vwap_dev*20` → %5 sapma =
  tam sinyal, `ema_cross*50` → %2 fark = tam sinyal, `ha_streak*20` → ±5
  streak = tam sinyal) `calc_vwap_dev`/`calc_ema_cross`'un döndürdüğü
  birimlerle (yüzde puanı) tutarlı — birim uyuşmazlığı yok.
  `BIAS_WEIGHTS`/`TOTAL_WEIGHT` ağırlıklı ortalaması doğru hesaplanıyor.
  `trade_velocity`'nin yön oyuna dahil edilmemesi (önceki bir turun
  düzeltmesi, dosyanın kendi yorumunda belgeli) hâlâ doğru uygulanmış.
- `signal_agent_v2.py`'de `orderflow_bias`/`orderflow_confidence` kullanımı
  (`_calc_confluence` ağırlık 2.5, `_detect_risk_flags`'te
  `ORDERFLOW_OPPOSITION`) işaret tutarlılığı açısından doğrulandı: pozitif
  bias = bullish = YES ile hizalı, negatif = bearish = NO ile hizalı —
  tüm eşik karşılaştırmaları (`>15`/`<-15`, `<-30`/`>30`) doğru yönde.
- `OrderFlowData.agrees_with()`/`is_bullish`/`is_bearish`/`is_strong`
  property'lerinin kod tabanında hiç çağrılmadığı doğrulandı (`grep`) — ölü
  yardımcı metodlar, canlı etkisi sıfır, dokunulmadı (CLAUDE.md sadelik
  kuralı).

Bu dosyada gerçek bir hata bulunamadı.

## Bulunan ama düzeltilmeyen (canlı etkisi sıfır) sorun: `research_agent.py`'nin fear_greed/social_sentiment alanları

`agents/subagents/research_agent.py::_fetch_enhanced()`:
```python
social = self.enhanced_signals.get_social_signal(sym) or {}
result[sym] = EnhancedData(
    ...
    social_sentiment=social.get("score", 0.0),
    fear_greed_index=social.get("fear_greed"),
)
```
`agents/enhanced_signals.py::get_social_signal()` **hiçbir zaman** `"fear_greed"`
anahtarı döndürmüyor (sadece `{"score", "signal", "boost"}`) — yani
`fear_greed_index` bu yoldan her zaman `None` kalıyor. Ayrıca `"score"`
alanı `get_social_signal()`'da 0-100 ölçekli bir "sosyal aktivite" skoru
(Reddit/Twitter hacmi), ama `EnhancedData.social_sentiment`'ın dokümante
ölçeği -1..+1 (`# -1 to +1`) — ölçek uyuşmazlığı da var.

**Canlı etki kontrolü**: `sig.fear_greed` ve `sig.social_sentiment`
(`agents/subagents/signal_agent_v2.py`'ye `coordinator.py::_re_enrich_signals()`
ve `signal_agent_v2.py`'nin kendi enrich adımı üzerinden yazılıyor) hiçbir
yerde `_calc_confluence()`, `_detect_risk_flags()` veya `to_review_summary()`
(Claude reviewer prompt'u) içinde okunmuyor (`grep -n "fear_greed\|social_sentiment"
agents/subagents/signal_agent_v2.py` → sadece atama satırları, kullanım
yok). Botun gerçek Fear&Greed sinyali tamamen ayrı bir yoldan geliyor:
`agents/binance_feed.py::_fetch_fear_greed()`/`get_fear_greed()` →
`binance_feed.py:1219`'daki `signals.append(("fear_greed", fng_signal, 0.05))`
— bu, canlı Bayesian/aggregate sinyalin gerçekten kullandığı, doğru
çalışan, bağımsız bir bileşen. `research_agent.py`'nin kırık
`fear_greed_index`/ölçek-uyumsuz `social_sentiment` alanları bu ikinci
(gerçek) yoldan tamamen bağımsız, hiçbir tüketicisi olmayan ölü veri.

CLAUDE.md'nin sadelik kuralı ve repodaki yerleşik "ölü kod tespit edilince
belgelenir, dokunulmaz" örüntüsü (100-103. turlarda `check_paired_profit`,
`crypto_directional/`, `signal_bridge/`, `quality_filter.py`,
`control_plane/approval_queue.enqueue()`) gereği düzeltilmedi — sadece
kaydediliyor.

## Sonuç — bu turda kod değişikliği YOK

`agents/subagents/reviewer_agent.py`, `agents/subagents/research_agent.py`,
`agents/subagents/coordinator.py`'nin merge/reduce zinciri ve
`agents/subagents/orderflow_agent.py` satır satır incelendi. Hiçbirinde
pozisyon boyutlandırma, yön, risk gate'leme veya sermaye muhasebesini bozan
**gerçek** bir canlı-karar hatası bulunamadı. Tek yeni bulgu
(`research_agent.py`'nin `fear_greed`/`social_sentiment` alanları) canlı
kararlara sıfır etki eden, kanıtlanmış ölü veri — CLAUDE.md'nin "gereksiz
fix uydurma" kuralı gereği düzeltilmedi.

Bu yüzden bu turda kod/test değişikliği yapılmadı — sadece bu inceleme
belgesi eklendi. `git stash`/`pop` doğrulama adımı uygulanabilir bir
düzeltme olmadığı için atlandı.

## Tam suite
Baseline (oturum başı, değişiklik öncesi de sonrası da aynı — kod
değişikliği yok): **1773 passed, 4 skipped**. Bu turdan sonra da aynı:
**1773 passed, 4 skipped** (0 regresyon, 0 yeni test — beklenen, çünkü
hiçbir kaynak dosya değişmedi).

## Sermaye/performans notu
Bu oturumda çalışan bir bot instance'ı yok — %10 hedefine karşı gerçek
ilerleme doğrulanamıyor. Önceki turlarda düzeltilen kritik canlı-karar
hataları (edge wiring, OPT-7 EXPIRED streak, NO-direction Kelly penaltısı,
WS coin evreni, `/api/status` meta-merge sırası) hâlâ yerinde ve testlerle
korunuyor; bu turda aynı sınıftan yeni bir canlı-karar hatası bulunamadı.
`agents/subagents/reviewer_agent.py` (Claude API trade reviewer, canlı
yolda `ENABLE_REVIEWER_AGENT=true` varsayılanıyla aktif) ve
`agents/subagents/orderflow_agent.py` (sinyal oylamasına 2.5 ağırlıkla
giren order-flow bias motoru) gibi doğrudan sermaye kararına giren iki
önemli bileşenin doğru çalıştığının bu turda satır satır doğrulanması,
gelecekteki turların güven bütçesini başka yerlere yönlendirebilmesi
açısından faydalı bir negatif sonuç.

## Sıradaki tur için notlar
- `agents/subagents/reviewer_agent.py`, `agents/subagents/research_agent.py`
  ve `agents/subagents/orderflow_agent.py` artık satır satır taranmış ve
  temiz — bir sonraki turun bunlara dönmesi düşük getirili olur.
- Hâlâ derinlemesine taranmamış canlı-yol adayları: `shadow_runner/
  {readiness,journal,summary_metrics}.py` (readiness_verdict zincirinin
  `control_plane/live_gate.py`'nin 11. gate noktasına giden kısmı — 103.
  turda dolaylı doğrulandı ama `_check_readiness()`'in kendisi hâlâ satır
  satır okunmadı), `agents/subagents/signal_agent_v2.py`'nin geri kalanı
  (Bayesian/edge hesaplama kısmı — bu turda sadece confluence/risk-flag
  kısmı okundu, `ArbitrageEngine` çağrı zinciri ve `_enrich_signal()`'in
  tamamı henüz satır satır değil), `strategies/arbitrage_engine.py`'nin
  6-model (Bayesian+Edge+Spread+Stoikov+Kelly+MC) motorunun kendisi (çok
  büyük dosya, önceki turlarda parça parça dokunulmuş ama tam satır-satır
  geçiş yapılmamış olabilir — teyit edilmeli).
- `research_agent.py`'nin `fear_greed_index`/`social_sentiment` alanlarının
  hem anahtar-uyuşmazlığı (`"fear_greed"` hiç dönmüyor) hem ölçek-uyuşmazlığı
  (0-100 vs dokümante -1..+1) olduğu ve ikisinin de hiçbir tüketicisi
  olmadığı bu turda tespit edildi — düzeltme gerektirmiyor (sıfır canlı
  etki), ama bir sonraki tur bunu tekrar "yeni keşif" olarak raporlamamalı.
- Onay kuyruğu / doğrudan emir yolu çelişkisi (103b'de not edilen,
  `agents/orchestrator.py` ~1126) hâlâ çözülmedi — büyük bir mimari karar
  gerektirdiği için kullanıcı onayı bekliyor.
