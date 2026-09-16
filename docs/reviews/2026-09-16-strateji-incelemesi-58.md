# Günlük Strateji İncelemesi — 2026-09-16 (58. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum açıldığında `main` üzerinde 4 birleştirilmemiş PR vardı (#92-#95,
56./57. çalışmalar, farklı eşzamanlı oturumlardan). Çalışma dalı
(`claude/brave-faraday-rvz03v`) `main`'in güncel HEAD'inden (`caf9085`,
PR #91 birleşmiş) yeni oluşturuldu — bu turun kapsamı o PR'larla
çakışmıyor, ayrı bir konu üzerinde çalışıldı.

Kapsam seçimi için `docs/reviews/*.md` üzerinde modül isimleri grep'lendi:
`agents` (48), `core` (32), `strategies` (38) çok kez incelenmişken
`execution_realism` (1), `operator_layer` (1), `signal_bridge` (1),
`monitoring` (2), `crypto_directional` (4), `calibration` (6) çok daha az
incelenmiş. `operator_layer/pnl.py` kontrol edildi — 55. çalışmanın
dashboard DAILY_STOP_LOSS düzeltmesiyle zaten tutarlı (aynı
`day_start_capital` mantığı doğru uygulanmış), bulgu yok. Canlı işlem
yoluna asıl etkisi olan `strategies/arbitrage_engine.py` (48 kez anılmış
ama büyük dosya, her turda tamamı taranmıyor) üzerinde odaklanıldı.

## Bulgu (58.) — Toplam dış-sinyal boost tavanı, SmartMoney/TopTrader/OB_Depth/KalshiArb için hiç çalışmıyordu

### Kapsam
`strategies/arbitrage_engine.py::_evaluate_market()`, "AGGREGATE BOOST CAP"
bölümü (~satır 984-996, düzeltme öncesi).

### Kök neden
Bu bölüm, SmartMoney (±0.02), TopTrader (±0.03), OB_Depth (±0.03) ve
KalshiArb (±0.02) boost'larının **toplam** etkisini ±0.04 ile sınırlamak
için var — kendi yorumunda da böyle yazıyor: "Allow external boosts
(whale, smart trader, orderflow) to modify probability but cap total boost
at ±0.04 to prevent runaway." Ama toplam boost'u
`bayesian_prob - _pre_boost_prob` olarak ölçüyordu, ve `_pre_boost_prob`
tam da bu dört boost'un **hepsi uygulandıktan sonra** yakalanıyor (16.
çalışmada, birkaç satır sonraki "disable macro signals" reset'inin bu dört
boost'u da silmemesi için bilinçli olarak oraya taşınmış). Sonuç:
`bayesian_prob - _pre_boost_prob` bu dört sinyal için her zaman ~0'a eşit
— yani tavan kontrolü tam olarak sınırlamak istediği sinyaller için hiç
tetiklenmiyordu.

Her boost tek tek sınırlı, ama toplamları hiç sınırlı değildi. Bu dört
sinyal hepsi trend-takip edici olduğundan aynı yönde hizalanmaları
istisnai değil — böyle bir durumda tek bir döngüde +0.07 ila +0.10
(0.02+0.03+0.02, OB_Depth de eklenirse +0.10) birleşik boost
`bayesian_prob`'a eklenebiliyor, ±0.04 tavanını ve hemen üstündeki ayrı
sabit 0.65 güven tavanını (gerçek kayıplardan sonra eklenmişti: "DOGE
P=0.874→LOSS, HYPE P=0.917→LOSS") rahatça aşabiliyordu.

### Neden önemli
Şişirilmiş `bayesian_prob`, `edge = bayesian_prob - price` hesabını
besliyor — bu da her canlı trade için yön seçimini ve Kelly pozisyon
boyutlandırmasını doğrudan etkiliyor. Sınırsız birleşik boost, aşırı
güvenli olasılık tahminleri üzerine büyütülmüş bahisler anlamına geliyor.

### Somut senaryo (doğrulandı, `ArbitrageEngine.analyze()` gerçek çalıştırılarak)
SmartMoney (+0.02) + TopTrader (+0.03) + KalshiArb (+0.02) hepsi bullish
yönde hizalı — ham toplam boost +0.07:
- **Düzeltme öncesi**: `bayesian_prob` 0.65 → 0.72 (+0.07, tavan hiç
  tetiklenmedi).
- **Düzeltme sonrası**: 0.65 → 0.69 (+0.04, dokümante edilen tavana tam
  oturuyor, `BOOST_CAP` log satırı artık doğru şekilde basılıyor).

### Düzeltme
`_evaluate_market()`'te, sabit 0.65 olasılık tavanından hemen sonra ve
SmartMoney/TopTrader/OB_Depth/KalshiArb çalışmadan **önce**
`_prob_before_external_boosts = bayesian_prob` eklendi. Toplam tavan
kontrolü artık bu erken baseline'a göre ölçüyor/kırpıyor —
`_pre_boost_prob`'a değil. `_pre_boost_prob`'un kendi amacı (macro
sinyaller kapalıyken reset) değişmedi, aynı şekilde kullanılmaya devam
ediyor.

### Test
`tests/test_aggregate_boost_cap_bypassed_by_early_capture.py` (yeni, 1
test): `ArbitrageEngine.analyze()`'i önce tüm dış trackerlar nötrken,
sonra SmartMoney+TopTrader+KalshiArb hepsi bullish hizalıyken çalıştırıp
`bayesian_prob` farkının ≤ 0.04 olduğunu doğruluyor.

Düzeltme öncesi (`git stash -- strategies/arbitrage_engine.py` ile
doğrulandı): **1 failed** (fark 0.07 > 0.04). Düzeltme sonrası: **1 passed**.

### Doğrulama
`python3 -m pytest tests/` → **759 passed, 2 skipped** (758 taban + 1 yeni
test), sıfır regresyon.

## Sonuç
Toplam dış-sinyal boost tavanı artık kendi dokümante ettiği ±0.04 sınırını
gerçekten uyguluyor — SmartMoney, TopTrader, OB_Depth ve KalshiArb aynı
yönde hizalandığında `bayesian_prob` artık sabit 0.65 tavanına yakın
seviyelerde sınırsız şişemiyor. CLAUDE.md'nin risk kuralları veya Kelly/
Bayesian'ın iç matematiği değiştirilmedi — sadece tavanın ölçtüğü baseline
düzeltildi.

## Sıradaki tur için notlar (devralınan + yeni)
- `data/trade_memory.json`'daki `CAPITAL_LOW` uyarısı ve sim-live WR farkı
  hâlâ araştırılmayı bekliyor (önceki turlardan devralınan, eski/stale veri
  olabilir — tarih 2026-03-24, bu turun tarihinden ~6 ay eski).
- `MC_GATE_SHADOW` loglarını izlemeye devam et; `MC_GATE_ENFORCE=true`'ya
  geçiş kararı hâlâ bekliyor (devralınan).
- `er.passes_gate`/`fill_decision` canlı emir döngüsüne hiç bağlanmıyor
  (54. çalışmadan devralınan) — NO-taraf fiyat hatası düzeltildiğinden bir
  sonraki tur shadow→enforce geçişini değerlendirebilir.
- **Yeni**: `strategies/arbitrage_engine.py` çok büyük ve yoğun bir dosya
  (48 kez anılmış ama tek bir turda tamamı taranamıyor) — diğer boost/cap
  bölümlerinin (regime addon, tech score, ML score) benzer baseline
  karışıklığı taşıyıp taşımadığı henüz sistematik olarak kontrol edilmedi.
- `operator_layer/aggregator.py`, `operator_layer/ledgers.py`,
  `operator_layer/readiness_view.py`, `operator_layer/api.py` hâlâ satır
  satır incelenmedi (sadece `pnl.py` ve `health.py` bu turda gözden
  geçirildi, `health.py`'de `drift_alerts` alanına JOURNAL kategorisi
  alarmlarının da karıştığı fark edildi ama kozmetik/düşük etkili
  bulunduğu için bu turda dokunulmadı).
