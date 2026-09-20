# Günlük Strateji İncelemesi — 2026-09-20 (104. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = `f47c5cc` (#184/#185/#186 — 103. tur — hepsi
merge edilmiş). Bu branch (`claude/brave-faraday-g2ed4g`) `origin/main` ile
tam eşit başladı, açık farkı yoktu.

Konteynerde çalışan bir bot instance'ı yok (`data/status.json`/
`control.json`/`positions.json` bu sandbox'ta yok, ağ erişimi de yok) —
%10 hedefine karşı gerçek zamanlı sermaye ilerlemesi bu oturumdan
doğrulanamıyor; katkı kod/strateji doğruluğu seviyesinde kalıyor.

## Baseline doğrulama
`pip install -r requirements.txt` + `python3 -m pytest tests/
calibration/tests execution_realism/tests crypto_directional/tests
signal_bridge/tests -q` → **1773 passed, 4 skipped** (103. turun
1769'undan +4 — #185 (`test_web_server_status_meta_merge.py`) ve #186
(`test_cycle_risk_budget_sim_capital_floor.py`) main'e merge olduğu için
beklenen artış).

## Bu turda incelenen alan

103. turun "sıradaki tur için notlar" bölümü dört aday önerdi:
`shadow_runner/*`, `calibration/*`, `agents/subagents/{research_agent,
reviewer_agent}.py`, ve `monitoring/daily_review.py::
write_readiness_verdict()` zinciri. Hepsi derinlemesine takip edildi;
none'da yeni bir canlı-karar hatası bulunamadı (aşağıda özetlendi). Bunun
üzerine arama genişletildi ve gerçek hata `core/candlestick_analyzer.py`'de
(102. turda kısmen düzeltilmiş, ama tam taranmamış dosya) bulundu.

### 1. `shadow_runner/*` zinciri — canlı etkisi doğrulandı, hata yok
`agents/orchestrator.py` sadece `JournalWriter` ve `shadow_runner.types`
kullanıyor (shadow journal yazımı — canlı karara girmiyor,
sadece kayıt). `shadow_runner.readiness.assess_readiness()` ve
`shadow_runner.summary_metrics`/`validation` yalnızca
`monitoring/daily_review.py` tarafından tüketiliyor (aşağıda #4).

### 2. `control_plane/live_gate.py::_check_readiness()` + `shadow_runner/readiness.py::assess_readiness()` — satır satır okundu, hata yok
`assess_readiness()`'in verdict mantığı (blocker→NO_GO, ≥1 fail→NO_GO,
≥3 warn→CONDITIONAL_REVIEW, `strict_metrics`/`regime_review` eksikse
CONDITIONAL_REVIEW'a sabitleniyor) tutarlı. `_check_readiness()`
(`control_plane/live_gate.py:190`) dosya yoksa/parse hatasındaysa/
`verdict != TINY_PILOT_CANDIDATE`'sa/yaş `max_age_hours`'ı aşıyorsa
fail-closed davranıyor — hepsi doğru. `agents/orchestrator.py`'nin
`check_live_gate()` çağrıları (satır 1106, 1393) sadece
`self._is_live_trading()` iken çalışıyor ve gerçek `capital`/
`open_count`/`market_id` değerlerini geçiyor — stub/bypass yok.

### 3. `monitoring/daily_review.py::write_readiness_verdict()` — gerçekten hiç çağrılmıyor, ama bu bir hata değil
`grep -rn "write_readiness_verdict"` → tanımı hariç TEK eşleşme
`operator_layer/ledgers.py`'nin docstring'inde (referans, çağrı değil).
Fonksiyonun kendisi hiçbir yerden (kod, test, CI) çağrılmıyor — repo'da
CI workflow'u da yok. Sonuç: `data/readiness_verdict.json` hiçbir zaman
otomatik yazılmıyor, yani `check_live_gate()`'in "readiness" kontrolü
(#3/11) otomatik yolda HER ZAMAN "bulunamadı" ile fail oluyor.

Bu bir hata değil — `control_plane/types.py`'nin INC-2026-03-15-001
sonrası doktrini ("Sinyal → emir arasında insan onayı ZORUNLU") ve
`shadow_runner/readiness.py`'nin kendi tasarım notu (`PilotConstraints`:
"GO verdict authorizes a 3-day, single-asset, $10 max position pilot
only") ile tam tutarlı: TINY_PILOT_CANDIDATE verdict'i kasıtlı olarak
insan operatörün `generate_daily_review()` + `write_readiness_verdict()`'i
elle çalıştırıp gözden geçirmesini gerektiriyor. Otomatik yol fail-closed
kalıyor (güvenli yön) — `control_plane/approval_queue.py`'nin 103. turda
tespit edilen ölü-ama-güvenli örüntüsüyle aynı sınıf. Düzeltme gerektirmez.

### 4. `agents/subagents/{research_agent,reviewer_agent,coordinator}.py` — satır satır okundu, hata yok
Üçü de daha önceki turlarda (22., 52., FIX-A, FIX-B, trade_number-matching
fix'leri gibi) ağır şekilde düzeltilmiş ve halihazırda geniş test kapsamı
var (`test_reviewer_*　.py` x5). Bu turda satır satır yeniden okundu:
`ReviewDecision.approved`'ın VETO'yu dahil etmesi (AutonomousEngine'in
REVIEWER_VETO×0.25 dalı için kasıtlı), `_parse_claude_response()`'un
trade_number ile eşleştirmesi, `suggested_size_pct` clamp'i (0-1),
`coordinator.py`'nin REDUCE'u `sig.size`'a erken uygulamayıp
orchestrator'a post-floor bırakması — hepsi doğru ve dokümante. Yeni bir
hata bulunamadı.

### 5. `core/candlestick_analyzer.py` — YENİ HATA BULUNDU VE DÜZELTİLDİ

Bu dosya 102. turda kısmen düzeltilmişti (HAMMER/HANGING_MAN doji-shape
bug'ı) ama o tur sadece tek bir kural bloğunu (#3/#5) incelemişti. Bu turda
dosyanın tamamı satır satır okundu ve `THREE_INSIDE_UP`/`THREE_INSIDE_DOWN`
(kural #18/#19) kurallarında eksik bir harami-containment kontrolü
bulundu.

**Hata**: Gerçek bir harami (içteki mum, dıştaki mumun gövdesi içinde tam
olarak kalmalı) hem üst HEM alt sınırı gerektirir — tam da yukarıdaki
`BULLISH_HARAMI`/`BEARISH_HARAMI` (kural #10/#11) kurallarının doğru
yaptığı gibi (`body_top(iç) < body_top(dış) AND body_bot(iç) >
body_bot(dış)`). `THREE_INSIDE_UP` sadece `CA._body_top(c2) < c3[1]`
kontrol ediyordu (`CA._body_bot(c2) > c3[4]` YOK); `THREE_INSIDE_DOWN` da
simetrik olarak sadece `CA._body_bot(c2) > c3[1]` kontrol ediyordu
(`CA._body_top(c2) < c3[4]` YOK). Sonuç: c2'nin gövdesi c3'ün gövdesinden
diğer taraftan taşsa bile (gerçekte harami DEĞİL, sadece iki üst üste
binen mum) desen hâlâ ateşleniyordu.

**Doğrulama** (`python3 -c` ile önce-sonra):
```
c3 bearish (open=100, close=90), c2 bullish (open=85, close=88 → body 85-88,
c3'ün 90-100 gövdesinin ALTINA taşıyor), c1 bullish (close=105, confirm).
Düzeltme öncesi: ['THREE_INSIDE_UP'], score=0.7
Düzeltme sonrası: [], score=0.0
```
Simetrik `THREE_INSIDE_DOWN` senaryosu da doğrulandı.

**Canlı etki** (`strategies/arbitrage_engine.py` satır ~1200-1274):
`THREE_INSIDE_UP`/`THREE_INSIDE_DOWN` `_BULLISH_REVERSAL`/
`_BEARISH_REVERSAL` setlerinde, `pattern_score()`'a ±0.7 katkı yapıyor.
Tek başına yanlış-pozitif bir tespit, `_pattern_bullish = _pattern_score
>= 0.6` / `_pattern_bearish = _pattern_score <= -0.4` eşiklerini TEK
BAŞINA aşabiliyor (başka bir onay gerekmeden) — yani gerçekte harami
olmayan iki üst üste binen mumdan `CANDLE_YES_ACTIVATE`/
`CANDLE_NO_ACTIVATE` tetiklenebiliyordu.

**Düzeltme**: Her iki kurala eksik containment bound'u eklendi
(`core/candlestick_analyzer.py`). Regresyon testi eklendi:
`tests/test_three_inside_requires_full_harami_containment.py` (6 test —
her iki yön için false-positive reddi, true-positive'in hâlâ ateşlenmesi,
ve pattern_score eşiklerinin artık aşılmadığının doğrulanması).

Not: `tests/` altında `THREE_INSIDE` için önceden HİÇ test yoktu
(`grep -rl THREE_INSIDE tests/` boş dönüyordu) — 102. turun hanging-man
fix'i ile aynı "az test edilmiş sınıflandırma kuralı" örüntüsü.

## Tam suite
Baseline (değişiklik öncesi): **1773 passed, 4 skipped**.
Bu turdan sonra: **1779 passed, 4 skipped** (+6 yeni test, 0 regresyon).

`data/autonomous_state.json`'da test suite çalıştırma yan etkisi oluştu
(bilinen davranış) — `git checkout -- data/autonomous_state.json` ile
geri alındı.

## Sermaye/performans notu
Bu oturumda çalışan bir bot instance'ı yok, %10 hedefine karşı gerçek
ilerleme bu oturumdan doğrulanamıyor. Bu turun katkısı: candlestick pattern
tanıma katmanında canlı BUY/NO sinyaline sızabilen somut bir yanlış-pozitif
kaynağı kapatıldı — bu, sinyal kalitesini (dolayısıyla dolaylı olarak
kazanç hedefini) doğrudan etkileyen bir düzeltme.

## Sıradaki tur için notlar
- `core/candlestick_analyzer.py` artık tamamen satır satır taranmış ve
  temiz (25 desen kuralı + `trend_analysis`/`multi_tf_score` dahil) —
  bir sonraki turun bu dosyaya dönmesi düşük getirili olur.
- `calibration/{calibrator,probability_mapper,edge_estimator}.py` bu
  turda incelenmedi (zaman bütçesi candlestick bulgusuna gitti) — 103.
  turun notu hâlâ geçerli: `grep` bunların `agents/`/`strategies/`
  tarafından hiç import edilmediğini gösteriyor, muhtemelen
  `crypto_directional/` sınıfında ölü kod, teyit edilmeli.
- `write_readiness_verdict()` zincirinin kasıtlı-manuel-gate olduğu bu
  turda netleştirildi (bkz. yukarı #3) — bir sonraki tur bunu tekrar
  "araştırılacak" olarak listelememeli.
- Henüz derinlemesine taranmamış adaylar: `shadow_runner/{runner,replay,
  reporting}.py`'nin kendi iç mantığı (sadece giriş/çıkışları
  doğrulandı, gövdeleri satır satır okunmadı), `agents/subagents/
  signal_agent_v2.py` (coordinator/reviewer üzerinden dolaylı defalarca
  isim geçti ama kendisi ayrı ayrı son ~15 turda satır satır
  taranmadı).
