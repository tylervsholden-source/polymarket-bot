# Günlük Strateji İncelemesi — 2026-09-16 (57. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum açıldığında `main` üzerinde birleştirilmemiş **3 PR** vardı, hepsi
`b5bbd23` (54. çalışma, #88) tabanından türemiş:

1. **#89** (`claude/brave-faraday-slgw64`) — "chore: sync autonomous engine
   runtime state snapshot". Diff tek satır: `data/autonomous_state.json`'un
   `last_update` alanı. PR açıklaması, 54. çalışmanın #88'ini bağımsızca
   doğruladığını ve sonrasında bu zararsız state senkronunu taşıdığını
   söylüyordu. `pytest tests/` → **753 passed, 2 skipped** (iddiayla
   birebir). Doğrudan merge edildi.
2. **#90** (`claude/brave-faraday-nhoo7s`) — "ReviewerAgent risk layer was
   disabled by default, not enabled (55th daily review)".
   `agents/orchestrator.py`'de `enable_review=os.getenv("ENABLE_REVIEWER_AGENT",
   "false")` idi — hem `CLAUDE.md`'nin belgelediği varsayılan (`true`) hem
   de `AgentCoordinator`'ın kendi sınıf varsayılanıyla (`enable_review: bool
   = True`) ters. `.env.example`'da bu değişken hiç yoktu, yani env
   ayarlamayan her deployment reviewer'ı (VETO/REDUCE risk katmanı) tamamen
   kapalı çalıştırıyordu — Claude API hatası durumundaki rule-based
   fallback'i bile devreye girmiyordu. Diff iddiayla eşleşti (`false` →
   `true` + `.env.example` belgelemesi + 2 yeni test). `pytest tests/` →
   **755 passed, 2 skipped** (iddiayla birebir). Merge edildi.
3. **#91** (`claude/brave-faraday-eozds5`) — "dashboard DAILY_STOP_LOSS
   check used life-of-bot capital instead of today's start-of-day capital
   (56th daily review)". `operator_layer/pnl.py::build_equity_state()`
   günlük -%15 stop-loss durumunu `abs(daily_pnl) / initial_capital`
   (botun ömür boyu başlangıç sermayesi) ile hesaplıyordu; gerçek kapı
   (`core/position_manager.py::daily_loss_exceeded()`) ise
   `day_start_capital = capital - daily.pnl` kullanıyor. `capital`'ın
   `initial_capital`'dan (botun amacı $1000→$3000 olduğu için) hızla
   sapması kaçınılmaz olduğundan, dashboard hem sermaye büyüdüğünde
   (yanlışlıkla "DAILY_STOP_LOSS" gösterip) hem de küçüldüğünde (gerçek
   stop tetiklenmişken "clear" gösterip) gerçek kapıyla anlaşmazlığa
   düşüyordu. `core/position_manager.py`'deki gerçek formülü satır satır
   doğruladım — diff bu formülü birebir yansıtıyor. `pytest tests/` →
   **756 passed, 2 skipped** (iddiayla birebir). Merge edildi.

Üçü de temiz merge oldu, çakışma yaşanmadı (ayrı dosyalar:
`data/autonomous_state.json`, `agents/orchestrator.py` tek satır +
`.env.example`, `operator_layer/pnl.py`). Çalışma dalı bu güncel `main`'den
(`caf9085`) yeniden oluşturuldu. Taban test suite'i: **758 passed, 2
skipped**.

## Bugünkü tarama
`docs/reviews/*.md` içindeki tüm dosya referansları grep'lendi ve
`agents/`, `core/`, `strategies/`, `control_plane/`, `monitoring/`,
`shadow_runner/`, `operator_layer/`, `execution_realism/` altındaki gerçek
kod dosyalarıyla karşılaştırıldı. Hiç isimle anılmamış adaylar arasında:
`monitoring/readiness_checks.py`, `shadow_runner/readiness.py`,
`shadow_runner/validation.py`, `monitoring/daily_review.py`, tüm
`operator_layer/*.py` (sadece `pnl.py` bugün #91'de değişti), `agents/
ws_feed.py`. `monitoring/readiness_checks.py` ve `shadow_runner/
readiness.py`'yi satır satır inceledim — sağlam; docstring'deki "1 FAIL →
CONDITIONAL" notu ile kodun "1 FAIL → NO_GO" davranışı arasındaki fark bir
an şüpheli göründü ama `tests/test_live_pilot_readiness.py::
test_single_fail_produces_no_go` bunu "Task 5.3: any FAIL blocks pilot"
olarak açıkça kasıtlı belgeliyor — sadece üstteki modül docstring'i eski,
davranış hatası değil, dokunulmadı.

`monitoring/daily_review.py`'yi incelerken gerçek bir hata bulundu.

## Bulgu (57.) — Canlı shadow kayıtları `decision="EXECUTE"` yazıyordu, ama tüketiciler `EXECUTE_YES`/`EXECUTE_NO` bekliyor

### Kapsam
`agents/orchestrator.py::_record_shadow_decisions()` (~satır 2489-2500).

### Kök neden
`shadow_runner/types.py`'deki `DecisionSummary.decision` alanının kendi
docstring'i açık: `decision: str  # TradeDecisionType.value`. Ve
`calibration/types.py::TradeDecisionType` enum'ının sadece 3 üyesi var:
`EXECUTE_YES`, `EXECUTE_NO`, `REJECT` — `"EXECUTE"` bunlardan biri değil.
Paper_strict/paper_loose shadow harness'ı (`shadow_runner/runner.py`, 
`calibration/decision_policy.py::decide()` üzerinden) bu sözlüğe her zaman
uyuyordu. Ama canlı orchestrator döngüsünün kendi kayıt fonksiyonu
(`_record_shadow_decisions`, sadece `policy_profile="live"` yazan tek yer)
bunun yerine düz `"EXECUTE"` literalini yazıyordu:

```python
decision_summary = DecisionSummary(
    decision="EXECUTE" if is_execute else "REJECT",   # ← sözleşmeye uymuyor
    ...
)
```

Journal'da "bu bir execute mi?" sorusunu `("EXECUTE_YES", "EXECUTE_NO")`
ile tam eşleştirerek soran en az 3 gerçek tüketici var:
- `monitoring/daily_review.py::_build_candidate_lists()` — günlük shadow
  raporunun **WOULD-TRADE** / **OBSERVATION ZONE** bölümü.
- `monitoring/metrics.py::_is_execute()` — drift monitoring rejection/EV
  metrikleri.
- `shadow_runner/reporting.py::_is_execute()` — profil sapma raporu.

Bunların hiçbiri canlı orchestrator'ın düz `"EXECUTE"` literaline hiç
eşleşmiyordu — yani canlı döngüde kaydedilen her gerçek EXECUTE kararı bu
tüketiciler için görünmezdi.

### Neden önemli
`monitoring/daily_review.py::generate_daily_review()`, bu botun bütün
"günlük strateji incelemesi" pratiğinin adını verdiği, `readiness_verdict.
json`'u üreten (`write_readiness_verdict()`) ve insan operatörün canlı
pilot onayından önce okuduğu tam da rapor. `format_daily_review()`'in
**WOULD-TRADE** bölümü, `shadow_runner/readiness.py::PilotConstraints.
mandatory_review_hours=24`'ün talep ettiği zorunlu insan incelemesinin en
somut girdisi — "bugün gerçekte kaç aday trade edilirdi, hangi EV'yle".
Bu hata yüzünden, gerçek pozitif-edge'li EXECUTE kararları olan bir günde
bile rapor "WOULD-TRADE: 0 candidates passed live_like gate today" diye
yazıyordu — operatörü botun aslında hiç trade sinyali üretmediğine
inandırıp yanlış bir "sinyal kalitesi düştü" sonucuna götürebilirdi, tam
da 56. çalışmanın (#91) dashboard-gerçek kapı anlaşmazlığıyla aynı sınıf
hata (sadece burada dashboard yerine günlük inceleme raporu).

`shadow_runner/summary_metrics.py::compute_summary_metrics()` — asıl
GO/NO_GO verdiktini besleyen fonksiyon — `!= "REJECT"` ile filtrelediği
için bu hatadan etkilenmiyordu; yani `readiness_verdict.json`'daki
verdict'in kendisi (control_plane/live_gate.py'nin okuduğu) bozulmamıştı.
Hata, verdict'i besleyen sayılara değil, aynı raporun insan-okur
bölümüne (would-trade listesi) hapsolmuştu — ama bu bölüm de tam olarak
"insan her 24 saatte bir incelemeli" gereksiniminin dayandığı kanıt.

### Somut senaryo (doğrulandı)
25 canlı shadow kaydı (5 EXECUTE + 20 REJECT) elle üretilip
`generate_daily_review()`'e verildi:
- **Düzeltme öncesi**: `live_metrics.execute_count=5` (doğru — metrikler
  `!= "REJECT"` kullanıyor) ama `report.would_trade` listesi **boş**
  (`len == 0`) — rapor operatöre "0 aday" diyor.
- **Düzeltme sonrası**: `report.would_trade` listesi **5 kayıt** içeriyor
  — gerçek durumu yansıtıyor.

### Düzeltme
`agents/orchestrator.py::_record_shadow_decisions()`: `is_execute` ise
`sig_match.direction`'a göre `"EXECUTE_YES"` veya `"EXECUTE_NO"` yazılıyor
(değilse hâlâ `"REJECT"`), `TradeDecisionType` sözleşmesine uyuyor.
Kod tabanında başka hiçbir yer düz `"EXECUTE"` literaline exact-match
yapmıyor (grep ile doğrulandı) — bu yüzden değişiklik sadece bu üç
tüketiciyi düzeltiyor, başka bir davranışı bozmuyor.

### Test
`tests/test_shadow_decision_value_matches_trade_decision_type.py` (yeni,
4 test):
1. `test_yes_execute_writes_execute_yes` — YES sinyali `"EXECUTE_YES"`
   yazıyor.
2. `test_no_execute_writes_execute_no` — NO sinyali `"EXECUTE_NO"`
   yazıyor.
3. `test_no_signal_produced_still_writes_reject` — sinyal üretilmeyen
   aday hâlâ `"REJECT"` yazıyor (regresyon değil).
4. `test_daily_review_would_trade_list_sees_real_live_executes` — uçtan
   uca: 5 EXECUTE + 20 REJECT canlı-şekilli kayıt → `generate_daily_
   review()`'in `would_trade` listesi 5 kayıt içeriyor.

Düzeltme öncesi (`git stash -- agents/orchestrator.py` ile doğrulandı):
**3 failed, 1 passed** (sadece REJECT sanity testi geçiyordu).
Düzeltme sonrası: **4 passed**.

### Doğrulama
`python3 -m pytest tests/` → **762 passed, 2 skipped** (758 taban + 4 yeni
test), sıfır regresyon.

## Kapsam dışı bırakılanlar
- `monitoring/metrics.py`/`monitoring/drift_monitor.py`'deki aynı sınıf
  `_is_execute()` string-eşleşme hatası da düzeltmenin yan etkisiyle artık
  doğru çalışıyor (aynı kök nedeni paylaşıyordu), ama bu modüller grep ile
  doğrulandı: repo genelinde hiçbir yerden import edilmiyor (`_gen_
  artifacts.py` hariç — o da tek seferlik bir rapor betiği, canlı yol
  değil). Yani bu modüller şu an ölü kod; düzeltme onları da iyileştirdi
  ama bu turun asıl hedefi değillerdi.
- `monitoring/readiness_checks.py` üst-docstring'indeki "1 FAIL →
  CONDITIONAL, 2+ FAIL → NO_GO" notu, gerçek kod ve testlerle ("Task 5.3:
  any FAIL blocks pilot") çelişiyor ama bu kasıtlı bir tasarım kararı
  (test'te açıkça belgeli) — sadece üstteki modül seviyesi docstring'i
  güncel değil. Davranış hatası değil, dokunulmadı.

## Sonuç
Üç bekleyen PR (#89 zararsız state senkronu, #90 reviewer risk katmanının
varsayılan olarak kapalı olması, #91 dashboard stop-loss yanlış payda)
sırayla doğrulanıp `main`'e alındı. Bugünkü 57. bulgu:
`agents/orchestrator.py::_record_shadow_decisions()`'ın canlı EXECUTE
kararlarını `TradeDecisionType` sözleşmesine uymayan bir literal ile
yazması, günlük shadow incelemesinin **WOULD-TRADE** bölümünü (insan
operatörün pilot onayı için okuduğu somut kanıt) sistematik olarak boş
gösteriyordu. GO/NO_GO verdiktinin kendisi etkilenmemişti, ama insan
inceleme adımının en somut girdisi güvenilmezdi. Düzeltme, canlı kayıt
yazıcısını doğru enum sözlüğüne taşıyarak kök nedeni çözdü.

## Sıradaki tur için notlar (devralınan + yeni)
- `data/trade_memory.json`'daki `CAPITAL_LOW` uyarısı ve sim-live WR farkı
  hâlâ araştırılmayı bekliyor (önceki turlardan devralınan).
- `er.passes_gate`/`fill_decision` hâlâ canlı emir döngüsüne bağlanmıyor
  (`agents/orchestrator.py:715+` sadece ham Bayesian `edge >= 0.05`
  kullanıyor) — NO-taraf fiyat hatası 54. turda düzeltildiğinden, birkaç
  günlük shadow verisiyle `passes_gate=False` oranı gözlemlendikten sonra
  MC_GATE ile aynı shadow→enforce deseniyle değerlendirilebilir
  (devralınan, hâlâ açık).
- `_record_shadow_decisions()`'daki `snapshot_age_seconds=0.0` sabiti hâlâ
  düzeltilmedi — kapı canlıya bağlanmadan önce gerçek piyasa verisi
  yaşıyla değiştirilmeli (devralınan, hâlâ açık).
- `control_plane/entry_window_guard.py::parse_market_start_time()`'ın
  `reference_year=2026` hardcoded varsayılanı (düşük öncelik, yıl
  dönümünde izlenmeli — devralınan, hâlâ açık).
- **Yeni**: `monitoring/metrics.py`/`monitoring/drift_monitor.py` şu an
  repo genelinde hiçbir yerden import edilmiyor (ölü kod, "Kapsam dışı"
  bölümüne bakınız) — ileride `DriftMonitor`'ı canlıya bağlama kararı
  verilirse önce bu modüllerin kendisi tekrar satır satır incelenmeli.
- **Yeni**: `monitoring/readiness_checks.py`'nin üst-docstring'indeki FAIL
  eşiği açıklaması ("1 FAIL → CONDITIONAL") gerçek davranışla
  (`assess_readiness()`: "1 FAIL → NO_GO", testlerle kasıtlı olarak
  doğrulanmış) çelişiyor — davranış hatası değil ama docstring güncellenip
  kafa karışıklığı önlenebilir (düşük öncelik, kod dışı).
