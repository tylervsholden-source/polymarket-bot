# Günlük Strateji İncelemesi — 2026-09-19 (97. tur, konsolidasyon)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = `a364862` (#168, 95. tur sonrası). **4 açık
PR** bulundu, hepsi "96. tur" etiketli, dört farklı eşzamanlı oturum
tarafından aynı `a364862` üzerine, ~3 saatlik bir pencerede (13:11–16:07 UTC)
açılmış:

- **#169** (13:11, dal `dfsxuu`) — bid_sum tavanı + tek taraflı ask tabanı
  kontrolü eklendi. bid_sum için `pricing_snap.bid_yes/bid_no` kullanıyor
  ama ask tabanı için ham `ask_yes`/`_ask_no` lokallerine dönüyor
  (tutarsız, ama bu ikisi için ham/`pricing_snap` değerleri zaten eşit
  olduğundan davranış farkı yok). `check_bid_overround` guard'ı yok.
- **#170** (14:09, dal `7e4wjt`) — aynı iki kontrol, ama ham `bid_yes`/
  `_bid_no` lokalleri kullanılarak (bid_sum için `pricing_snap` DEĞİL).
  `best_bid` market payload'da eksikse ham `bid_yes=0` okunuyor,
  `pricing_snap.bid_yes` ise bunu `ask_yes*0.99`'a fallback'liyor — yani bu
  PR, `best_bid` eksik olduğunda `bid_sum`'u olduğundan düşük hesaplayıp
  gerçek bir near-arb patolojisini kaçırabilirdi. `check_bid_overround`
  guard'ı var.
- **#171** (15:14, dal `q7m2xk`) — **farklı bir hata**: `strategies/
  arbitrage_engine.py::_evaluate_market()`'teki `ML_BOOST` dalı (`ml_score >
  0.5`) yalnızca log basıyor, `ML_CAUTION`'ın simetriği olarak boyutu hiç
  büyütmüyordu.
- **#172** (16:07, dal `anv2gr`) — #169/#170 ile aynı hata, ama hem bid_sum
  hem ask tabanı kontrolü tutarlı şekilde `pricing_snap.bid_yes/bid_no/
  ask_yes/ask_no` (fallback sonrası, kaydın kendisinin iddia ettiği
  değerler) üzerinden yapılıyor — hem #169'un tutarsızlığını hem #170'in
  `bid_yes=0` kaçırma riskini taşımıyor.

## Bu turda yapılanlar

### 1. Dört PR bağımsız olarak doğrulandı ve karşılaştırıldı
- Kaynak (`calibration/decision_policy.py::_check_binary_sanity()`,
  `agents/orchestrator.py`'de `pricing_snap`/ham `bid_yes`/`_bid_no`/
  `ask_yes`/`_ask_no` tanımları) okunarak #169/#170/#172'nin gerçek
  davranış farkı teyit edildi (yukarıdaki analiz).
- `pip install -r requirements.txt` ile bağımlılıklar kuruldu (bu
  konteynerde başlangıçta `pytest` bile yoktu).
- **#172** ayrı checkout edildi: hedef test grubu (`test_execute_candidate_
  pricing_sanity.py` + 7 ilişkili dosya) → 94/94 passed; tam suite
  (`tests/ calibration/tests crypto_directional/tests execution_realism/
  tests signal_bridge/tests`) → **1748 passed, 4 skipped** — PR'ın kendi
  iddiasıyla birebir.
- **#171** ayrı checkout edildi: `test_ml_boost_never_increased_size.py` →
  1/1 passed; tam suite → **1747 passed, 4 skipped** (baseline 1746 + 1 yeni
  test) — PR'ın kendi iddiasıyla tutarlı.
- Her checkout'ta `data/autonomous_state.json`'a test yan etkisi oluştu;
  commit öncesi `git checkout -- data/autonomous_state.json` ile geri
  alındı.

### 2. Konsolidasyon: en doğru pricing-sanity PR'ı + bağımsız ML_BOOST PR'ı merge edildi
İki pricing-sanity PR'ı arasında en tutarlı/doğru olan **#172** seçildi
(gerekçe yukarıda). #172 ve #171 farklı dosyalara dokunuyor
(`agents/orchestrator.py` vs `strategies/arbitrage_engine.py`) — mantıksal
çakışma yok, sıralı merge edildi:

1. `#172` merge edildi (`ccf692c`).
2. `#171` merge edildi (`b9ef97b`) — `#172`'nin üzerine temiz uyguladı.
3. `#169` ve `#170`, #172'ye referans veren açıklayıcı yorumlarla kapatıldı
   (birbiriyle ve #172 ile çakışan yinelenen dallar; git geçmişini
   temiz tutmak için).

**Son doğrulama** (`origin/main` = `b9ef97b`, iki merge sonrası):
- Tam suite: **1749 passed, 4 skipped** (1746 baseline + 2 [#172] + 1 [#171]
  = tam beklenen aritmetik, sıfır regresyon).
- `data/autonomous_state.json` yan etkisi tekrar geri alındı.

## Sermaye/performans notu
Bu oturumda çalışan bir bot instance'ı yok (konteynerde `data/status.json`/
`control.json`/`positions.json` mevcut değil, ağ erişimi de yok) — %10
hedefine karşı gerçek ilerleme bu oturumdan doğrulanamıyor. Bu turun katkısı,
gerçek sermaye riske girmeden önce `TINY_PILOT_CANDIDATE` canlı-geçiş
doğrulamasının (`control_plane/live_gate.py`) dayandığı iki okunabilirlik
sinyalini (`suspicious_underround_rate`, ML-boost'un Kelly boyutlandırmasına
gerçekten yansıması) daha güvenilir hale getirmek — dolaylı risk azaltma.

## Sıradaki tur için notlar
- **Zamanlama sıklığı**: aynı gün içinde 4 bağımsız oturumun ~3 saatlik bir
  pencerede aynı numaralı ("96. tur") çakışan PR açması — bu sorun 86./89./
  93. turlarda zaten kullanıcıya raporlandı, tekrar ayrıca bildirilmiyor,
  ama konsolidasyon ihtiyacının tekrarlayan doğrudan nedeni olmaya devam
  ediyor.
- `agents/whale_tracker.py:48`'deki `market` query param sorusu hâlâ
  doğrulanamadı (ağ erişimi bu oturumda da yok). Kalıcı bir açık madde.
- 94. turun bıraktığı `_sim_target`'e ulaşmadan önce eşzamanlı açık sim
  pozisyon sayısı için ayrı, açık bir üst sınır sorusu hâlâ cevaplanmadı.
- #172'nin notuna göre: `_check_binary_sanity()`'nin `_record_shadow_
  decisions()`'a taşınması gereken bilinen adımlarının tamamı artık
  kapsanıyor (1/2/3) — bu spesifik açık madde kapandı.
