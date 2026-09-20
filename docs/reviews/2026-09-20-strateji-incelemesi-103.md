# Günlük Strateji İncelemesi — 2026-09-20 (103. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = `330ffaa` (#182, 102c'nin PR'ı dahil olmak
üzere PR #180/#181/#182 hepsi merge edilmiş). Bu branch (`claude/brave-
faraday-ibv7fq`) `origin/main` ile tam eşit başladı (`git merge-base HEAD
origin/main` == `HEAD`), açık farkı yoktu.

Konteynerde çalışan bir bot instance'ı yok (`data/status.json`/
`control.json`/`positions.json` bu sandbox'ta yok, ağ erişimi de yok) —
%10 hedefine karşı gerçek zamanlı sermaye ilerlemesi bu oturumdan
doğrulanamıyor; katkı kod/strateji doğruluğu seviyesinde kalıyor.

## Baseline doğrulama
`pip install -r requirements.txt` + `python3 -m pytest tests/
calibration/tests execution_realism/tests crypto_directional/tests
signal_bridge/tests -q` → **1769 passed, 4 skipped** (102c'nin 1764'ünden
+5 — #180 (candlestick, +4 test) ve #181 (ws_feed, +1 test) main'e merge
olduğu için beklenen artış).

Not: test suite çalıştırıldığında `data/autonomous_state.json` yan etkisi
oluşuyor (bilinen davranış, önceki turlarda da görülmüş); `git checkout --
data/autonomous_state.json` ile geri alındı.

## Bu turda incelenen alan

Görev talimatının önerdiği iki en-az-taranmış alan derinlemesine incelendi:

1. **`core/web_server.py`** (417 satır) — tamamı satır satır okundu:
   `do_GET`/`do_POST`/`do_OPTIONS`, `_send_status`, `_send_signals`,
   `_send_gate_status`, `_send_pending_orders`, `_handle_order_action`,
   `_send_chamber`, `_send_json_data`/`_send_file`, `start()`.
2. **`scripts/migrate_positions.py`** (152 satır) ve
   **`scripts/verify_binance_trades.py`** (225 satır) — tamamı satır satır
   okundu.

### `core/web_server.py` — tüm route'lar tek tek sınıflandırıldı

| Route | Ne yapıyor | Canlı karara girer mi? |
|---|---|---|
| `GET /`, `/chamber` | Statik HTML servis | Hayır |
| `GET /api/status` | `status.json`+`position_meta.json`+`positions.json` merge, dashboard'a JSON | Hayır — salt okuma |
| `GET /api/control` | `control.json` oku | Hayır — salt okuma |
| `POST /api/control` | `live_trading`/`simulation_running`/`min_bet` whitelist'i yazıyor | **88. turda zaten incelendi** (whitelist doğru) |
| `GET /api/gate` | `check_live_gate()`'i "snapshot" olarak çalıştırıp sonucu JSON döner | Hayır — kodun kendi yorumu da doğruluyor: `daily_loss_exceeded=False, # Snapshot — tam kontrol orchestrator'da`. Gerçek gate kontrolü her zaman `orchestrator.py::_cycle()`/`_execute_approved_orders()` içinde ayrıca ve bağımsız çalışıyor; bu endpoint'in çıktısı hiçbir yerden okunup karara beslenmiyor. |
| `GET /api/signals` | Win/loss streak, Kelly-mult, Fear&Greed, SPX, regime — dashboard görüntüleme amaçlı hesaplanıyor | Hayır — sadece JSON response, hiçbir tüketicisi yok |
| `GET /api/pending`, `POST /api/pending/approve|reject` | `core/approval_queue.py` (→ `control_plane/approval_queue.py`) üzerinden PENDING/APPROVED emirleri sorgula/onayla | **Aşağıda ayrıntılı incelendi — bkz. "İkinci bulgu"** |
| `GET /api/chamber/*` | `operator_layer/api.py`'ye delege — Architect Chamber operator dashboard'u | Hayır — `operator_layer/*` hiçbir yerden `agents/`, `strategies/`, `core/` (web_server.py hariç) tarafından import edilmiyor (`grep -rln operator_layer agents/ strategies/ core/ main.py` → sadece `core/web_server.py`). Salt-okuma görüntüleme katmanı. |

**Sonuç**: `/api/control`'un whitelist'i hariç (zaten 88. turda incelenmiş),
`web_server.py`'deki her route ya salt-okuma bir dashboard sink'i, ya da
(approval queue hariç) hiçbir canlı tüketicisi olmayan bir JSON endpoint'i.
Orchestrator hiçbir zaman kendi HTTP sunucusuna (`127.0.0.1:8080`) istek
atmıyor — `grep -rn "8080\|api/signals\|api/gate" agents/ strategies/
core/ main.py` tek eşleşmeyi `main.py`'nin `start_web(port=8080)` çağrısında
buluyor. Bu dosyada yeni bir hata **bulunamadı**.

### İkinci bulgu (reddedilen aday): approval-queue POST route'ları — kod
doğru ama canlı yolda tamamen ölü

`POST /api/pending/approve|reject` → `core/approval_queue.approve/reject`
→ `control_plane/approval_queue.py::ApprovalQueue.approve/reject` — bu
state machine'i (`PENDING → APPROVED | REJECTED`, `control_plane/types.py`
docstring'i "INC-2026-03-15-001 dersi: Sinyal → emir arasında insan onayı
ZORUNLU" diyor) satır satır okundum, `transition()`/`cleanup_expired()`/
`_file_lock()` doğru görünüyor.

Ama `grep -rn "\.enqueue(\|enqueue(" agents/ strategies/ core/
control_plane/ main.py` — **`enqueue()` şu anki canlı kod tabanında hiçbir
yerden çağrılmıyor**. `agents/orchestrator.py`, sinyal → emir arasında bu
kuyruğu hiç kullanmıyor; kendi yorumunda da açıkça yazıyor
(`# ── DOĞRUDAN EMİR VER (onay kuyruğu bypass) ──`, satır ~1126) —
gerçek/otomatik yürütme yolu approval queue'yu kasıtlı olarak bypass
ediyor. Sonuç: `get_pending()` her zaman `[]` döner,
`agents/orchestrator.py::_execute_approved_orders()` her döngüde `approved
= _get_approved_orders(); if not approved: return` ile hemen çıkıyor, ve
dashboard'daki approve/reject butonları (varsa) hep boş bir listeye
işlem yapıyor — kod hatasız ama fonksiyonel olarak inert.

Bunu "düzeltilecek bir hata" olarak değil, CLAUDE.md'nin sadelik kuralı
("tek seferlik işlemler için helper/abstraction ekleme", "gereksiz olmayan
şeyi değiştirme") ve bu repodaki yerleşik "ölü kod tespit edilince
belgelenir, dokunulmaz" örüntüsüyle (100./101. turda `check_paired_profit`,
88./100./102. turda `crypto_directional/`, 100. turda `signal_bridge/`)
aynı sınıfta bir bulgu olarak kaydediyorum — canlı karara sıfır etkisi
var, "urgent fix" gerektiren bir şey değil. `_execute_approved_orders()`
içindeki `_fresh_price_ok()` staleness kontrolünün olmaması (doğrudan
emir yolunda var, approved-order yolunda yok) da aynı sebeple önemsiz:
approved-order yoluna hiçbir zaman gerçek bir emir düşmüyor.

### `scripts/migrate_positions.py` — tek seferlik bakım scripti, canlı yola bağlı değil

`clean_positions()`/`clean_closed()`/`fix_pnl()`/`migrate()` satır satır
okundu. `grep -rn "migrate_positions" .` → hiçbir CI/cron/session-hook/
başka Python dosyası bu scripti çağırmıyor; sadece `python
scripts/migrate_positions.py` ile elle çalıştırılıyor. `--reset-capital`
olmadıkça `data["capital"]` alanına hiç dokunulmuyor (yalnızca
`positions`/`closed`/`daily` normalize ediliyor) — yani normal çalıştırma
capital muhasebesini bozamaz. `fix_pnl()`'in davranışı (`pnl==0 and
result=="LOSS" → "NEUTRAL"`) dosyanın kendi docstring'indeki niyetle
birebir örtüşüyor. Yeni bir hata bulunamadı.

### `scripts/verify_binance_trades.py` — tarihi tek-seferlik düzeltme scripti, artık geçerli senaryosu yok

Docstring'in kendisi bunun geçmişte (`13 trades were resolved by Binance
kline comparison instead of CLOB tokens.winner`) bir kerelik bir sorunu
düzeltmek için yazıldığını söylüyor. `core/position_manager.py`'nin
resolve mantığı zaten bugün SADECE CLOB `tokens.winner` kullanıyor —
satır ~404-407'deki yorum bunu doğruluyor: `"Binance kontrolü: gerçek
sonuçlar TERS kaydediliyordu. Sadece CLOB winner ile resolve et."` — yani
canlı kod artık hiç Binance-kline-resolved trade üretmiyor, bu script'in
hedef aldığı veri sınıfı (`pnl_verified=True and not
resolution_corrected`) bugünden itibaren boş küme (yeni trade'ler için).
`apply_corrections()`'daki PnL formülü (`shares=amount/entry;
WIN→shares*1.0-amount; LOSS→-amount`) `core/position_manager.py::
_close_position()`'daki gerçek formülle birebir tutarlı doğrulandı —
tutarsızlık yok. Script insan onayı (`input("Apply corrections?")`)
olmadan hiçbir şeyi diske yazmıyor; otomasyona bağlı değil. Yeni bir hata
bulunamadı.

## Genişletilmiş arama (step 2): zaten bulunamayınca ek bir alan

Ana iki alanda gerçek bir hata bulunamadığı için, görev talimatının 2.
adımı gereği son ~15 tur (88-102c) boyunca hiç isim geçmeyen, canlı yola
bağlı bir aday arandı:

- **`operator_layer/{api,aggregator,pnl,health,readiness_view}.py`** —
  yalnızca `core/web_server.py`'nin `/api/chamber/*` GET'lerine besleniyor
  (yukarıda doğrulandı), yani `web_server.py` incelemesinin bir parçası
  olarak zaten sınıflandırıldı: salt-görüntüleme, canlı etkisi yok.
- **`control_plane/approval_queue.py`** — yukarıda derinlemesine
  incelendi, ölü kod olarak doğrulandı.
- **`monitoring/daily_review.py::write_readiness_verdict()`** —
  `data/readiness_verdict.json`'ı yazıyor ve bu dosya gerçekten
  `control_plane/live_gate.py::_check_readiness()` üzerinden canlı
  trading'i gate'leyen 11 kontrolden biri (`TINY_PILOT_CANDIDATE` +
  tazelik). Ancak `write_readiness_verdict()`'in tek çağıranı
  (`grep -rln write_readiness_verdict`) `_gen_artifacts.py`/
  `_gen_snapshot.py` — ikisi de 91. ve 101. konsolidasyon turlarında zaten
  isim geçmiş (~12 tur önce, sınırda ama "zaten taranmış" kabul edildi) ve
  bu dosyanın kendisi (`monitoring/daily_review.py`) 88. turda da
  isim geçmişti. Bu zincir üzerinde yeni, spesifik bir lead olmadan
  (CLAUDE.md: "sadece yeni bir lead varsa tekrar dokunma") derinlemesine
  yeniden taramadım.
- **`strategies/quality_filter.py`** — `grep -rn quality_filter agents/
  strategies/ core/ main.py` → hiçbir yerden import edilmiyor, ölü kod
  (yeni bir tespit, ama yine canlı etkisi sıfır olduğu için "düzeltme"
  gerektirmiyor — CLAUDE.md sadelik kuralı gereği dokunulmadı).

Bu genişletilmiş taramanın hiçbirinde "gerçek, canlı karara giren ve
düzeltilmesi gereken" bir hata çıkmadı.

## Sonuç — bu turda kod değişikliği YOK

`core/web_server.py`, `scripts/migrate_positions.py`,
`scripts/verify_binance_trades.py` satır satır incelendi; ek olarak
`control_plane/approval_queue.py` ve `operator_layer/*` zinciri
derinlemesine takip edildi. Hiçbirinde pozisyon boyutlandırma, yön, risk
gate'leme veya sermaye muhasebesini bozan **gerçek** bir canlı-karar hatası
bulunamadı — bulunanlar (approval-queue'nun `enqueue()`'sız ölü olması,
`quality_filter.py`'nin hiç import edilmemesi) CLAUDE.md'nin "minimal
değişiklik, gereksiz fix uydurma" kuralı gereği düzeltilmedi, sadece
kaydedildi (repo'nun `check_paired_profit`/`crypto_directional`/
`signal_bridge` ile aynı yerleşik örüntüsü).

Bu yüzden bu turda kod/test değişikliği yapılmadı — sadece bu inceleme
belgesi eklendi. `git stash`/`pop` doğrulama adımı uygulanabilir bir
düzeltme olmadığı için atlandı (yapılacak bir şey yok).

## Tam suite
Baseline (oturum başı, değişiklik öncesi de sonrası da aynı — kod
değişikliği yok): **1769 passed, 4 skipped**. Bu turdan sonra da aynı:
**1769 passed, 4 skipped** (0 regresyon, 0 yeni test — beklenen, çünkü
hiçbir kaynak dosya değişmedi).

## Sermaye/performans notu
Bu oturumda çalışan bir bot instance'ı yok, %10 hedefine karşı gerçek
ilerleme bu oturumdan doğrulanamıyor. Bu turun katkısı negatif bir sonuç:
görev talimatının işaret ettiği iki en-az-taranmış alanın (dashboard/web
sunucusu, scripts/) gerçekten canlı sermaye/pozisyon kararlarına sıfır
etkisi olduğu doğrulandı — bu da gelecekteki turların zaman bütçesini
başka yerlere (aşağıya bkz.) yönlendirmesi için faydalı bir negatif sonuç.

## Sıradaki tur için notlar
- `core/web_server.py` ve `scripts/` artık tamamen taranmış ve temiz —
  CLAUDE.md'nin önerdiği iki "az taranmış alan" bitti, bir sonraki turun
  bu ikisine dönmesi düşük getirili olur.
- Gerçekten hiç isim geçmemiş, canlı-bağlantısı doğrulanması gereken
  adaylar: `shadow_runner/{runner,replay,reporting,validation}.py`
  (yalnızca shadow/paper mod mu besliyor yoksa gerçek karara sızıyor mu
  netleştirilmeli), `calibration/{calibrator,probability_mapper,
  edge_estimator}.py` (`grep` bunların `agents/`/`strategies/` tarafından
  hiç import edilmediğini gösterdi — muhtemelen `crypto_directional/`
  sınıfında ölü kod, ama teyit edilmeli), `agents/subagents/
  {research_agent,reviewer_agent}.py` (şimdiye kadar sadece toplu
  `agents/subagents/*.py` referanslarıyla anıldı, ayrı ayrı derin
  taranmadı).
- `control_plane/approval_queue.py`'nin `enqueue()`'ının hiçbir yerden
  çağrılmadığı (dolayısıyla PENDING/APPROVED akışının canlı kodda inert
  olduğu) ve `strategies/quality_filter.py`'nin hiç import edilmediği bu
  turda tespit edildi — ikisi de düzeltme gerektirmiyor (sıfır canlı
  etki), ama bir sonraki tur bunları tekrar "yeni keşif" olarak
  raporlamamalı.
- `monitoring/daily_review.py::write_readiness_verdict()` zincirinin
  (`_gen_artifacts.py`/`_gen_snapshot.py` → `readiness_verdict.json` →
  `control_plane/live_gate.py`'nin 11. gate noktası) gerçekten canlı
  trading'i gate'lediği doğrulandı — bu zincir daha önce (88./91./101.
  turlarda) dolaylı olarak isim geçmiş ama `_check_readiness()`'in kendi
  tazelik/verdict mantığı bu turda satır satır yeniden okunmadı; spesifik
  bir lead olmadığı için dokunulmadı ama bir sonraki tur için makul bir
  aday.
