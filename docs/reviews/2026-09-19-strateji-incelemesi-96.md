# Günlük Strateji İncelemesi — 2026-09-19 (96. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = `a364862` (#168, 95. tur sonrası). Açık,
birleştirilmemiş PR yoktu. 95. turun "sıradaki tur için notlar" bölümü,
`_record_shadow_decisions()`'ın pricing-sanity kontrolünün
`calibration/decision_policy.py::_check_binary_sanity()`'nin yalnızca 1.
adımını (ask_sum bandı) kopyaladığını, 2. (bid_sum tavanı) ve 3. (tek taraflı
min ask tabanı) adımların hâlâ uygulanmadığını işaret ediyordu. Bu tur o
maddeyi ele aldı.

## Bu turda bulunan ve düzeltilen hata

`agents/orchestrator.py::_record_shadow_decisions()`'taki `_pricing_sanity_reason`
hesaplaması yalnızca iki kontrol yapıyordu: STALE_PRICING (snapshot yaşı) ve
`ask_yes+ask_no` bandı (`BINARY_SANITY_MIN_ASK_SUM_LIVE`/`MAX_ASK_SUM_LIVE`).
Ama `calibration/decision_policy.py::_check_binary_sanity()` — bu shadow
yolunun taklit etmesi gereken kanonik politika — `live` modda üç kontrol
uyguluyor:

1. `ask_sum` bandı (kopyalanmıştı)
2. `bid_sum > BINARY_SANITY_MAX_BID_SUM_LIVE (1.00)` → risksiz arbitraj
   imkansız olduğu için SUSPICIOUS_UNDERROUND (`config.check_bid_overround`
   ile korunuyor, `LIVE_CAL_CONFIG.check_bid_overround=True`)
3. `ask_yes` veya `ask_no` < `BINARY_SANITY_MIN_SINGLE_ASK_LIVE (0.05)` →
   tek taraflı/patolojik teklif → SUSPICIOUS_UNDERROUND

2 ve 3 numaralı kontroller shadow kayıt yolunda hiç uygulanmıyordu. Sonuç:
`bid_yes+bid_no` 1.00'i aşan (risksiz arbitrajın imkansız olduğu, dolayısıyla
yapısal olarak bozuk) bir fiyatlamayla ya da `ask_yes`/`ask_no`'dan biri
0.05'in altında olan (piyasanın bir tarafta neredeyse kesinleştiği, patolojik
tek taraflı teklif) bir fiyatlamayla eşleşen bir EXECUTE adayı, kanonik
politika onu hard-REJECT edeceği hâlde, shadow'da EXECUTE olarak
kaydediliyordu. Bu da 95. turda düzeltilen sorunun aynı sınıfı: gerçek
sermaye riske giren tam olarak eşleşmiş adaylarda `suspicious_underround_rate`
metriği (→ `monitoring/readiness_checks.py::check_suspicious_underround_rate`
→ `control_plane/live_gate.py`'nin `TINY_PILOT_CANDIDATE` doğrulaması)
tetiklenemiyordu.

**Düzeltme (`agents/orchestrator.py`):**
- `calibration.types`'tan `BINARY_SANITY_MAX_BID_SUM_LIVE` ve
  `BINARY_SANITY_MIN_SINGLE_ASK_LIVE` import edildi.
- `_pricing_sanity_reason` zincirine iki `elif` eklendi: bid_sum tavanı
  (`LIVE_CAL_CONFIG.check_bid_overround` ile korunarak, kanonik koddaki
  `config.check_bid_overround` guard'ıyla birebir) ve tek taraflı ask tabanı.
  Sıra korundu: STALE_PRICING → ask_sum bandı → bid_sum tavanı → tek taraflı
  ask tabanı — `_check_binary_sanity()`'nin adım sırasıyla aynı.

**Testler** (`tests/test_execute_candidate_pricing_sanity_bid_and_single_ask.py`,
yeni dosya, 95. turun `test_execute_candidate_pricing_sanity.py`'siyle aynı
desen):
- `test_bid_overround_execute_candidate_downgraded_to_reject` — eşleşen YES
  sinyali + sağlıklı ask bandı (`ask_yes=0.40, ask_no=0.60` → sum=1.00) ama
  `bid_yes=0.38, bid_no=0.65` → sum=1.03 > 1.00 tavan → REJECT/
  SUSPICIOUS_UNDERROUND bekleniyor.
- `test_one_sided_ask_execute_candidate_downgraded_to_reject` — eşleşen
  sinyal + `ask_yes=0.03` (< 0.05 taban), `ask_sum=1.03` ve `bid_sum=0.98`
  her ikisi de sağlıklı bantta, execution_realism'in kendi geçidi de
  (0.60 bayesian_prob'a karşı 0.03'ten alım son derece kârlı) geçerdi →
  REJECT/SUSPICIOUS_UNDERROUND bekleniyor.
- `test_healthy_execute_candidate_still_records_as_execute` — regresyon
  kontrolü: hem bid_sum hem tek taraflı ask sağlıklıyken hâlâ EXECUTE_YES
  yazmalı.

**Doğrulama:**
- Yeni 3 test, düzeltme öncesi koda karşı çalıştırıldı (`git stash` ile
  `agents/orchestrator.py` değişikliği geri alınarak): 2/3 fail (bid-overround
  ve one-sided-ask testleri beklenen şekilde `EXECUTE_YES` alıp `REJECT`
  bekliyordu; healthy-regresyon testi zaten eski davranışla uyumluydu) —
  testlerin gerçek hatayı yakaladığı doğrulandı.
- Düzeltme geri getirildikten (`git stash pop`) sonra: yeni 3 test +
  `test_execute_candidate_pricing_sanity.py`'nin 3 testi → 6/6 passed.
- İlgili geniş test grubu (`test_execute_candidate_pricing_sanity_bid_and_
  single_ask.py test_execute_candidate_pricing_sanity.py
  test_shadow_decision_value_matches_trade_decision_type.py
  test_shadow_stale_underround_rejection_reason.py
  test_shadow_execution_realism_gate.py test_rejection_analytics.py
  test_replay_consistency.py test_shadow_acceptance_metrics.py
  test_live_pilot_readiness.py`) → 95/95 passed.
- Tam test suite: baseline (95. tur sonrası `origin/main`) **1746 passed, 4
  skipped** → düzeltme sonrası **1749 passed, 4 skipped** (+3 yeni test, sıfır
  regresyon).
- `data/autonomous_state.json`'daki test yan etkisi commit öncesi geri
  alındı (`git checkout -- data/autonomous_state.json`).

## Sıradaki tur için notlar
- `agents/whale_tracker.py:48`'deki `market` query param sorusu hâlâ
  doğrulanamadı (ağ erişimi bu oturumda da kullanılamadı). Kalıcı bir açık
  madde — yalnızca gerçek prod ağ erişimiyle çözülebilir.
- 94. turun bıraktığı `_sim_target`'e ulaşmadan önce eşzamanlı açık sim
  pozisyon sayısı için ayrı, açık bir üst sınır sorusu hâlâ cevaplanmadı.
- `data/trade_patterns.json` / `data/3day_eval.txt` gerçek (canlı olmayan
  sim/shadow) trade geçmişinde `LOW_EDGE_LOSS` deseni baskın (1008 oluşum,
  toplam PnL -2548) ve YES tarafı NO tarafına göre belirgin şekilde daha
  zayıf (`3day_eval.txt`: YES PnL -14.39 vs NO PnL +15.40, 44 trade'lik
  pencerede). Bu, kod-doğruluğu hatası değil; `docs/strategy.md`'nin zaten
  not ettiği yön tahmini zayıflığı ile tutarlı bir gözlem. Sonraki turlarda
  YES/NO asimetrisinin kaynağını (Bayesian yön tahmini mi, gate'lerin YES'i
  NO'dan farklı filtrelemesi mi) izlemeye devam etmek gerekiyor.
