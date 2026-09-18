# Günlük Strateji İncelemesi — 2026-09-18 (80. tur)

## Durum
Oturum başında `origin/main` = bu branch = `9c6ee20` (#140, 79. inceleme
sonrası — "thorough live/shadow path sweep, no new bug found"). Açık PR
yoktu. Baseline test: `python3 -m pytest tests/ -q` → 844 passed, 2 skipped
(`crypto_directional/` hariç — sklearn eksik, canlı yolla ilgisiz).

## Bu turda yapılanlar
Skeptik, taze bir gözden geçirme; özellikle son birkaç commit'in bug
sınıfına (LEAD_LAG boost bypass, CLOB-sync outcome hardcode, risk snapshot
sıralaması, TradeAnalyzer NEUTRAL puanlama, edge cost modeli, autonomous
engine stale performance) ve state'in cycle'lar arası okunup yazıldığı
noktalara odaklanıldı.

### Bulunan ve düzeltilen hata: OPT-6 loss-slot cooldown sim modunda hiç çalışmıyordu

`Orchestrator._update_loss_streak()`'in OPT-6 bloğu (`agents/orchestrator.py`)
her cycle'da koşulsuz olarak `self._last_loss_slots.clear()` yapıp SADECE
`position_manager.data["closed"]` (gerçek CLOB pozisyonları) üzerinden
rebuild ediyordu. Ama bu ortamda (ve CLAUDE.md/docs/architecture.md'nin
belgelediği güncel varsayılan çalışma biçiminde) `live_trading=false` —
gerçek pozisyonlar hiç açılmıyor; `_cycle()`'ın `else` dalı trade'leri
`self._sim_trades`/`self._sim_results`'a yazıyor (position_manager'a hiç
dokunmadan). `_check_sim_resolutions()` — aynı cycle'da
`_update_loss_streak()`'ten hemen önce çalışıyor — bir sim trade LOSS ile
resolve olduğunda ilgili zaman dilimini `_last_loss_slots`'a incremental
olarak ekliyordu (`LOSS_SLOT_TRACK` log satırı). Ama hemen ardından
`_update_loss_streak()` çalışıp `_last_loss_slots.clear()` + sadece
(sim modunda boş/ilgisiz) `closed` listesinden rebuild yaptığı için, az
önce eklenen sim-mode loss slot'u aynı cycle içinde sessizce siliniyordu.
Sonuç: `_limit_coins_per_period()`'ın tükettiği `_is_adjacent_to_loss_slot()`
kontrolü sim/paper modunda (botun şu anki fiili çalışma modu) hiçbir zaman
tetiklenmiyordu — CLAUDE.md'nin "OPT-6: Loss Slot Cooldown — Kayıp olan
slot'tan sonraki slot'u atla (dead cat bounce 1 periyot sürüyor)" diye
belgelediği koruma sim modunda fiilen devre dışıydı, tam da dead-cat-bounce
riskinin en yüksek olduğu anda.

Bu, 12 Eylül'deki OPT-6 wiring düzeltmesiyle (o zaman `_is_adjacent_to_loss_slot()`
hiç çağrılmıyordu) aynı "kontrol var ama canlı/aktif yola tam bağlı değil"
deseninin farklı bir kökten kaynaklanan, o zamandan beri hiçbir incelemede
adı geçmeyen bir tekrarı — bu sefer wiring değil, iki ayrı trade-kaydı
deposu (`position_manager.data["closed"]` vs `self._sim_results`) arasındaki
state-kaynağı uyuşmazlığı.

**Fix**: `loss_slot_source`'u canlı modda `closed`, sim modda
`self._sim_results` olacak şekilde `self._is_live_trading()`'e göre seç
(ikisi aynı anda dolu olmaz, `_cycle()`'ın kendi dallanmasıyla tutarlı).

**Test**: `tests/test_opt6_sim_mode_loss_slot_survives_rebuild.py` (4 test) —
sim-mode senaryosu pre-fix kaynağa karşı fail ediyor (`AssertionError:
assert '8:05AM-8:10AM' in set()`), fix sonrası geçiyor; live-mode senaryoları
regresyon yok diye ekli. Tam suite: **848 passed, 2 skipped** (was 844/2).

### İncelenip reddedilen adaylar

1. **`_finalize_cycle()`'ın `update_positions()`'ı ikinci kez çağırması**
   (`agents/orchestrator.py:1478`, `_cycle()`'ın başındaki `:598`'den sonra).
   İzlendi: `update_positions()` idempotent (kapanan pozisyonlar
   `self.data["positions"]`'tan siliniyor), ikinci çağrı sadece bu cycle
   içinde AÇILAN bir pozisyonun hemen ardından resolve olması gibi nadir bir
   durumu yakalıyor; `_analyze_new_closed_trades()` `_cycle()` tamamen
   bittikten sonra çalıştığı için her iki çağrının kapattığı trade'leri de
   görüyor. Sıralama bug'ı değil.
2. **`ReentryGuard.mark_closed()`'in sadece `_finalize_cycle()`'daki ikinci
   `update_positions()` çağrısının kapattığı pozisyonlar için çağrılması** —
   `_cycle()` başındaki ilk `update_positions()`'ın kapattığı pozisyonlar
   için hiç çağrılmıyor. İzlendi: aynı market_id zaten `mark_traded()` ile
   giriş anında cooldown'a ekleniyor (`agents/orchestrator.py:321/1045/1312`),
   ve bu botun 5/15dk'lık pencere marketleri zaten benzersiz market_id'ler
   (aynı pencereye ikinci kez giriş fiziksel olarak imkansız, her pencere
   kendi market_id'sini taşıyor) — `mark_closed()` defense-in-depth, tek
   gerçek koruma noktası zaten `mark_traded()`. Sermayeyi/kararı etkilemiyor.
3. **`core/approval_queue.py` → `control_plane/approval_queue.py`'nin
   `enqueue()`'u** — `agents/orchestrator.py` import ediyor
   (`_enqueue_order`) ama hiçbir yerden çağırmıyor; `core/web_server.py` da
   sadece `get_pending`/`get_all`/`approve`/`reject` kullanıyor. Tüm
   onay-kuyruğu mekanizması (INC-2026-03-15-001 için yazılmış) fiilen ölü —
   hiçbir kod yolu yeni PENDING emir enqueue etmiyor. `edge_model.
   execution_cost()`/`latency_arb` spike-path ile aynı "kullanılmayan
   alt-sistem" sınıfı, düzeltme gerektiren bir davranış farkı değil.
4. **`calibration/`, `signal_bridge/`, `shadow_runner/runner.py`
   (`ShadowRunner`/`calibration.decision_policy.decide()`)** — repo genelinde
   grep ile doğrulandı: `agents/`, `core/`, `strategies/`, `control_plane/`
   veya `main.py` hiçbiri bu modülleri import etmiyor. Canlı yol
   (`agents/orchestrator.py::_record_shadow_decisions`) kendi
   `ShadowDecisionRecord`'unu elle kuruyor, `shadow_runner/runner.py`'nin
   "Shadow runner uses the SAME decide() as live" iddiasının aksine.
   Mimari bir ayrışma/belge güncelliğini yitirmiş iddia, ama execution_cost()
   gibi zaten kabul edilmiş bir dead-code kategorisi — davranışsal bir bug
   değil.
5. **`strategies/ml_classifier.py` NEUTRAL etiketleme, `_extract_features`
   train/serve tutarlılığı** — 29. ve 68. incelemelerde zaten düzeltilmiş;
   elle yeniden doğrulandı, `_result_label()`/`_build_training_set()`/
   `signal_price` fallback zinciri hâlâ doğru, dokunulmadı.
6. **`agents/subagents/coordinator.py` merge/re-enrich/REDUCE mantığı** —
   22./52. incelemelerin fix'leri (REDUCE'ın sig.size'a önceden
   uygulanmaması, re-enrich sonrası re-sort) hâlâ yerinde; `AutonomousDecisionEngine.
   evaluate()`'in STREAK_FILTER SKIP korumasını (HIGH_RISK/CRITICAL/VETO
   dallarının SKIP'i ezmemesi) elle yeniden izlendi, doğru.

## Sonuç
Bir gerçek hata bulundu ve düzeltildi: OPT-6 loss-slot cooldown sim/paper
modunda `_update_loss_streak()`'in yanlış kaynaktan rebuild yapması yüzünden
etkisizdi. Altı aday derinlemesine incelendi ve reddedildi (yukarıda detaylı).
Tam test suite **848 passed, 2 skipped** (baseline 844/2 + 4 yeni test).
CLAUDE.md'nin risk kuralları (max %20 pozisyon, günlük -%15 stop, max 5 açık
pozisyon, min $5,000 hacim, min 0.05 edge) kod tarafında değiştirilmedi.

## Sıradaki tur için notlar
- `agents/whale_tracker.py:48`'deki `market` query param sorusu hâlâ
  doğrulanamadı (bu oturumun ağ politikası `data-api.polymarket.com`'u
  engelliyor) — sıradaki oturumlara devrediliyor.
- `control_plane/approval_queue.py`'nin `enqueue()`'unun hiçbir yerden
  çağrılmadığı (bkz. reddedilen aday #3) belgelenmedi; bu fiilen ölü bir
  insan-onay alt sistemi olduğu için ileride kaldırılması/aktive edilmesi
  ayrı bir mimari karar olarak değerlendirilebilir, bu turun kapsamı
  dışında bırakıldı.
