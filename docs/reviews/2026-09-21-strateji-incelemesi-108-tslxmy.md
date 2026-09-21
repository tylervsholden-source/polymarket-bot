# 108. Tur Strateji İncelemesi — 2026-09-21

## Kapsam
Planlı ("her gün stratejini gözden geçir, sermayenin %10'u kadar kazanma hedefi
için gereken kararları al") görevin bu turdaki çalıştırması.

## Durum tespiti
- `git fetch origin main` → HEAD zaten `65502af` (PR #202 / 107. tur ile
  senkron), açık PR yok.
- Tam test paketi: **1790 passed, 4 skipped** — 105-107. tur ile birebir aynı
  taban, regresyon yok.
- Bu sandbox'ta hâlâ canlı bot örneği yok: `data/status.json`,
  `data/control.json`, `data/positions.json` mevcut değil (yalnızca 2026-03
  tarihli `.bak`/`shadow_journal` durgun verileri var) → %10 sermaye hedefine
  karşı bu turdan doğrudan ölçülebilir ilerleme yine sağlanamıyor.
- **Zamanlama sıklığı sorunu hâlâ sürüyor**: son 24 saatte 52 commit
  (106. tur bunu ilk tespit edip kullanıcıya bildirmişti; 107. tur
  sürdüğünü doğruladı, bu tur da doğruluyor — düzeltilmemiş).

## Düzeltilen gerçek bug: test suite gerçek repo state'ini kirletiyordu
107. turun "başka bir oturum `data/autonomous_state.json`'ı canlı güncelledi"
gözlemi yanlış teşhis edilmişti — gerçek sebep **paralel oturum değil, testin
kendisiydi**. `agents/autonomous_engine.py`'deki `AutonomousDecisionEngine.
PERSISTENCE_FILE` sınıf değişkeni varsayılan olarak gerçek
`data/autonomous_state.json`'a işaret ediyor; `tests/` altında bu sınıfı
örnekleyen 10 dosyadan yalnızca 1'i (`test_research_agent_regime_wiring.py`)
bunu instance seviyesinde `tmp_path`'e yönlendiriyordu. Diğer 9 dosya
(`test_reduce_verdict_size_not_double_applied.py`,
`test_reviewer_verdict_skip_survives.py`,
`test_reviewer_veto_reaches_autonomous_engine.py`,
`test_streak_filter_skip_survives_high_risk.py`,
`test_adaptive_params_stale_without_signal.py`,
`test_adaptive_params_wiring.py`,
`test_autonomous_engine_no_direction_risk_penalty_removed.py`,
`test_drawdown_tracks_session_peak.py`,
`test_neutral_trades_not_counted_as_losses.py`) hiçbir override yapmadan
`AutonomousDecisionEngine()` çağırıyordu → her `pytest` çalıştırması gerçek
repo dosyasının `last_update` alanını (ve potansiyel olarak sayaçlarını)
değiştiriyordu. Bu, önceki her "günlük inceleme" turunda test suite
çalıştırıldığında görülen "beklenmedik uncommitted diff" / "başka oturum
aynı dosyayı güncelliyor" gözleminin gerçek kaynağıydı.

**Düzeltme**: `tests/conftest.py`'ye, repodaki mevcut `_pin_et_hour_gate` /
`_pin_position_manager_et_clock` desenini izleyen bir `autouse` fixture
eklendi (`_isolate_autonomous_engine_state`) — `AutonomousDecisionEngine.
PERSISTENCE_FILE`'ı `monkeypatch` ile her testte `tmp_path`'e yönlendiriyor.
9 dosyanın hiçbirine tek tek dokunmaya gerek kalmadı (tek merkezi düzeltme
noktası, `test_research_agent_regime_wiring.py`'nin instance-level
override'ı ile çakışmıyor — o zaten kendi `tmp_path`'ini kullanmaya devam
ediyor). Doğrulama: düzeltme öncesi `git checkout -- data/
autonomous_state.json` ile dosya committed haline döndürüldü, düzeltme
sonrası tam suite tekrar çalıştırıldı (**1790 passed, 4 skipped**, regresyon
yok) ve `git status` artık `data/autonomous_state.json`'da hiçbir değişiklik
göstermiyor.

## Yükseltilen bulgu: Onay kuyruğu / doğrudan emir yolu çelişkisi (104. turdan beri açık, ilk kez kullanıcıya bildiriliyor)

104-107. turlar bunu "kullanıcı kararı bekleyen açık mimari soru" olarak not
düşüp geçti ama hiçbiri kullanıcıya bildirim göndermedi (106'nın bildirimi
yalnızca zamanlama sıklığı hakkındaydı). Bu tur kodu doğrudan okuyarak
doğruladı — bu bir varsayım değil, doğrulanmış bir çelişki:

- `docs/APPROVAL_WORKFLOW_SPEC.md`: *"Her canlı emrin dashboard'dan
  onaylanması zorunludur. Doğrudan emir verme yolu kapatılmıştır."*
- `agents/orchestrator.py` `_cycle()` içinde (~satır 1137, yorum: `── DOĞRUDAN
  EMİR VER (onay kuyruğu bypass) ──`): ArbitrageEngine'in ürettiği her sinyal,
  yalnızca otomatik 11-nokta LiveGate kontrolünden geçtikten sonra
  `is_approved=True` **sabit değeriyle** doğrudan `client.place_order()`'a
  gidiyor. `control_plane.approval_queue.enqueue()` fonksiyonu import
  ediliyor (satır 42) ama **hiçbir yerde çağrılmıyor** — yani PENDING
  kuyruğuna hiçbir AI sinyali hiç girmiyor.
- Ayrı bir fonksiyon (`_execute_approved_orders`, satır 1345) gerçekten
  `data/pending_orders.json`'daki APPROVED kayıtları LiveGate'ten geçirip
  execute ediyor — ama bu yol yalnızca dashboard'dan **manuel** girilen
  emirler için kullanılıyor gibi görünüyor (AI sinyalleri hiç oraya
  düşmüyor).

**Sonuç:** Canlı modda (`live_trading=true`) AI sinyalleri, belgelenmiş
güvenlik kontrolünün (her emrin insan onayından geçmesi) aksine, otomatik
LiveGate kontrolleri dışında hiçbir insan onayı olmadan doğrudan
gerçekleştiriliyor. Bu iki şekilde okunabilir:
1. **Spec güncel değil** — bot kasıtlı olarak tam otonom çalışacak şekilde
   tasarlandı (CLAUDE.md'nin "otonom karar motoru" vizyonuyla tutarlı),
   dokümantasyon güncellenmeli.
2. **Kod güvenlik açığı içeriyor** — kullanıcı gerçekten her canlı emrin
   onaydan geçmesini istiyor, mevcut davranış istenmeyen risk taşıyor.

Bu, sermaye/güvenlik etkisi olan ve yalnızca kullanıcının karar
verebileceği bir mimari tercih olduğu için **bu turda kod değişikliği
yapılmadı** — otomatik bir tur, gerçek parayla ilgili bu tür bir
davranış değişikliğini tek taraflı karara bağlayıp sessizce push
etmemeli. Kullanıcıya bu turda ilk kez doğrudan bildirim gönderildi.

## Diğer devreden açık sorular (değişmedi)
(2) `enhanced_signals.py` confluence/risk-flag'e hiç bağlı değil,
(3) `copytrade.py` ölü kod, (4) `top_trader_signal.py`'de `TOP_TRADERS`
listesi kullanılmıyor, (5) `write_readiness_verdict()` manuel-gate sorusu.
Hiçbiri sermaye güvenliğini bu turdaki bulgu kadar doğrudan etkilemiyor,
bu yüzda düşük öncelikli kaldı.

## Sonuç
Kod tabanı sağlıklı (1790/1790 + test-izolasyon düzeltmesi sonrası da
1790/1790, regresyon yok). Bu turda gerçek bir bug düzeltildi (test suite'in
repo state'ini kirletmesi) — bu, önceki turların "paralel oturum" teşhisini
de düzeltiyor. Asıl aksiyon kullanıcıda: (a) zamanlama
sıklığı hâlâ günlük değil ~saatlik/daha sık, (b) onay kuyruğu/doğrudan emir
çelişkisinin hangi yönde çözüleceği (spec'i gevşet mi, kodu sıkılaştır mı).
