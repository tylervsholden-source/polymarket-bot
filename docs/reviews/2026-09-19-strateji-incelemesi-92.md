# Günlük Strateji İncelemesi — 2026-09-19 (92. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = `d93723b` (#162, 90. inceleme sonrası). Tek
açık PR vardı: **#163** ("91. tur"), başka bir eşzamanlı oturum tarafından
`d93723b` üzerine açılmıştı — `control_plane/entry_window_guard.py`'nin 4
saatlik horizon'u desteklememesi (90. turun `staleness_penalty.py` bulgusuyla
aynı hata sınıfı: `orchestrator.py`'nin zaten birinci sınıf desteklediği bir
horizon, tek bir gate'te sessizce "unsupported" kalıyordu).

## Bu turda yapılanlar

### 1. PR #163 doğrulama ve merge
Diff tek tek okundu; PR'ın iddiaları kaynak koda karşı bağımsız olarak
doğrulandı (`agents/orchestrator.py:204`'teki yorum ve `_shadow_detect_horizon()`'un
`"4 hour"/"4h" → 240` eşlemesi gerçekten var). PR'ın kendi test ortamı bu
oturumda yoktu (`loguru`, `pytest` vb. eksikti); `pip install -r
requirements.txt` ile kuruldu, ardından hedef testler ayrı bir worktree'de
bağımsız çalıştırıldı: `tests/test_entry_window_policy.py` +
`test_entry_window_live_gate.py` + `test_entry_window_guard_year_rollover.py`
→ 47 passed; tam suite → 1731 passed, 4 skipped (PR'ın iddia ettiğiyle
birebir eşleşti). `mergeable_state=clean`, check yoktu (repo'da CI
tanımlı değil, önceki turlarla tutarlı). Merge edildi (`06ea309`).

### 2. Yeni bulunan ve düzeltilen hata: `_record_shadow_decisions()` execution_realism'in kendi `passes_gate` sonucunu hiç okumuyordu
91. turun "sıradaki tur" notunda bırakılan açık maddeyi (`shadow_runner/
summary_metrics.py`'nin `SUSPICIOUS_UNDERROUND`/`STALE_PRICING`/
`PARTIAL_FILL_REJECTED` string'lerini arayan üç metriği) takip ederken, kök
neden beklenenden daha derin çıktı.

`agents/orchestrator.py::_record_shadow_decisions()`, her EXECUTE adayı için
`compute_executable_ev(..., policy_mode="live")` çağırıyor (satır ~2673) ve
sonuçtan yalnızca `fill_fraction`/`executable_ev` alıyordu —
`er.passes_gate`'i (ve altındaki `fill_sim.fill_decision`/
`staleness.should_reject` detayını) tamamen atıyordu. `execution_realism/
core.py`'nin kendi docstring'i açıkça şunu söylüyor: canlı modda bir PARTIAL
fill "`decide()`'da `PARTIAL_FILL_REJECTED` olarak reddedilir" — ama hiçbir
çağıran bu eşlemeyi hiç yapmıyordu. Sonuç: sinyal üreten her aday, execution
reality ne derse desin (UNFILLABLE, PARTIAL fill, EXPIRED/stale fiyat),
`decision=EXECUTE_YES/NO`, `passes_final_gate=True`, `rejection_reason=None`
olarak yazılıyordu.

Bu, `shadow_runner/summary_metrics.py`'nin `partial_fill_rejection_rate` ve
`stale_pricing_rate`'ini (`rejection_counts.get("PARTIAL_FILL_REJECTED"/
"STALE_PRICING", 0) / total`) yapısal olarak sıfıra sabitliyordu — bu
reason'lar EXECUTE yolunda asla üretilemiyordu. `monitoring/
readiness_checks.py::check_partial_fill_rejection_rate()` (>%40 → FAIL) ve
`check_stale_pricing_rate()` (>%30 → FAIL), `control_plane/live_gate.py`'nin
gerçek emir vermeyi gate'lediği `TINY_PILOT_CANDIDATE` doğrulamasının
parçası — yani bu iki BLOCKER/FAIL seviyeli kontrol, gerçek orderbook
durumu ne olursa olsun her zaman GREEN okuyordu (91. turun bulduğu
`staleness_penalty.py`/90. turun bulduğu horizon sorunlarıyla aynı aile:
readiness verdict'i sessizce GO yönünde çarpıtan bir metrik boşluğu).

`SUSPICIOUS_UNDERROUND` tespiti ayrı bir konu: bu mantık yalnızca
`calibration/decision_policy.py`'de var (89./90. turlarda ölü kod olarak
doğrulanmış, `orchestrator.py`/`coordinator.py` import zincirinden hiç
erişilmiyor) ve config-bağımlı, çok parçalı bir kontrol
(`ask_sum`/`bid_sum`/tek-taraflı ask bantları). Bu modülü canlı yola
bağlamak veya mantığını `orchestrator.py`'ye kopyalamak bu turun kapsamı
için fazla riskli/geniş görüldü — **bilinçli olarak sonraki tura
bırakıldı** (aşağıya not edildi).

**Düzeltme (`agents/orchestrator.py::_record_shadow_decisions()`):**
- `er.passes_gate is False` olduğunda, `er.fill_sim.fill_decision`/
  `er.staleness.should_reject`'e göre spesifik neden belirlenip
  (`PARTIAL_FILL_REJECTED` / `UNFILLABLE` / `STALE_PRICING` /
  `EXECUTABLE_EV_BELOW_THRESHOLD`) kayıt REJECT'e düşürülüyor:
  `decision="REJECT"`, `passes_final_gate=False`,
  `intended_size_usdc_used=0.0`, `rejection_reason=<neden>`.
  `execution_adjusted_ev`/`fill_fraction` denetim izi için korunuyor (Phase
  11 politikasının kendi ifadesiyle: "EV is still computed for audit
  trail").
- Fillable + taze bir aday için davranış değişmedi (regresyon testiyle
  doğrulandı).

**Testler** (`tests/test_shadow_execution_realism_gate.py`, yeni dosya):
- `test_generously_liquid_fresh_trade_still_records_as_execute` — regresyon
  kontrolü, davranış değişmemeli.
- `test_unfillable_liquidity_downgrades_execute_to_reject` — liquidity=5,
  size=3 → ratio=%60 → UNFILLABLE.
- `test_partial_fill_downgrades_execute_to_partial_fill_rejected` —
  liquidity=9 → ratio=%33 → PARTIAL → canlı modda hard-reject.
- `test_stale_pricing_downgrades_execute_to_stale_pricing_reject` —
  `market_fetch_utc` 2 saat önce → 5dk horizon için EXPIRED zone.
- `test_partial_fill_rejection_is_now_visible_to_summary_metrics` —
  uçtan uca: `shadow_runner.summary_metrics.compute_summary_metrics()`'in
  artık bu reddi gerçekten saydığı doğrulandı (`partial_fill_rejected_count
  == 1`, önceden hep 0'dı).

**Doğrulama:**
- Sadece `agents/orchestrator.py` geri alınıp yeni testler eski koda karşı
  çalıştırıldı: 5 testten 4'ü fail etti (regresyon testi hariç, beklenen
  davranış) — testlerin gerçek hatayı yakaladığı doğrulandı.
- `python3 -m pytest tests/test_shadow_execution_realism_gate.py -q` → 5
  passed.
- Tam test suite: **1736 passed, 4 skipped** (baseline 1731 + 5 yeni test,
  sıfır regresyon).
- `data/autonomous_state.json`'daki test yan etkisi commit öncesi geri
  alındı.

## Sıradaki tur için notlar
- **Yeni açık madde:** `SUSPICIOUS_UNDERROUND` tespiti hâlâ canlı yola
  bağlı değil (yukarıda açıklandı). Doğru çözüm muhtemelen
  `calibration/decision_policy.py::_check_binary_sanity()`'yi olduğu gibi
  içe aktarıp `_record_shadow_decisions()`'da `ask_yes`/`ask_no`/`bid_yes`/
  `bid_no` ile çağırmak (mantığı kopyalamamak — config-bağımlı bantlar var,
  kopya sürüklenmeye açık). `calibration/*`'ın gerçekten dead-code olup
  olmadığı ve içe aktarmanın başka bağımlılık getirip getirmeyeceği bir
  sonraki turda değerlendirilmeli.
- Bu turda tekrar teyit edilen ölü kod: değişiklik yok, 89.-91. turların
  listesi geçerliliğini koruyor.
- `agents/whale_tracker.py:48`'deki `market` query param sorusu hâlâ
  doğrulanamadı — bu oturumda da `data-api.polymarket.com`'a ağ erişimi
  engelliydi (proxy 403). Kalıcı bir açık madde.
- Zamanlama sıklığı sorunu (86./89. turlarda kullanıcıya bildirildi) bu
  oturumda tekrar ayrıca bildirilmedi — daha önce net şekilde raporlandığı
  için gereksiz tekrar olur; sorun hâlâ hesap düzeyinde, bu oturumun
  erişemediği bir zamanlayıcı ayarı.
