# Günlük Strateji İncelemesi — 2026-09-18 (83. tur)

## Durum
Oturum başında `origin/main` = bu branch = `d2181a1` (#144, 82. inceleme
sonrası — OPT-6 loss-slot cooldown'un sim modunda çalışmadığı bulunup
düzeltilmiş, 848 passed/2 skipped baseline'ı). Açık PR yoktu. Ortamda
`loguru`/`httpx`/`pandas`/`numpy`/`pytest-asyncio`/`py-clob-client` eksikti
(requirements.txt'de listeli ama kurulu değildi) — kurulduktan sonra
baseline: `python3 -m pytest tests/ -q` → **850 passed, 2 skipped**
(`crypto_directional/` hariç — sklearn eksik, canlı yolla ilgisiz; sayı
farkı 82. turdaki 848'e göre sadece bağımlılık kurulumundan, koddan değil).

## Bu turda yapılanlar
Skeptik, taze bir gözden geçirme; talimatta belirtilen state/ordering ve
capital-accounting bug sınıfına odaklanıldı. `agents/orchestrator.py`,
`core/position_manager.py`, `agents/autonomous_engine.py`,
`agents/trade_analyzer.py`, `strategies/kelly_criterion.py` satır satır
okundu; özellikle 82. turun OPT-6 fix'inin dokunduğu
`_update_loss_streak()` fonksiyonu ve `_cycle()`'ın `closed_trades`
değişkenini tüketen tüm noktalar (`kelly.update_streak()`,
`walk_forward.validate()`, `autonomous_engine.evaluate()`) izlendi.

### Bulunan ve düzeltilen hata: OPT-7 ardışık-NO-WIN bounce guard'ı sim/paper modunda hiç çalışmıyordu (82. turun OPT-6 fix'iyle AYNI kök neden, kardeş kod bloğunda kalmış)

`Orchestrator._update_loss_streak()`'in en başında tanımlanan
`closed = self.position_manager.data.get("closed", [])` satırı koşulsuz
olarak SADECE gerçek CLOB pozisyonlarını okuyordu. 82. turda tam olarak bu
aynı kök neden — `_is_live_trading()==False` iken (botun varsayılan/güncel
çalışma modu, bkz. CLAUDE.md) bu listenin HER ZAMAN boş kaldığı, gerçek
trade'lerin hiç açılmadığı, sim trade'lerin bunun yerine
`self._sim_results`'a yazıldığı — teşhis edilip **SADECE** aynı fonksiyonun
altındaki OPT-6 `loss_slot_source` değişkenine düzeltme uygulanmıştı. Ama
fonksiyonun en başındaki `closed` değişkeni (yukarıdaki satır) hiç
dokunulmadan kaldı — ve bu değişken, OPT-6'dan önce iki yerde daha
kullanılıyordu:

1. `_consecutive_losses` hesabı (satır ~2054-2064) — CIRCUIT_BREAKER
   kaldırıldığı için artık sadece loglama amaçlı, davranışsal etkisi yok.
2. **OPT-7 `_consecutive_wins_per_coin` hesabı** (satır ~2066-2090) —
   `_cycle()`'daki CONSEC_WIN_GUARD tarafından tüketiliyor: aynı coin'de
   **3+ ardışık NO WIN → sinyali tamamen SKIP et**, **2 ardışık NO WIN →
   half-kelly küçült** (CLAUDE.md'nin belgelediği "2+ ardışık NO-win
   periyottan sonra %100 bounce geliyor" pattern'inin doğrudan uygulaması).

Sonuç: `closed` sim/paper modunda hep boş kaldığı için
`_consecutive_wins_per_coin` hiçbir zaman doldurulamıyordu — bot aynı
coin'de art arda kaç kez NO WIN alırsa alsın (3, 5, 10...), CONSEC_WIN_GUARD
hiçbir zaman tetiklenmiyordu. CLAUDE.md'nin dokümante ettiği en kritik
bounce-risk korumalarından biri, tam da botun fiili çalışma modunda (sim/
paper), tam da koruması gereken senaryoda (art arda NO kazançlarının
ardından gelen bounce) sessizce devre dışıydı.

İkinci, birleşen bir sorun daha vardı: `closed`'ı doğru kaynaktan
(`self._sim_results`) okumaya başlasak bile, sim-mode trade kayıtları
`"outcome"` alanı DEĞİL `"direction"` alanı taşıyor (73. turda tam tersi
yönde — gerçek trade'lerin "outcome" taşıdığı, "direction"ın sadece sim
kayıtlarında olduğu — zaten bir kez düzeltilmişti, bkz.
`tests/test_consec_win_guard_field.py`). Kod sadece `"outcome"` okuyordu;
bu yüzden `closed` kaynağı düzeltildikten sonra bile sim trade'ler için
`_side` hep boş string kalıp OPT-7 hiç tetiklenmezdi.

**Fix** (`agents/orchestrator.py::_update_loss_streak`):
1. `closed`'ı `_is_live_trading()`'e göre seç (canlıda `position_manager.
   data["closed"]`, sim'de `self._sim_results` — 82. turun OPT-6 için
   kurduğu ayrımın ta kendisi, artık fonksiyonun tek kaynağı).
2. OPT-6'nın kendi `loss_slot_source` seçimini kaldırdım — artık gereksiz,
   `loss_slot_source = closed` olarak tekilleştirdi (aynı davranış, ama tek
   kaynak — bir daha ayrışmasın).
3. OPT-7'nin taraf kontrolünü `trade.get("outcome") or trade.get("direction")`
   olacak şekilde genişlettim — her iki kayıt şeması da (gerçek/sim) doğru
   okunuyor.

**Test**: `tests/test_opt7_sim_mode_consec_win_guard.py` (3 test) — sim-mode
senaryoları pre-fix kaynağa karşı fail ediyor (`AssertionError: assert None
== 3` / `== 2`), fix sonrası geçiyor; live-mode izolasyon testi (stray
sim_results canlı moda sızmamalı) baştan da geçiyordu, regresyon yok diye
ekli. Mevcut `tests/test_consec_win_guard_field.py`'nin canlı-mod
fixture'ı artık `_is_live_trading=True` stub'ı taşıyor (önceden implicit
olarak varsayılan `False`/sim davranışına güveniyordu — kodun yeni,
doğru davranışıyla açıkça uyumlu hale getirildi, assertion'lar
değişmedi). Tam suite: **853 passed, 2 skipped** (was 850/2).

Not: İlk denemede `closed`'ı "gerçek boşsa sim'e düş" (`or` fallback,
`_is_live_trading()` sorgusuz) şeklinde basitleştirmeyi denedim, ama bu
`tests/test_opt6_sim_mode_loss_slot_survives_rebuild.py::
test_live_mode_ignores_sim_results` testini kırdı: canlı modda henüz hiç
kapanış yokken (closed=[]) eskiden kalma bir `_sim_results` varsa (örn.
sim'den live'a geçiş), bu fallback onu yanlışlıkla canlı muhasebeye
sızdırıyordu. `_is_live_trading()` bazlı seçime dönüldü — 82. turun kurduğu
davranış sözleşmesiyle birebir tutarlı ve testi kırmıyor.

### İncelenip reddedilen adaylar

1. **`kelly.update_streak(closed_trades)` / `walk_forward.validate(closed_trades)`
   / `autonomous_engine.evaluate(closed_trades=...)`'nin sim modunda da aynı
   `closed_trades = position_manager.data.get("closed", [])` kaynağını
   kullanması** (`_cycle()` satır ~611) — bu OPT-7 ile TAM AYNI kök neden
   sınıfı (sim modunda hep boş liste) ve gerçek: Dynamic Kelly streak
   multiplier, walk-forward "STOP" önerisi ve AutonomousDecisionEngine'in
   `_update_performance()`'ı (`if closed_trades:` guard'ı yüzünden)
   sim/paper modunda asla çalışmıyor — win_rate/drawdown/consecutive_losses/
   consecutive_wins performans anlık görüntüsü kalıcı olarak sıfırda kalıyor,
   STREAK_LOSS/STREAK_WIN/DRAWDOWN korumaları hiç tetiklenmiyor. Bunu
   doğruladım ama BU TURDA düzeltmedim: kapsam çok daha geniş (3 ayrı
   tüketici, farklı şema varsayımları — walk_forward.validate()'in ve
   autonomous_engine._update_performance()'ın sim kayıtlarının eksik
   alanlarıyla (örn. "pnl" yok) nasıl davranacağını ayrı ayrı doğrulamak
   gerekiyor) ve "bir turda bir minimal fix" ilkesini aşıyor. Bir sonraki
   tura not olarak bırakıyorum (aşağıda).
2. **`_finalize_cycle()`, `ReentryGuard.mark_closed()`,
   `control_plane/approval_queue.py`, `calibration/`/`signal_bridge`/
   `shadow_runner`, `ml_classifier.py` NEUTRAL etiketleme,
   `coordinator.py` merge/REDUCE mantığı** — 82. turda derinlemesine
   incelenip reddedilmişti, bu turda yeniden litige edilmedi (talimat
   gereği), kod tarafında da değişiklik yok.
3. `core/position_manager.py::update_positions`/`_check_order_filled`/
   `_close_position` — satır satır okundu, tüm "BUG:" yorumları önceki
   turların gerçek/doğrulanmış düzeltmelerine ait; yeni bir kusur
   bulunamadı.
4. `strategies/kelly_criterion.py` — Kelly formülü, streak multiplier,
   regime/capital-preservation ayarlamaları elle doğrulandı, doğru.

## Sonuç
Bir gerçek hata bulundu ve düzeltildi: OPT-7 ardışık-NO-WIN bounce guard'ı
(`_consecutive_wins_per_coin`), 82. turda OPT-6 için düzeltilen AYNI
sim/live state-kaynağı ayrımına sahip olmadığı için sim/paper modunda
(botun fiili çalışma biçimi) hiçbir zaman tetiklenmiyordu — ek olarak
sim-mode kayıtlarının "direction" alanını da (yalnızca "outcome" yerine)
okuyacak şekilde genişletildi. Tam test suite **853 passed, 2 skipped**
(baseline 850/2 + 3 yeni test). CLAUDE.md'nin risk kuralları (max %20
pozisyon, günlük -%15 stop, max 5 açık pozisyon, min $5,000 hacim, min 0.05
edge) kod tarafında değiştirilmedi.

## Sıradaki tur için notlar
- **Capital-risk-relevant, kapsam dışı bırakıldı**: `_cycle()`'ın
  `closed_trades = position_manager.data.get("closed", [])` değişkeni
  (satır ~611) — `kelly.update_streak()`, `walk_forward.validate()` ve
  `autonomous_engine.evaluate(closed_trades=...)`'a besleniyor — sim/paper
  modunda HER ZAMAN boş. Bunun pratik etkisi: `AutonomousDecisionEngine.
  _update_performance()` `if closed_trades:` guard'ı yüzünden sim modunda
  asla çalışmıyor, yani `PerformanceSnapshot` (win_rate, consecutive_losses/
  wins, drawdown_pct) kalıcı olarak sıfırda/varsayılanda kalıyor ve
  STREAK_LOSS_THRESHOLD/STREAK_WIN_THRESHOLD/DRAWDOWN_DANGER korumaları
  (CLAUDE.md'nin "Otonom Karar Akışı"nda belgelediği "Loss streak / drawdown
  koruması") hiç devreye girmiyor; aynı şekilde Dynamic Kelly streak
  multiplier ve walk-forward STOP önerisi de sim modunda hep nötr (1.0/
  aktif değil) kalıyor. Düzeltme OPT-7 ile aynı desende olurdu
  (`_is_live_trading()`'e göre `closed`/`self._sim_results` seç) ama üç
  farklı tüketicinin sim-kaydı şema farklarını (özellikle `pnl` alanının
  sim kayıtlarında hiç olmaması — session_pnl/hourly_pnl hesaplarını
  etkiler) ayrı ayrı doğrulamak gerektiğinden bir sonraki tura bırakıldı.
- `agents/whale_tracker.py:48`'deki `market` query param sorusu hâlâ
  doğrulanamadı — bu oturumda da `data-api.polymarket.com`'a erişim
  denendi, aynı şekilde engellendi (`curl: CONNECT tunnel failed, response
  403`, EGRESS_BLOCKED). Sıradaki oturumlara devrediliyor.
- `control_plane/approval_queue.py`'nin `enqueue()`'unun ölü kod olduğu
  (82. turda reddedilen aday) hâlâ geçerli, mimari karar olarak kapsam
  dışı bırakılmaya devam ediyor.
