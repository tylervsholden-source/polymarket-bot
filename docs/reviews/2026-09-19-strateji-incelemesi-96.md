# Günlük Strateji İncelemesi — 2026-09-19 (96. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `claude/brave-faraday-anv2gr` = `a364862` (#168, 95. tur
sonrası, `origin/main` ile eşit). Bu oturumda çalıştırılan bir bot instance'ı
yok (konteynerde `data/status.json`/`control.json`/`positions.json` boş) —
görev, önceki turlarla aynı şekilde kod/strateji incelemesi ve düzeltmesi
olarak yürütüldü.

## Bu turda yapılan: 95. turun bıraktığı açık madde çözüldü

95. tur şu notu bırakmıştı: `calibration/decision_policy.py::
_check_binary_sanity()`'nin ek kontrolleri (adım 2: `bid_sum` tavanı, adım 3:
tek taraflı `min_single_ask` — ikisi de live/paper_strict modda koşulsuz
uygulanıyor) `agents/orchestrator.py::_record_shadow_decisions()`'ta hâlâ hiç
uygulanmıyor; yalnızca adım 1 (`ask_sum` bandı) kopyalanmış durumdaydı.

Bu madde doğrulandı ve gerçek bir hata olduğu teyit edildi:

- `calibration/decision_policy.py:530-551` (`_check_binary_sanity()`), live
  modda `LIVE_CAL_CONFIG.check_bid_overround=True` olduğu için üç kontrolü de
  (ask_sum bandı, bid_sum tavanı ≤ `BINARY_SANITY_MAX_BID_SUM_LIVE` (1.00),
  tek taraflı ask tabanı ≥ `BINARY_SANITY_MIN_SINGLE_ASK_LIVE` (0.05))
  koşulsuz REJECT olarak uyguluyor — bu shadow yolunun taklit etmesi gereken
  kanonik politika budur.
- `agents/orchestrator.py`'de yalnızca `BINARY_SANITY_MIN_ASK_SUM_LIVE` /
  `BINARY_SANITY_MAX_ASK_SUM_LIVE` import edilmişti;
  `BINARY_SANITY_MAX_BID_SUM_LIVE` / `BINARY_SANITY_MIN_SINGLE_ASK_LIVE` hiç
  kullanılmıyordu.

**Sonuç:** `bid_yes + bid_no > 1.00` (risk-free arb'a yakın, gerçekte
imkansız fiyatlama) veya `ask_yes`/`ask_no`'dan biri `0.05`'in altında
(patolojik tek taraflı kotasyon) olan bir aday — `ask_sum` bandı içinde
kaldığı sürece — hâlâ EXECUTE olarak kaydediliyordu. 95. turda kapatılan
delikle aynı sınıftan bir boşluk: gerçek sinyalle eşleşmiş, sermaye riske
giren adaylar için `suspicious_underround_rate` iki ayrı fiyatlama
patolojisini asla göremiyordu.

**Düzeltme (`agents/orchestrator.py::_record_shadow_decisions()`):**
- İki eksik import eklendi.
- `_pricing_sanity_reason` zincirine, `_check_binary_sanity()`'nin sırasını
  koruyarak, iki `elif` dalı eklendi:
  - `(pricing_snap.bid_yes + pricing_snap.bid_no) > BINARY_SANITY_MAX_BID_SUM_LIVE`
  - `pricing_snap.ask_yes < BINARY_SANITY_MIN_SINGLE_ASK_LIVE or pricing_snap.ask_no < BINARY_SANITY_MIN_SINGLE_ASK_LIVE`
  Her ikisi de `SUSPICIOUS_UNDERROUND` üretiyor (kanonik politikayla aynı
  rejection reason vocabulary).
- Kontroller, zaten kaydedilmiş `pricing_snap.bid_yes`/`bid_no`/`ask_yes`/
  `ask_no` alanları üzerinden çalışıyor (raw `bid_yes`/`_bid_no` yerine) —
  böylece sanity check, kaydın kendisinin iddia ettiği fiyatlarla birebir
  aynı değerleri görüyor (fallback ikamesinden sonra).

**Testler** (`tests/test_execute_candidate_pricing_sanity.py`, mevcut
dosyaya eklendi, 95. review'ın aynı deseni):
- `test_bid_overround_execute_candidate_downgraded_to_reject` — eşleşen YES
  sinyali + `bid_yes(0.60)+no_best_bid(0.41)=1.01` (tavanın üstü) while
  `ask_yes(0.60)+no_best_ask(0.41)=1.01` (bandın içi) → REJECT/
  SUSPICIOUS_UNDERROUND bekleniyor.
- `test_one_sided_ask_execute_candidate_downgraded_to_reject` — eşleşen
  sinyal + `no_best_ask=0.04` (tabanın altı) while `ask_sum=0.97` (bandın
  sınırında, içeride) ve `bid_sum=0.95` (tavanın altında) → REJECT/
  SUSPICIOUS_UNDERROUND bekleniyor.
- Mevcut `test_healthy_execute_candidate_still_records_as_execute`
  regresyonsuz geçti (bid_sum=0.98, ask_no=0.62 — her iki yeni eşiğin de
  uzağında).

**Doğrulama:**
- Sadece `agents/orchestrator.py` geri alınıp 5 testin tamamı eski koda karşı
  çalıştırıldı: 2 yeni test fail (üçüncüsü zaten önceki davranışla uyumluydu
  — beklenen, yeni testlerin gerçek hatayı yakaladığı doğrulandı).
- `pytest tests/test_execute_candidate_pricing_sanity.py
  tests/test_shadow_decision_value_matches_trade_decision_type.py
  tests/test_shadow_stale_underround_rejection_reason.py
  tests/test_shadow_execution_realism_gate.py tests/test_rejection_analytics.py
  tests/test_replay_consistency.py tests/test_shadow_acceptance_metrics.py
  tests/test_live_pilot_readiness.py` → 94/94 passed.
- Tam test suite: baseline (95. tur sonrası) **1746 passed, 4 skipped** →
  düzeltme sonrası **1748 passed, 4 skipped** (+2 yeni test, sıfır
  regresyon).
- `data/autonomous_state.json`'daki test yan etkisi commit öncesi geri
  alındı.

## Sıradaki tur için notlar
- `agents/whale_tracker.py:48`'deki `market` query param sorusu hâlâ
  doğrulanamadı (ağ erişimi bu oturumda da yok). Kalıcı bir açık madde —
  yalnızca gerçek prod ağ erişimiyle çözülebilir.
- 94. turun bıraktığı `_sim_target`'e ulaşmadan önce eşzamanlı açık sim
  pozisyon sayısı için ayrı, açık bir üst sınır sorusu hâlâ cevaplanmadı.
- `_check_binary_sanity()`'nin geri kalanı artık `_record_shadow_decisions()`
  tarafından tam kapsanıyor (adım 1/2/3 hepsi); bu turdan sonra
  `calibration/decision_policy.py::decide()` ile bu shadow yolu arasında,
  binary sanity açısından bilinen bir davranış farkı kalmadı.
