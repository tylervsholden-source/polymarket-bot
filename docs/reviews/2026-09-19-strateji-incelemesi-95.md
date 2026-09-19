# Günlük Strateji İncelemesi — 2026-09-19 (95. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = `4bc75dd` (#166, 93. konsolidasyon turu
sonrası), ama `claude/brave-faraday-2mrfts` dalında **94. turun** açık,
birleştirilmemiş PR'ı (#167) bulundu — bir önceki oturum tarafından
oluşturulmuş, henüz merge edilmemişti.

## Bu turda yapılanlar

### 1. PR #167 (94. tur) doğrulanıp merge edildi
`agents/orchestrator.py::_cycle()`'daki fix (`open_count`/`directional_count`
sim/paper modda cycle'lar arası devreden sim pozisyonlarını sayıyor) bağımsız
olarak doğrulandı:
- Diff `main`'in güncel haline karşı incelendi (15 satır, iddia edilenle
  birebir).
- `self._sim_trades`'in `_check_sim_resolutions()` tarafından her cycle'da
  `still_open` ile güncellendiği, dolayısıyla fix'in doğru noktada
  (sayaçların sıfırdan hesaplandığı satırların hemen altında, approved_signals
  döngüsünden önce) uygulandığı teyit edildi.
- `pytest tests/test_sim_cross_cycle_position_caps.py
  tests/test_sim_mode_cycle_budget_counters.py` → 8/8 passed.
- Tam suite: **1743 passed, 4 skipped** (PR'ın iddia ettiğiyle birebir).
- Test yan etkisi (`data/autonomous_state.json`) commit'e girmeden geri
  alındı.
- PR #167 merge edildi (`d7ab70e`).

### 2. Bulunan ve düzeltilen yeni hata: EXECUTE adayları için pricing-sanity kontrolü hiç çalışmıyordu

92./93. turların bıraktığı açık madde takip edildi: `SUSPICIOUS_UNDERROUND`
(ve `STALE_PRICING`) kontrolü, `_record_shadow_decisions()`'ta yalnızca
**zaten** başka bir nedenle REJECT olacak adaylar için ulaşılan `elif`
zincirinde yaşıyordu (`if _is_execute_after_realism: ... elif ... elif
SUSPICIOUS_UNDERROUND ...`). Gerçek bir sinyalle eşleşen (`is_execute=True`)
VE `execution_realism` gate'ini geçen (`_er_rejection_reason is None`) bir
aday doğrudan `if _is_execute_after_realism:` dalına gidip
`_rejection_reason = None` alıyordu — fiyatlama sağlığı o aday için **hiç**
kontrol edilmiyordu. `ask_yes + ask_no` canlı-mod güven bandının
(`[0.97, 1.10]`) çok dışında olsa bile, ya da snapshot 60 saniyeden eski olsa
bile fark etmiyordu.

`calibration/decision_policy.py::decide()` — bu shadow yolunun taklit etmesi
gereken kanonik politika — bu aynı kontrolü (adım 6b) her canlı aday için,
sinyal eşleşip eşleşmediğine bakmaksızın, herhangi bir EV/gecikme
kontrolünden ÖNCE sert bir REJECT olarak uyguluyor. Orchestrator'ın shadow
kayıt yolu bu davranışı yalnızca sinyal eşleşmeyen adaylar için taklit
ediyordu — tam da gerçek sermaye riske giren, sinyal-eşleşmiş adaylar için
DEĞİL.

**Sonuç:** `shadow_runner/summary_metrics.py`'nin `suspicious_underround_rate`
/ `stale_pricing_rate` metrikleri (bunlar `monitoring/readiness_checks.py`'nin
`check_suspicious_underround_rate` / `check_stale_pricing_rate` kontrollerini
besliyor, onlar da `control_plane/live_gate.py`'nin gerçek emir vermeden önce
baktığı `TINY_PILOT_CANDIDATE` doğrulamasının parçası) tam da önemli olan
adaylarda — gerçek bir sinyalle eşleşmiş adaylarda — asla tetiklenemiyordu.
Fiyatlaması bozuk (underround/overround) veya bayat bir snapshot, sinyal
üretilmediği sürece hiç sayılmıyordu; ama gerçek riski taşıyan tam olarak
sinyal üretilen adaylardı.

**Düzeltme (`agents/orchestrator.py::_record_shadow_decisions()`):**
- `_pricing_sanity_reason` artık `is_execute`/`_er_rejection_reason`'dan
  bağımsız, koşulsuz hesaplanıyor (STALE_PRICING → SUSPICIOUS_UNDERROUND
  sırası korunarak, mevcut REJECT-only elif zincirinin aynısı).
- `_is_execute_after_realism` artık `_pricing_sanity_reason is None`'ı da
  gerektiriyor — `_er_rejection_reason` ile aynı şekilde EXECUTE'ü REJECT'e
  düşürebiliyor.
- `_rejection_reason` zinciri `_pricing_sanity_reason`'ı `_er_rejection_reason`
  ile `_side_diag`/`NO_SIGNAL_PRODUCED` arasına ekliyor (öncelik sırası
  değişmedi, sadece artık her iki dal için de değerlendiriliyor).

**Testler** (`tests/test_execute_candidate_pricing_sanity.py`, yeni dosya, 92.
review'ın `test_shadow_execution_realism_gate.py`'siyle aynı desen):
- `test_underround_execute_candidate_downgraded_to_reject` — eşleşen bir
  YES sinyali + `ask_yes(0.40)+ask_no(0.30)=0.70` (bandın çok altı) →
  REJECT/SUSPICIOUS_UNDERROUND bekleniyor.
- `test_stale_execute_candidate_downgraded_to_reject` — eşleşen sinyal +
  90 saniyelik snapshot yaşı → REJECT/STALE_PRICING bekleniyor.
- `test_healthy_execute_candidate_still_records_as_execute` — regresyon
  kontrolü: sağlıklı fiyatlama hâlâ EXECUTE_YES yazmalı.

**Doğrulama:**
- Sadece `agents/orchestrator.py` geri alınıp yeni 3 test eski koda karşı
  çalıştırıldı: 2/3 fail (üçüncüsü zaten önceki davranışla uyumluydu —
  beklenen, testlerin gerçek hatayı yakaladığı doğrulandı).
- Mevcut ilgili testler çalıştırıldı; `tests/test_shadow_decision_value_matches_trade_decision_type.py::test_no_execute_writes_execute_no`
  regresyona uğradı: 57. turdan kalma `_no_signal()` test fixture'ı
  `best_ask=0.28` + `no_best_ask=0.55` kullanıyordu — toplam 0.83, gerçek bir
  SUSPICIOUS_UNDERROUND (aynı zamanda `bayesian_prob=0.30` ile birlikte
  execution_realism açısından da hiçbir sane `no_best_ask` değeri edge'i
  pozitif tutamıyordu: adil no-vig fiyat ~0.72, bu zaten prob(NO)=0.70'in
  üzerinde). Fixture, aynı EXECUTE_NO-etiketleme amacını koruyarak, hem sane
  (`0.35+0.65=1.00`) hem de execution_realism açısından kârlı
  (`bayesian_prob=0.10` → prob(NO)=0.90 vs ask_no=0.65) gerçekçi fiyatlarla
  güncellendi.
- `pytest tests/test_execute_candidate_pricing_sanity.py
  tests/test_shadow_decision_value_matches_trade_decision_type.py
  tests/test_shadow_stale_underround_rejection_reason.py
  tests/test_shadow_execution_realism_gate.py tests/test_rejection_analytics.py
  tests/test_replay_consistency.py tests/test_shadow_acceptance_metrics.py
  tests/test_live_pilot_readiness.py` → 95/95 passed (fixture düzeltmesinden
  sonra).
- Tam test suite: baseline (94. tur sonrası `origin/main`) **1743 passed, 4
  skipped** → düzeltme sonrası **1746 passed, 4 skipped** (+3 yeni test,
  sıfır regresyon).
- `data/autonomous_state.json`'daki test yan etkisi commit öncesi geri
  alındı.

## Sıradaki tur için notlar
- `calibration/decision_policy.py::_check_binary_sanity()`'nin ek kontrolleri
  (`bid_sum` tavanı ve tek taraflı `min_single_ask` — adım 2/3, sadece
  live/paper_strict modda) `_record_shadow_decisions()`'ta hâlâ hiç
  uygulanmıyor; yalnızca `ask_sum` bandı (adım 1) kopyalanmış durumda. Düşük
  öncelikli, doğrulanması gereken açık madde.
- `agents/whale_tracker.py:48`'deki `market` query param sorusu hâlâ
  doğrulanamadı (ağ erişimi bu oturumda da `connect_rejected`). Kalıcı bir
  açık madde — yalnızca gerçek prod ağ erişimiyle çözülebilir.
- 94. turun bıraktığı `_sim_target`'e ulaşmadan önce eşzamanlı açık sim
  pozisyon sayısı için ayrı, açık bir üst sınır sorusu hâlâ cevaplanmadı.
