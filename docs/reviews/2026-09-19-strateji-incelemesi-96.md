# Günlük Strateji İncelemesi — 2026-09-19 (96. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = `a364862` (#168, 95. tur sonrası), açık
birleştirilmemiş PR yok. Yeni dal (`claude/brave-faraday-dfsxuu`) `main`'den
ayrıldı.

## Bu turda yapılanlar

### Bulunan ve düzeltilen hata: `_check_binary_sanity()`'nin bid_sum tavanı ve tek taraflı ask tabanı kontrolleri (adım 2/3) shadow karar kaydına hiç taşınmamıştı

95. turun bıraktığı açık madde takip edildi: `agents/orchestrator.py::_record_shadow_decisions()`,
`calibration/decision_policy.py::_check_binary_sanity()`'nin sadece **adım 1**'ini
(ask_sum bandı `[BINARY_SANITY_MIN_ASK_SUM_LIVE, BINARY_SANITY_MAX_ASK_SUM_LIVE]`)
kopyalamıştı. Canonical fonksiyonun live/paper_strict modda uyguladığı diğer iki
kontrol hiç portlanmamıştı:

- **Adım 2 — bid_sum tavanı**: `bid_yes + bid_no > BINARY_SANITY_MAX_BID_SUM_LIVE
  (1.00)` → risk-free arb'a yakın bölge.
- **Adım 3 — tek taraflı ask tabanı**: `ask_yes` veya `ask_no` <
  `BINARY_SANITY_MIN_SINGLE_ASK_LIVE (0.05)` → tek taraflı, neredeyse kesin
  fiyatlanmış market (pathological quote).

Sonuç: eşleşen bir sinyali olan ve execution_realism gate'ini geçen bir aday,
`ask_sum` bandı içinde kalsa bile `bid_sum=1.02` (near-arb) veya
`ask_no=0.03` (tek taraflı pathological quote) gibi durumlarda hâlâ hiçbir
uyarı olmadan EXECUTE olarak kaydediliyordu. Bu, 95. turun adım 1 için
kapattığı aynı kör noktayı adım 2/3 için açık bırakıyordu: `shadow_runner/
summary_metrics.py`'nin `suspicious_underround_rate`'i — `monitoring/
readiness_checks.py::check_suspicious_underround_rate` üzerinden
`control_plane/live_gate.py`'nin gerçek emir vermeden önce baktığı
`TINY_PILOT_CANDIDATE` doğrulamasının parçası — bid_sum veya tek taraflı ask
patolojisi yüzünden reddedilmesi gereken tam da sinyal-eşleşmiş (gerçek
sermaye riske giren) adaylarda asla tetiklenemiyordu.

**Düzeltme (`agents/orchestrator.py::_record_shadow_decisions()`):**
- `BINARY_SANITY_MAX_BID_SUM_LIVE` ve `BINARY_SANITY_MIN_SINGLE_ASK_LIVE`
  `calibration.types`'tan import edildi.
- `_pricing_sanity_reason` hesaplamasına, `_check_binary_sanity()` ile aynı
  sırada (ask_sum bandı → bid_sum tavanı → tek taraflı ask tabanı), iki yeni
  `elif` dalı eklendi.
- bid_sum, `pricing_snap.bid_yes + pricing_snap.bid_no` üzerinden hesaplandı
  — ham `bid_yes`/`_bid_no` lokal değişkenleri değil, zaten fallback
  doldurulmuş (`bid_yes if bid_yes > 0 else ask_yes*0.99` / synthetic
  `_bid_no`) `PricingSnapshot` alanları kullanıldı, çünkü gerçek bir
  `MarketPricingSnapshot`'un taşıyacağı değerler bunlar.
- Öncelik sırası korunmadı, sadece genişletildi: her iki yeni kontrol de
  aynı `SUSPICIOUS_UNDERROUND` rejection_reason'ı üretiyor (canonical
  fonksiyonla birebir — üç kontrol de aynı mesaj önekini paylaşıyor).

**Testler** (`tests/test_execute_candidate_pricing_sanity.py`, 95. turun
dosyasına eklendi):
- `test_bid_sum_ceiling_execute_candidate_downgraded_to_reject` — eşleşen YES
  sinyali + `ask_yes(0.50)+ask_no(0.50)=1.00` (bant içi) ama
  `bid_yes(0.49)+bid_no(0.52)=1.01` (tavanın üstü) → REJECT/
  SUSPICIOUS_UNDERROUND bekleniyor.
- `test_min_single_ask_execute_candidate_downgraded_to_reject` — eşleşen YES
  sinyali + `ask_yes=0.03` (tabanın altı), `ask_sum=1.00` ve `bid_sum=0.97`
  ikisi de bant içi → REJECT/SUSPICIOUS_UNDERROUND bekleniyor.
- Mevcut 3 test (underround/stale/healthy) regresyonsuz geçmeye devam etti.

**Doğrulama:**
- Sadece `agents/orchestrator.py` geri alınıp yeni 2 test eski koda karşı
  çalıştırıldı: 2/2 fail (beklenen — testlerin gerçek hatayı yakaladığı
  teyit edildi), diğer 3 test hâlâ geçiyordu.
- `python3 -m pytest tests/test_execute_candidate_pricing_sanity.py
  tests/test_shadow_decision_value_matches_trade_decision_type.py
  tests/test_shadow_stale_underround_rejection_reason.py
  tests/test_shadow_execution_realism_gate.py tests/test_rejection_analytics.py
  tests/test_replay_consistency.py tests/test_shadow_acceptance_metrics.py
  tests/test_live_pilot_readiness.py calibration/tests/test_binary_market_sanity.py`
  → 116/116 passed.
- Tam test suite: baseline (95. tur sonrası `origin/main`) **1746 passed, 4
  skipped** → düzeltme sonrası **1748 passed, 4 skipped** (+2 yeni test,
  sıfır regresyon).
- `data/autonomous_state.json`'daki test yan etkisi commit öncesi geri
  alındı.

## Sermaye/performans notu (bilgi amaçlı, bu turda aksiyon alınmadı)
`data/3day_eval.txt` (son gerçek 44 trade): toplam PnL sadece $1.01, YES
trade'ler net negatif (-$14.39), NO trade'ler net pozitif (+$15.40) — bu,
CLAUDE.md'de zaten belgelenmiş "yön tahmini, edge değil" ve "Bayesian
overconfidence" bulgularıyla tutarlı.

`data/trade_patterns.json`'daki `LOW_EDGE_LOSS` (1008 oluşum, 0 kazanç,
-$2548 toplam) ilk bakışta alarm verici görünüyor, ama incelendi ve **stale
veri** olduğu teyit edildi:
- `data/trade_analyses.json`'daki 200 kaydın tamamı `2026-03-26` tarihli
  (bugün `2026-09-19`) ve tamamının `edge=0` alanı var (`LOW_EDGE_LOSS`
  eşiği `edge_at_entry < 0.08` olduğu için bu, gerçek düşük-edge kayıplarından
  değil, o dönemde `edge` alanının hiç yazılmamasından kaynaklanıyor gibi
  duruyor).
- `agents/orchestrator.py:1370` civarındaki mevcut kod yorumu, tam bu sınıf
  bir hatayı ("approval queue" onay yolunda `add_position()`'a `edge=`
  geçilmediği için `TradeAnalyzer`'ın her zaman `edge=0` okuması) zaten
  belgeliyor ve düzeltme hâlâ uygulamada (`edge=edge` çağrıya geçiliyor).
  Sim path (`agents/orchestrator.py:1129`, `sim_entry["edge"] = signal.edge`)
  de kontrol edildi — mevcut kodda edge doğru yazılıyor.
- Sonuç: mevcut kod, `data/trade_analyses.json`'daki Mart 2026 verisinin
  toplandığı zamandan sonra düzeltilmiş görünüyor (90+ ardışık günlük
  incelemenin bir kısmı tam olarak bu tarz "sinyal metadata'sı kayıp/wiring"
  hatalarını hedefledi). Bu oturumda gerçek trade akışı yok (ağ erişimi
  `connect_rejected`), o yüzden düzeltmenin gerçekten etkili olduğu canlı
  veriyle doğrulanamadı — ama kod incelemesi mevcut path'lerin ikisinin de
  (approval-queue ve sim) `edge`'i artık doğru taşıdığını gösteriyor.

## Sıradaki tur için notlar
- Yukarıdaki `edge=0` stale-veri teyidini canlı/sim veri biriktikçe
  doğrulamaya devam et — yeni kapanan trade'lerin `edge_at_entry`'sinin
  gerçekten sıfırdan farklı yazıldığından emin ol
  (`data/trade_analyses.json`'a yeni giren kayıtları kontrol ederek).
- `agents/whale_tracker.py:48`'deki `market` query param sorusu hâlâ
  doğrulanamadı (ağ erişimi bu oturumda da `connect_rejected`). Kalıcı bir
  açık madde — yalnızca gerçek prod ağ erişimiyle çözülebilir.
- 94. turun bıraktığı `_sim_target`'e ulaşmadan önce eşzamanlı açık sim
  pozisyon sayısı için ayrı, açık bir üst sınır sorusu hâlâ cevaplanmadı.
