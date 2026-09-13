# Günlük Strateji İncelemesi — 2026-09-13 (17. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum özeti
- Branch `claude/brave-faraday-j1kff1`, session açılışında görev talimatındaki
  designated branch olarak boştu (remote'ta yoktu). `main`'in en son commiti
  (`86ec7c8`, 16. çalışma, PR #35) üzerinden oluşturuldu.
- Açılışta açık PR #35 (16. çalışma) bulundu. İzole bir git worktree'de
  bağımsız doğrulandı: `pytest tests/` → 609 passed, 2 skipped; ayrıca yeni
  regresyon testi (`test_smart_money_boost_survives_reset.py`) pre-fix commit
  (`9e33f88`) üzerinde çalıştırılıp gerçekten FAIL ettiği, post-fix'te PASS
  ettiği doğrulandı. PR squash-merge edildi (`86ec7c8`).
- `data/control.json`/`positions.json`/`.env` bu ortamda yok → gerçek API
  kimlik bilgisi veya canlı pozisyon yok; bu çalışma tamamen statik kod
  incelemesi + test kanıtı üzerinden yürütüldü.
- Not: geçmiş PR gövdelerinin "main'e merge edildi" iddialarının aksine,
  bu çalışmanın başında `origin/main` GitHub API üzerinden hâlâ 16.
  çalışmanın taban commiti öncesindeydi (yerel bir `git fetch` çağrısı
  sessizce eksik kalmıştı) — GitHub API (`list_commits`/`list_branches`)
  ile çapraz kontrol edilerek doğrulandı, gerçek `main` durumu buydu.

## Bugünkü inceleme kapsamı
16. çalışma 13 dosyayı (control_plane/*, calibration/*,
strategies/{edge_model,stoikov,spread_model,sum_monitor,quality_filter})
zaten satır satır doğrulamıştı. Bu çalışma, orchestrator'ın gerçek import
zincirinde olup önceki çalışmaların değinmediği bir sonraki katmana indi:
`agents/subagents/{research_agent,coordinator,signal_agent_v2,
reviewer_agent}.py` (multi-agent pipeline'ın merge/enrich/review mantığı) ve
`agents/autonomous_engine.py` (otonom karar motoru — 12. çalışma sadece
aggression-label mislabel'ını bulmuştu, motorun geri kalanı incelenmemişti).

- `research_agent.py` → `coordinator.py` → `signal_agent_v2.py` →
  `reviewer_agent.py` veri akışı satır satır izlendi: `ResearchResult.
  get_market_context()`'in ürettiği her alan (whale/smart-money/regime/
  orderflow/RSI) hem `SignalAgentV2.run()`'da hem `Coordinator._re_enrich_
  signals()`'da gerçekten `EnrichedSignal`'a yazılıyor; `_compute_confluence`
  ve `_detect_risk_flags`'in ürettiği flag isimleri (`COUNTER_REGIME_NO/YES`,
  `WHALE_OPPOSITION`, `RSI_OVERBOUGHT/OVERSOLD`, `THIN_EDGE`,
  `ORDERFLOW_OPPOSITION`, `REGIME_OVEREXTENDED`) `reviewer_agent.py`'nin
  rule-based fallback'ındaki kontrollerle (13. çalışmanın COUNTER_REGIME
  prefix-match düzeltmesi dahil) birebir eşleşiyor — tutarsızlık bulunamadı.
  `Coordinator.run_cycle()`'daki REDUCE size uygulaması (10. çalışma) ve
  API-down FIX-B fallback filtresi de yeniden doğrulandı, davranış tutarlı.

## Bugün bulunan ve düzeltilen gerçek hata: Drawdown koruması hiçbir zaman
## gerçek session tepe noktasını takip etmiyordu

### Kod incelemesi
`agents/autonomous_engine.py::_update_performance()`:
```python
# Drawdown
if perf.initial_capital > 0:
    peak = max(perf.initial_capital, capital)
    perf.drawdown_pct = max(0, (peak - capital) / peak)
```
`peak`, her çağrıda yalnızca `initial_capital` (sabit env var) ile o anki
`capital`'ın maksimumu olarak yeniden hesaplanıyor — botun session içinde
gerçekten ulaştığı en yüksek sermayeyi hatırlayan hiçbir state yok. Sermaye
`initial_capital`'ın üzerine bir kez çıktıktan sonra, `peak` matematiksel
olarak her zaman o çağrının `capital` değerine eşit oluyor
(`max(initial, capital) == capital` when `capital > initial`), yani
`(peak - capital) == 0` her zaman — **sermaye ne kadar büyümüşse büyümüş
olsun, sonraki her türlü düşüş `drawdown_pct=0` olarak raporlanıyor.**

`evaluate()`'deki tek kullanım noktası:
```python
if self._performance.drawdown_pct > self.DRAWDOWN_DANGER:  # 0.90
    risk_level = RiskLevel.CRITICAL
```
Bu botun hedefi $1000→$3000 (sermayenin büyümesi rutin bekleniyor); sermaye
büyüdükten sonra yaşanacak herhangi bir gerçek çöküş (ör. $1000→$10.000→$900,
gerçek tepe noktasından %91 düşüş) bu korumayı **hiçbir zaman** tetikleyemez
— çünkü $900, `initial_capital` olan $1000'in altında olsa bile `peak`
o anki `capital`'dan (900) değil, `max(1000, 900)=1000`'den hesaplanıyor ve
görünen drawdown yalnızca %10 oluyor, gerçek %91 değil.

### Düzeltme
`PerformanceSnapshot`'a `peak_capital` alanı eklendi; her
`_update_performance()` çağrısında `peak_capital = max(peak_capital,
initial_capital, capital)` ile gerçek session tepe noktası güncelleniyor ve
`drawdown_pct` bu gerçek tepe noktasından hesaplanıyor. Minimal değişiklik —
`DRAWDOWN_DANGER` eşiği veya başka hiçbir mantık değişmedi, yalnızca
`drawdown_pct`'in artık gerçekte ölçtüğü şeyi ölçmesi sağlandı.

`tests/test_drawdown_tracks_session_peak.py` eklendi:
1. `_update_performance()`'ı doğrudan çağırıp $1000→$3000 (yeni peak, 0%
   drawdown) → $1500 (peak'ten gerçek %50 düşüş, ama initial_capital'ın hâlâ
   üzerinde) senaryosunda `drawdown_pct≈0.5` bekliyor.
2. `evaluate()` üzerinden uçtan uca: $1000→$10.000 peak → $900 (peak'ten
   >%90 düşüş) senaryosunda nihai `risk_level` CRITICAL olmalı.

Düzeltme öncesi kodda (`git stash` ile geçici geri alma) her iki test de
**FAIL** ediyor (`drawdown=0.0%`, `risk_level=LOW`); düzeltme sonrası her
ikisi de **PASS**.

## Bugün ayrıca bulunan ve düzeltilen ikinci sorun: `test_no_valuation_live_
## orderbook.py` gerçek wall-clock saate bağlı flaky

Tam test suite çalıştırıldığında (`pytest tests/`)
`test_no_position_valuation_uses_real_orderbook_not_stale_gamma_fallback`
gerçek çalıştırma anında **FAIL** etti: `get_orderbook` hiç çağrılmadı.
Kök neden 6a2ba74'ün (9. çalışma) düzelttiği sınıfla birebir aynı:
`core/position_manager.py::update_positions()`'ın question-parse edilen
TIME_EXPIRED kontrolü `datetime.now(ZoneInfo("America/New_York"))`'u
doğrudan çağırıyor, testte mock'lanacak bir yer yok. Testin fixture
pozisyonu `"1:00PM-1:05PM ET"` saatini kullanıyor; kontrol saat-of-day
üzerinden 12 saatlik kayan bir pencereyle çalışıyor (13:05 ET ile ertesi
gün 01:05 ET arası — günün yarısı) — suite bu pencere içinde bir anda
çalıştığında (bu çalıştırmada gerçekten öyle oldu: `now=13:09 ET`) pozisyon
"expired" sayılıp orderbook hiç sorgulanmadan farklı bir kod yoluna giriyor,
testin asıl doğrulamak istediği path'i sessizce atlıyor.

Düzeltme: `datetime.now(ZoneInfo(...))` çağrısı `core/position_manager.py`'de
`_now_et()` adında modül seviyesi, mock'lanabilir bir fonksiyona çıkarıldı
(iki kullanım noktası da bu fonksiyona yönlendirildi — `strategies.
arbitrage_engine._get_current_et_hour()` ile aynı desen). `tests/conftest.py`
içine, mevcut `_pin_et_hour_gate` fixture'ının yanına yeni bir autouse
fixture (`_pin_position_manager_et_clock`) eklendi; `_now_et`'i sabit
09:00 ET'e pinliyor — testin `"1:00PM-1:05PM ET"` fixture'ı bu saatte açıkça
non-expired. Production davranışı değişmedi (gerçek çalışmada `_now_et()`
hâlâ gerçek saati döndürüyor); yalnızca test suite artık günün hangi saatinde
çalıştığından bağımsız deterministik.

## Doğrulama
- Düzeltme öncesi (`git stash` ile `agents/autonomous_engine.py` geri
  alındı): `test_drawdown_tracks_session_peak.py` → **2 failed**
  (`drawdown=0.0%`, `risk_level=LOW` beklenen CRITICAL yerine).
- Düzeltme sonrası: aynı test dosyası → **2 passed**.
- Tam suite çalıştırılırken (düzeltmeler öncesi hâliyle)
  `test_no_valuation_live_orderbook.py` gerçek çalıştırma anında FAIL etti
  (kanıt: yukarıdaki bölüm); `_now_et()` + conftest pin sonrası aynı test
  3 kez art arda stabil PASS (farklı gerçek saatlerde tekrar çalıştırıldı).
- `pytest tests/` → **611 passed, 2 skipped** (609 → 611: 2 yeni drawdown
  testi, mevcut testlerden hiçbiri bozulmadı).
- `git status` → yalnızca amaçlanan dört değişiklik
  (`agents/autonomous_engine.py`, `core/position_manager.py`,
  `tests/conftest.py`, yeni test dosyası); test çalıştırma yan etkisiyle
  değişen `data/autonomous_state.json` commit'lenmeden geri alındı.

## Sonuç
17. çalışma önce açık PR #35'i (16. çalışma) bağımsız doğrulayıp merge etti,
sonra multi-agent pipeline'ın merge/enrich/review katmanını (research_agent
→ coordinator → signal_agent_v2 → reviewer_agent) satır satır izleyip
tutarsızlık bulamadı. Asıl bulgu `autonomous_engine.py`'nin drawdown
hesabında: session tepe noktası hiç takip edilmiyordu, bu da CRITICAL
drawdown korumasının botun kendi $1000→$3000 hedefinin doğal sonucu olan
"önce büyü, sonra düş" senaryosunda fiilen hiç çalışamayacağı anlamına
geliyordu — sermaye koruması açısından en önemli günlük hedefle (kazancı
korumak) doğrudan ilgili bir hata. Ayrıca, tam suite çalıştırılırken gerçek
zamanda yakalanan bir flaky test (9. çalışmanın ET-hour gate flakiness'iyle
aynı kök neden sınıfı, farklı bir dosyada) kalıcı olarak düzeltildi. Her iki
düzeltme de regresyon testiyle kilitlendi (düzeltme öncesi kodda gerçekten
fail ettikleri doğrulandı).
