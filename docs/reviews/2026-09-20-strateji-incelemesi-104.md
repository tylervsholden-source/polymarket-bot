# Günlük Strateji İncelemesi — 2026-09-20 (104. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = `f47c5cc` (#186 dahil, 103. turun tüm
düzeltmeleri merge edilmiş: `position_meta.json` merge sırası + sim-mode
risk-budget floor). Bu branch (`claude/brave-faraday-g1wnf7`) `origin/main`
ile tam eşit başladı, açık farkı yoktu.

Konteynerde çalışan bir bot instance'ı yok (`.env`, `data/status.json`,
`data/control.json`, `data/positions.json` yok; canlı Polymarket/Anthropic
API erişimi yok) — %10 hedefine karşı gerçek zamanlı sermaye ilerlemesi bu
oturumdan doğrulanamıyor; katkı kod/strateji doğruluğu seviyesinde kaldı.

Not: Açık iki PR mevcuttu (#187 — 103. tur konsolidasyon dokümanı, docs-only,
mergeable=clean; #183 — 102. tur konsolidasyonu, muhtemelen artık gereksiz).
Bu oturum bunları yönetmekle görevlendirilmedi ve dokunulmadı — sadece dosya
adı çakışmasını önlemek için not edildi (`-104.md` kullanıldı).

## Baseline doğrulama
- `pip install -r requirements.txt`
- `python3 -m pytest tests/ calibration/tests execution_realism/tests signal_bridge/tests crypto_directional/tests -q`
  → **1773 passed, 4 skipped** (bu branch'in başlangıç durumu).
- Test suite'in bilinen `data/autonomous_state.json` yan etkisi `git checkout --` ile geri alındı.

## Bu turda incelenen alanlar
103b'nin "sıradaki tur için notlar" bölümünde önerilen adaylar tarandı:
`operator_layer/` (ledgers.py, aggregator.py, health.py tam okundu),
`agents/subagents/reviewer_agent.py` (455 satır, tam okundu),
`agents/enhanced_signals.py` (519 satır, tam okundu), `agents/copytrade.py`
(309 satır, tam okundu).

### Hatasız / etkisiz çıkan alanlar
- **`agents/subagents/reviewer_agent.py`** — satır satır okundu. Bu dosya
  zaten önceki turlarda ağır şekilde düzeltilmiş (`approved` property'sinin
  VETO'yu dahil etmesi, `trade_number` ile eşleştirme, `suggested_size_pct`
  clamp'i — hepsi kod içi yorumlarla belgelenmiş). Yeni bir hata
  bulunamadı.
- **`agents/copytrade.py`** (`CopytradeEngine`) — tamamı okundu, mantık
  tutarlı (horizon parse, direction mapping, snapshot persistence). Ama
  `grep -rn "CopytradeEngine" --include=*.py .` → sınıf hiçbir yerden
  instantiate edilmiyor (main.py, orchestrator.py, coordinator.py'de yok).
  Tamamen ölü kod — dokunulmadı (repo'nun "ölü kod belgelenir, dokunulmaz"
  örüntüsü, bkz. 88./100./102. tur `crypto_directional/`).
- **`agents/enhanced_signals.py`** — tamamı okundu. Tüm boost'lar
  (`strategies/arbitrage_engine.py:930-984`) zaten önceki turlarda kasıtlı
  olarak devre dışı bırakılmış (`# FIX: ... boost DISABLED` yorumları,
  MULTI_EX/OPTIONS/WHALE/SOCIAL hepsi) — sadece log için hesaplanıyor,
  `bayesian_prob`'a hiç eklenmiyor. Ayrı bir yol olan
  `research_agent.py::_fetch_enhanced()` içinde `opts.get("max_pain")` ve
  `social.get("fear_greed")` anahtarları hiç üretilmeyen alanlar (
  `get_options_signal()`/`get_social_signal()` sadece `pcr`/`iv`/`signal`/
  `boost` ve `score`/`signal`/`boost` döner) — sonuç olarak
  `EnrichedSignal.social_sentiment`/`fear_greed`/`max_pain` hep varsayılan
  değerde kalıyor. Ama bu alanlar `_compute_confluence()` ve
  `_detect_risk_flags()` içinde (confluence/risk-flag hesaplayan tek iki
  yer) hiç okunmuyor ve `to_review_summary()`'de de yok — yani canlı karara
  sıfır etkisi olan, zaten iki katmanlı (kullanılmayan alan + boost'u zaten
  disabled) bir ölü yol. Düzeltilecek gerçek bir davranış farkı yok
  (max_pain hesabı hiç implemente edilmemiş — eksik alanı "düzeltmek"
  yeni bir özellik eklemek olurdu, kapsam dışı).
- **`operator_layer/aggregator.py`, `health.py`** — okundu, iş mantığı
  (health scoring, aggregation) tutarlı, hata bulunamadı.

## Bulunan ve düzeltilen hata: `core/web_server.py` tek-thread'li `HTTPServer`, `/api/chamber/*` okuması `/api/control` dahil TÜM istekleri bloke edebiliyordu

`operator_layer/api.py::handle_summary()` docstring'i açıkça söylüyor:
"Frontend polls this every 5 seconds." Bu fonksiyon
`build_chamber_summary()` → `operator_layer/ledgers.py::read_journal_records()`
zincirine gidiyor, ve bu fonksiyon **tüm** `data/shadow_journal_*.jsonl`
dosyalarının **tüm satırlarını** belleğe okuyup parse ettikten sonra
zaman damgasına göre sıralayıp `max_records`'a kırpıyor — dosyanın
docstring'i "Journal reading is batched ... to avoid reading unbounded
files" dese de gerçek davranış tam tersi: `max_records` sadece sona
uygulanıyor, okumanın kendisi sınırsız.

Bu konteynerde sadece 3 günlük (`2026-03-15/16/17`) shadow journal verisi
var (~100MB toplam, 73k satır) ve gerçek zamanlı ölçüm:

```
read_journal_records(max_records=1000)      → 4.93s
read_journal_integrity_stats()               → 0.93s
```

Bot 20 günlük hedefli bir koşum için tasarlandı — bu dosyalar her gün
büyüyor, `handle_summary()` ise **her 5 saniyede bir** frontend tarafından
çağrılıyor. `core/web_server.py::start()` düz `HTTPServer` kullanıyordu
(`ThreadingHTTPServer` değil) — Python'un `http.server.HTTPServer`'ı
tek seferde tek istek işler, aynı `_Handler` sınıfı hem `/api/chamber/*`
hem `/api/control` (GET/POST) hem `/api/status`'u aynı soket üzerinden
sırayla serviser. Sonuç: chamber dashboard'u açık/polling durumdayken,
`POST /api/control` (örn. `live_trading: false` ile acil durdurma) o an
işlenmekte olan chamber isteği bitene kadar (ölçülen: ~5s, veri büyüdükçe
artacak) kuyrukta bekliyor — 20 günün sonunda bu süre onlarca saniyeye
çıkabilir. Bu, CLAUDE.md'nin "Bot ASLA Durmaz" / resilience doktrinine ve
acil durdurma kontrolünün duyarlı olması gerekliliğine doğrudan aykırı bir
davranış.

### Düzeltme
`core/web_server.py::start()` ve `__main__` bloğu düz `HTTPServer` yerine
stdlib'in `http.server.ThreadingHTTPServer`'ını kullanacak şekilde
değiştirildi (2 satır) — her istek artık ayrı bir thread'de işleniyor, bu
yüzden yavaş bir `/api/chamber/*` okuması diğer endpoint'leri artık bloke
edemiyor. `core/approval_queue.py`'nin altında yatan
`control_plane/approval_queue.py` zaten `fcntl.flock` ile dosya kilitleme
kullanıyor (çoklu-process güvenli tasarlanmış — web server zaten
orchestrator'dan ayrı bir thread'de çalışıyor, yani `positions.json`/
`control.json` gibi dosyalar her zaman orchestrator ile de yarışıyordu);
`control.json` read-modify-write'ı için ayrıca kilit eklenmedi çünkü (a)
admin POST'ları insan tetiklemeli ve pratikte eşzamanlı olma ihtimali
ihmal edilebilir düzeyde, (b) bu zaten var olan (web server thread'i vs.
orchestrator process'i) bir yarış sınıfına yeni bir katılımcı ekliyor,
yeni bir risk sınıfı değil — kapsamı gereksiz yere genişletmemek için
dokunulmadı.

### Test
`tests/test_web_server_threading.py` (1 yeni test):
`test_slow_chamber_request_does_not_block_control_endpoint` — gerçek bir
`web_server.start(port=0)` sunucusu başlatıp `operator_layer.api.
handle_summary`'yi yapay olarak yavaşlatarak (bir `threading.Event` ile
kontrol edilen blocking call) eşzamanlı bir `GET /api/control`'ün 1
saniyeden hızlı tamamlandığını doğruluyor.

Düzeltme öncesi doğrulama (`git stash` ile `core/web_server.py`'yi eski
haline getirip test çalıştırıldı): test **timeout ile FAIL** oldu
(`httpx.ReadTimeout`, 2 saniyelik client timeout'u bile aşıyor) — bug'ı
birebir yakalıyor. Düzeltme sonrası: PASS (~2.1s toplam, chamber
handler'ın kasıtlı 5s'lik `release.wait` üst sınırından çok önce control
isteği dönüyor).

### Tam suite
- Düzeltme öncesi (baseline): **1773 passed, 4 skipped**.
- Düzeltme sonrası: **1774 passed, 4 skipped** (+1 yeni test, 0 regresyon).

## Sermaye/performans notu
Bu oturumda çalışan bir bot instance'ı yok — %10 hedefine karşı gerçek
ilerleme doğrulanamıyor. Bulunan hata dashboard/kontrol katmanında
(operator_layer zaten canlı karar yoluna bağlı değil, ama `core/
web_server.py` hem chamber'ı hem de gerçek `/api/control` acil-durdurma
uç noktasını aynı sunucuda servis ediyor) — doğrudan sinyal/Kelly/edge
hesabını etkilemiyor ama gerçek bir operasyonel risk (acil durdurmanın
gecikmesi) düzeltildi.

## Sonuç
`core/web_server.py`'deki tek-thread'li `HTTPServer` → `ThreadingHTTPServer`
değişikliği yapıldı ve gerçek bir sunucu başlatan regresyon testiyle
doğrulandı (düzeltme öncesi test timeout ile fail, sonrası pass). Diğer
taranan alanlarda (`reviewer_agent.py`, `enhanced_signals.py`,
`copytrade.py`, `operator_layer/aggregator.py`/`health.py`) yeni bir hata
bulunamadı.

## Sıradaki tur için notlar
- `operator_layer/ledgers.py::read_journal_records()`/
  `read_journal_integrity_stats()` hâlâ dosyanın tamamını okuyup sonradan
  kırpıyor (docstring'in vaat ettiği "avoid reading unbounded files"
  davranışı yok) — bu turda sadece bloklamayı (ThreadingHTTPServer)
  düzelttik, okumanın kendisini hâlâ pahalı (shadow journal'lar büyüdükçe
  daha da pahalı olacak). Gerçek bir "tail-first" okuma (dosyanın sonundan
  başlayıp `max_records`'a ulaşınca durma) daha büyük, non-trivial bir
  değişiklik olacağı için bu turda kapsam dışı bırakıldı — ileride journal
  dosyaları büyümeye devam ederse (özellikle 20 günlük hedefin sonuna
  doğru) tekrar değerlendirilmeli.
- `agents/enhanced_signals.py::get_options_signal()`/`get_social_signal()`
  içinde `max_pain`/`fear_greed` alanları hiç üretilmiyor (research_agent.py
  bunları okumaya çalışıyor ama hep `None`/varsayılan kalıyor) — ama bu
  alanlar confluence/risk-flag hesabında hiç kullanılmadığı için canlı
  etkisi yok, düzeltilmedi (eksik özellik, bug değil).
- `agents/copytrade.py::CopytradeEngine` tamamen ölü kod (hiçbir yerden
  instantiate edilmiyor) — silinip silinmeyeceği kullanıcı kararı,
  dokunulmadı.
- Henüz derinlemesine taranmamış adaylar: `agents/hit_rate_tracker.py`
  (yalnızca dead `agents/signal_agent.py` tarafından kullanılıyor, düşük
  öncelik), `operator_layer/readiness_view.py`, `operator_layer/types.py`,
  `operator_layer/pnl.py` (103. turda not edilmiş, henüz satır satır
  okunmadı).
