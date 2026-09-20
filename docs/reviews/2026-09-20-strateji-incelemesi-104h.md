# Günlük Strateji İncelemesi — 2026-09-20 (104. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = `f47c5cc` (#184/#185/#186 — 103. turun üç
paralel oturumu: web_server.py merge-sırası fix'i, cycle risk-budget
un-floored balance fix'i, ve round-103 review dosyası rename'i — hepsi
main'e girmiş). Bu branch (`claude/brave-faraday-5tlsci`) `origin/main` ile
tam eşit başladı, açık farkı yoktu.

Konteynerde çalışan bir bot instance'ı yok (`data/status.json`,
`data/control.json`, `data/positions.json`, `.env` bu sandbox'ta yok; ağ
erişimi canlı Polymarket/Anthropic API'lerine değil) — %10 hedefine karşı
gerçek zamanlı sermaye ilerlemesi bu oturumdan doğrulanamıyor. Katkı,
önceki turların bıraktığı yerden kod/strateji doğruluğu seviyesinde devam
etti.

## Baseline doğrulama
`pip install -r requirements.txt` + `python3 -m pytest tests/
calibration/tests execution_realism/tests signal_bridge/tests
crypto_directional/tests -q` → **1773 passed, 4 skipped** (103b'nin
1771'inden +2 — #185/#186'nın testleri main'e girdiği için beklenen
artış).

## Bu turda incelenen alanlar
103b'nin "sıradaki tur için notlar" bölümünün işaret ettiği, daha önce
isim geçmemiş adaylar ele alındı: `agents/enhanced_signals.py` (519
satır), `agents/copytrade.py` (310 satır), `agents/hit_rate_tracker.py`
(199 satır) — ayrıca zincirleme olarak `agents/subagents/research_agent.py`,
`agents/subagents/signal_agent_v2.py`, `agents/subagents/reviewer_agent.py`,
`agents/autonomous_engine.py`, `agents/top_trader_signal.py`,
`agents/kalshi_arb.py`, `agents/whale_tracker.py` — hepsi satır satır
okundu (bu son grup daha önceki turlarda değişik derecelerde
incelenmişti; bu turda "hâlâ tutuyor mu" diye yeniden doğrulandı).

### `agents/whale_tracker.py`, `agents/kalshi_arb.py`, `agents/top_trader_signal.py`
Üçü de önceki turlarda (side/outcome yön hatası, cid anahtarı, freshest-
ticker seçimi) düzeltilmiş — düzeltmeler hâlâ yerinde, regresyon yok.
`agents/orchestrator.py` üzerinden gerçekten canlı yola bağlı oldukları
doğrulandı (`whale_tracker_cls`, `top_trader`, `kalshi_arb` hepsi
`ArbitrageEngine`/`AgentCoordinator`'a enjekte ediliyor).

**Yeni gözlem (düzeltme gerektirmiyor)**: `agents/top_trader_signal.py`
içindeki `TOP_TRADERS` listesi (Theo/GCR/Fredi9999/Domer/Polywhale1, hepsi
placeholder `0x1234` tarzı sahte adresler) `_process_trades()` tarafından
**hiç kullanılmıyor** — filtreleme belirli trader adreslerine göre değil,
data-api'den gelen tüm `$50+` işlemlere göre yapılıyor
(`params={"limit": 200, "min_size": 50}`, adres filtresi yok). Yani sınıfın
adı ve docstring'i ("sadece bilinen başarılı trader'ları takip eder") ile
gerçek davranışı (herhangi bir büyük işlem akışı) örtüşmüyor. Ancak bu bir
"yanlış karar" hatası değil — sinyal hâlâ gerçek piyasa verisinden
tutarlı şekilde üretiliyor, sadece isimlendirme yanıltıcı. Düzeltmek
(gerçek top-trader adresleriyle filtrelemek) veri/tasarım kararı
gerektiriyor (placeholder adresler gerçek değil) — kullanıcı onayı
olmadan dokunulmadı, not olarak bırakılıyor.

### `agents/enhanced_signals.py` + `agents/subagents/research_agent.py` zinciri
`EnhancedSignals.get_options_signal()` hiçbir zaman `"max_pain"` anahtarı
döndürmüyor (dosyanın kendi docstring'i "max pain" hesapladığını iddia
etse de `fetch_options_data()` bunu hiç hesaplamıyor), ve
`get_social_signal()` hiçbir zaman `"fear_greed"` anahtarı döndürmüyor.
Bu yüzden `research_agent.py::_fetch_enhanced()`'daki
`opts.get("max_pain")` ve `social.get("fear_greed")` her zaman `None`
dönüyor. Ancak `grep -rn "\.max_pain\|\.fear_greed\b\|\.social_sentiment\|
\.put_call_ratio" agents/ strategies/ core/` bu alanların
`signal_agent_v2.py`/`coordinator.py` dışında (yani `_compute_confluence()`,
`_detect_risk_flags()`, `to_review_summary()` — üçü de bu alanları
**hiç okumuyor**) hiçbir yerde tüketilmediğini gösterdi: canlı karar
üzerinde sıfır etki, salt ölü alan. (Gerçek fear&greed sinyali ayrı bir
mekanizmadan geliyor: `agents/binance_feed.py::_fetch_fear_greed()` →
Bayesian ağırlıklı sinyal listesine `("fear_greed", fng_signal, 0.05)`
olarak giriyor — bu doğru çalışıyor, karıştırılmamalı.) CLAUDE.md'nin
sadelik kuralı gereği dokunulmadı.

### `agents/copytrade.py` — YENİ BULGU: CopytradeEngine hiçbir yerden çağrılmıyor

`grep -rln "CopytradeEngine" --include=*.py .` → tek eşleşme
`agents/copytrade.py`'nin kendisi. `agents/orchestrator.py` ve `main.py`
içinde `CopytradeEngine`'e veya `copytrade` modülüne hiçbir referans yok —
sınıf hiçbir yerde instantiate edilmiyor, `check_and_copy()` hiçbir cycle'da
çağrılmıyor. Dosyanın kendisi (trader listesi, direction/side-outcome
mapping, horizon parser, snapshot persist) satır satır okundu ve mantık
olarak doğru görünüyor (`whale_tracker.py`/`top_trader_signal.py`'de daha
önce düzeltilen side/outcome yön hatasından bu dosya muaf — burada zaten
doğru yapılmış), ama bunun hiçbir önemi yok çünkü kod canlı hiçbir yoldan
tetiklenmiyor. Tamamen ölü kod — repo'nun yerleşik örüntüsüyle
(`check_paired_profit`, `crypto_directional/`, `signal_bridge/`,
`quality_filter.py`, `approval_queue.enqueue()`, `hit_rate_tracker.py`
zinciri) aynı sınıfta: bulundu, kaydedildi, sıfır canlı etkisi olduğu için
CLAUDE.md'nin "gereksiz olmayan şeyi değiştirme" kuralı gereği
dokunulmadı (silme de bu turun kapsamı dışında — kullanıcı onayı
gerektiren bir temizlik kararı).

### `agents/subagents/signal_agent_v2.py`, `agents/subagents/reviewer_agent.py`, `agents/autonomous_engine.py`
Üçü de önceki turlarda çok sayıda gerçek hata (VETO'nun approved
property'den atlanması, trade_number eşleştirme, suggested_size_pct
clamp, COUNTER_REGIME prefix eşleşmesi, çift-dampening önleme,
STREAK_FILTER SKIP'inin korunması) için düzeltilmiş ve her düzeltme kod
içinde ayrıntılı yorumla belgelenmiş. Bu turda satır satır yeniden okundu
— tüm düzeltmeler hâlâ yerinde, yeni bir hata bulunamadı.

### `agents/hit_rate_tracker.py`
`grep -rln "hit_rate_tracker\|HitRateTracker" --include=*.py . | grep -v
tests` → tek canlı-kod tüketicisi `agents/signal_agent.py`, ve
`docs/architecture.md`'nin kendisi bu dosyanın "DEVRE DISI (belgede var,
canlı kodda yok)" olduğunu doğruluyor — `agents/orchestrator.py`'nin
import listesinde `agents.signal_agent` hiç yok (doğrulandı:
`grep -n "^from agents\." agents/orchestrator.py` çıktısında yok). Yani
`hit_rate_tracker.py` da zaten belgelenmiş ölü-kod zincirinin bir parçası
— yeni bir bulgu değil, dokunulmadı.

## Sonuç — bu turda kod değişikliği YOK
İncelenen 8 dosyada (enhanced_signals, copytrade, hit_rate_tracker,
research_agent, signal_agent_v2, reviewer_agent, autonomous_engine,
top_trader_signal/kalshi_arb/whale_tracker yeniden doğrulama) pozisyon
boyutlandırma, yön, risk gate'leme veya sermaye muhasebesini bozan
**gerçek, düzeltmesi gereken** bir canlı-karar hatası bulunamadı. Tek yeni
somut bulgu (`CopytradeEngine`'in tamamen ölü olması) canlı kararlara sıfır
etkili, repo'nun yerleşik "belgele, dokunma" örüntüsüyle aynı sınıfta.

## Tam suite
Baseline (oturum başı) ve bu turun sonu aynı: **1773 passed, 4 skipped**
(0 regresyon, 0 yeni test — beklenen, çünkü hiçbir kaynak dosya değişmedi).

## Sermaye/performans notu
Bu oturumda çalışan bir bot instance'ı yok — %10 hedefine karşı gerçek
ilerleme bu oturumdan doğrulanamıyor. Önceki turlarda düzeltilen kritik
canlı-karar hataları (edge wiring, OPT-7 EXPIRED streak, whale/top-trader
yön hataları, position_meta merge sırası, risk-budget floor) hâlâ yerinde
ve testlerle korunuyor.

## Sıradaki tur için notlar
- `agents/copytrade.py`'nin canlıya bağlanıp bağlanmayacağı (yoksa kalıcı
  olarak silinip silinmeyeceği) bir kullanıcı kararı — bu turda
  otonom olarak değiştirilmedi.
- `agents/top_trader_signal.py::TOP_TRADERS` listesinin gerçek adreslerle
  doldurulup filtreleme için kullanılıp kullanılmayacağı da benzer şekilde
  bir tasarım kararı; şu anki davranış (adres filtresiz genel büyük işlem
  akışı) yanlış değil, sadece isimle uyuşmuyor.
- Henüz bu review zincirinde hiç derinlemesine taranmamış adaylar:
  `agents/latency_arb.py` (548 satır, en son 98. turda değinilmiş — ~6 tur
  önce), `strategies/maker_engine.py`, `agents/market_index_watcher.py`
  (en son 88. turda, ~16 tur önce).
