# Günlük Strateji İncelemesi — 2026-09-15 (50. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Bu turda yapılanlar — Monte Carlo ödeme formülü düzeltildi (49. turun yüksek öncelikli notu)

49. tur, `strategies/monte_carlo.py::simulate()`'in kazanç formülünü
(`bet * net_edge * 2.0`) miskalibre bulmuş ve gerçek Kelly/edge-model ile
tutarlı hale getirmeden `analyze()`'e bağlamanın botu sessizce tamamen
durdurabileceğini belgeleyip 3 adımlı bir sıralama bırakmıştı. Bu tur o
sırayı takip etti:

### Adım 1 — Ödeme formülü düzeltildi
`simulate()`'e `kelly_criterion.py::position_size()` ile aynı `b = (1/price) - 1`
formülünü kullanan bir `price` parametresi eklendi (varsayılan `0.5`, gerçek
çağrıda `signals[0].entry_price` geçiriliyor). Kazanç artık
`bet * net_edge * 2.0` (edge'in kendisiyle sınırlı, gerçekçi olmayan) yerine
`bet * b` (`bet/price - bet` — gerçek prediction-market ödemesi) olarak
hesaplanıyor. `strategies/arbitrage_engine.py::_maybe_run_monte_carlo()` ve
tek çağrı noktası (`analyze()`, satır ~408) güncellendi.

### Adım 2 — Düzeltilmiş formül gerçekçi edge/price kombinasyonlarıyla doğrulandı
```
price=0.40 edge=0.10 → mean_ret=+251.8x  (eskiden: viable=False, mean_ret negatif)
price=0.50 edge=0.20 → mean_ret=+18.5x
price=0.60 edge=0.30 → mean_ret=+2.4x
```
Formül artık doğru yönde çalışıyor (kazanan sinyaller pozitif beklenen
getiri veriyor), AMA **`viable` hâlâ test edilen HER kombinasyonda `False`**
— farklı, meşru bir nedenle: `position_size_pct=0.20` (MAX_POSITION_PCT) ile
100 ardışık trade compound edildiğinde varyans patlıyor (`p10_return`
-95%'e kadar, `max_drawdown` %70-90 civarı). Bu, 48./49. turların bulduğu
"formül hatası" değil — sermayenin sabit %20'sini her seferinde riske atıp
100 kez compound etmenin doğal, yüksek varyanslı sonucu. Gerçek canlı
sistem Kelly-boyutlandırmalı (genelde %20 tavanın çok altında, adaptif
çeyrek-Kelly) pozisyon açıyor; simülasyon ise her zaman tavanı (`0.20`)
kullanıyor — bu, simülasyonun canlı riskten daha kötümser olmasına neden
oluyor (bkz. `test_mc_position_pct_matches_kelly.py`'nin bilinçli tasarımı:
tavanla eşleşmesi ayrı bir regresyon testiyle korunuyor).

### Adım 3 — Kapı sadece GÖLGE MODDA bağlandı (engellemiyor)
`analyze()` artık `_maybe_run_monte_carlo()`'nun dönüş değerini gerçekten
tüketiyor (önceki 49 turdur atılıyordu), ama sadece gözlem amaçlı:
`viable=False` olduğunda `MC_SHADOW` uyarı logu basıyor, **sinyali
engellemiyor**. Adım 2'nin gösterdiği gibi, mevcut `viable` eşikleri
(`p10>-0.40`, `mean_dd<0.45`) sabit-%20 compounding simülasyonuna göre
kalibre edilmiş değil; bunları düzeltmeden gerçek gate olarak bağlamak
49. turun tam olarak uyardığı riski taşımaya devam ediyor (bot sessizce
trade yapamaz hale gelir). Gölge modun amacı: birkaç günlük canlı
`MC_SHADOW` log yoğunluğunu gözlemleyip false-positive oranını ölçmek.

### Testler
- `tests/test_mc_payout_matches_kelly_b.py` (yeni, 2 test): tek işlemlik
  determinist kazanç senaryosunda ödemenin `bet * b` olduğunu ve
  gerçekçi edge/price'ta `mean_return`'ün artık pozitif olduğunu doğrular.
- `tests/test_mc_position_pct_matches_kelly.py` güncellendi (yeni `price`
  parametresini karşılamak için) — testin asıl amacı (position_size_pct
  tavanla eşleşmeli) değişmedi.
- Tam suite: **742 passed, 2 skipped, 4 failed** — 4 başarısız test
  (`test_balance_sync_failure_sentinel`, `test_partial_fill_uses_real_filled_size`,
  `test_whale_tracker_outcome_side_mismatch` x2) bu oturumun sanal ortamında
  değişiklik öncesi `main`'de de aynı şekilde başarısız (doğrulandı:
  `git stash` ile önce/sonra karşılaştırıldı) — bu turun değişikliğiyle
  ilgisi yok, muhtemelen bu spesifik sandbox'ın bağımlılık/versiyon farkı.
  Gerçek canlı ortamda (49. turun raporladığı "744 passed, 0 failed"
  ortamı) tekrar doğrulanmalı.

### Sıradaki tur için not
`viable` eşiklerini (`p10>-0.40`, `mean_dd<0.45`, `mean_wr>0.50`) sabit
%20 compounding senaryosuna göre yeniden kalibre etmeden veya simülasyonu
gerçek adaptif Kelly boyutuyla (sabit tavan değil) çalıştırmadan gate'i
gerçek engelleyici moda geçirme — gölge modun log yoğunluğunu birkaç gün
gözlemleyip false-positive oranını ölçtükten sonra karar ver.

## Sonuç
49. turun bıraktığı 3 adımlı plan tamamlandı: ödeme formülü düzeltildi,
gerçekçi girdilerle doğrulandı, ve kapı sadece gözlem amaçlı (gölge modda)
bağlandı — canlı sinyalleri engellemeden Monte Carlo kapısının artık en
azından her cycle'da gerçekten hesaplanıp loglanmasını sağladı.
