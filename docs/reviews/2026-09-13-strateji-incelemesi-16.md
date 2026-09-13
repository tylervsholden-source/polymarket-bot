# Günlük Strateji İncelemesi — 2026-09-13 (16. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum özeti
- Branch `claude/brave-faraday-q6koi9`, `main`'in en son commiti (`9e33f88`,
  15. çalışma) üzerinden başlatıldı. Açık PR yok, `git status` temiz.
- `data/control.json`/`positions.json`/`.env` bu ortamda yok → gerçek API
  kimlik bilgisi veya canlı pozisyon yok.

## Bugünkü derin inceleme: daha önce hiç dokunulmamış control_plane/ ve
## strategies/ dosyaları

Görev talimatı 13 dosyayı işaret ediyordu (`control_plane/*`,
`calibration/*`, `strategies/edge_model.py`, `stoikov.py`, `spread_model.py`,
`sum_monitor.py`, `quality_filter.py`). İlk adım olarak her biri grep ile
canlı yola (`main.py` → `agents/orchestrator.py` →
`strategies/arbitrage_engine.py` / `agents/subagents/coordinator.py`)
gerçekten bağlı mı diye doğrulandı:

- **`calibration/*` (decision_policy.py, edge_estimator.py,
  probability_mapper.py) — ÖLÜ KOD, dokunulmadı.** Bu dosyalar yalnızca
  `shadow_runner/replay.py` ve `shadow_runner/runner.py` tarafından import
  ediliyor. `agents/orchestrator.py` `shadow_runner.journal` ve
  `shadow_runner.types`'ı import ediyor ama `shadow_runner.replay`/`.runner`'ı
  **hiç** import etmiyor — bunlar yalnızca `tests/`, `_gen_artifacts.py` ve
  `_gen_snapshot.py`'den çağrılıyor (15. çalışmanın zaten tespit ettiği
  418-dosyalık `incident_bundle/`/`review_bundle/` kopyalarıyla aynı sınıf:
  main.py'nin import zincirine hiç girmiyor). CLAUDE.md "Sadelik" kuralı
  gereği bağlanmamış/ölü modüle dokunulmadı.
- **`control_plane/entry_window_guard.py`, `expiry_guard.py`,
  `process_lock.py`, `reentry_guard.py`, `approval_queue.py`** — hepsi
  gerçekten canlı: `main.py` (`ProcessLock`), `agents/orchestrator.py`
  (`check_live_gate`, `ReentryGuard`, `ExpiryGuard`,
  `parse_market_times`/`check_entry_window`) üzerinden 11-nokta
  `check_live_gate()` fonksiyonuna doğru bağlanıyor. Satır satır izlendi
  (özellikle `expiry_guard`/`market`'in `"T" in end_date_iso` koşuluyla
  koşullu geçirilmesi, `is_recheck_after_approval` bayrağı, cooldown
  dosya kalıcılığı, PID lock mantığı) — CLAUDE.md/docstring'lerle tutarsız
  bir davranış bulunamadı.
- **`strategies/edge_model.py`, `stoikov.py`, `spread_model.py`,
  `sum_monitor.py`** — hepsi `arbitrage_engine.py` içinde gerçekten
  kullanılıyor (`self.edge_model.total_cost/single_market_edge`,
  `self.spread_model.find_dislocations`, `self.sum_monitor.analyze`).
  `strategies/stoikov.py`'nin kendisi `arbitrage_engine.py`'de
  instantiate edilip hiç çağrılmıyor (dead instantiation) ama gerçek
  kullanımı `strategies/maker_engine.py` üzerinden — `MakerEngine` da
  `orchestrator.py`'de `MAKER_ENABLED` (varsayılan `false`, "KAPALI" olarak
  belgelenmiş) ile doğru şekilde kapalı. Davranış değişikliği yok.
- **`strategies/quality_filter.py`** — 2026-09-12 (2. çalışma) tarafından
  zaten incelenmiş ve bilinçli olarak bağlanmamış bırakılmış (min-volume
  filtresi `MIN_MARKET_VOLUME` env var ile ayrı yolla bağlandı).

## Bugün bulunan ve düzeltilen gerçek hata: "TÜM EXTERNAL BOOST'LAR DEVRE
## DIŞI" resetinin, kendi listesinde OLMAYAN 4 sinyali de silmesi

### Kod incelemesi
`strategies/arbitrage_engine.py::_evaluate_market()` içinde (9b5fd52 "full
bot update" commit'inden beri, hiçbir önceki inceleme dokunmamış):

```python
_pre_boost_prob = bayesian_prob          # <- SmartMoney/TopTrader/OB_Depth/
_TOTAL_BOOST_CAP = 0.04                  #    KalshiArb'DAN ÖNCE yakalanıyordu

# Smart money boost (reduced: 0.05 → 0.02, volume-gated)
...
bayesian_prob = max(0.05, min(0.95, bayesian_prob + boost))   # SmartMoney
...
# ── TOP TRADER COPY SIGNAL ──
bayesian_prob = max(0.05, min(0.95, bayesian_prob + _tt_boost))  # TopTrader
...
# ── ORDERBOOK DEPTH ANALYSIS ──
bayesian_prob = max(0.05, min(0.95, bayesian_prob + _ob_boost))  # OB_Depth
...
# ── KALSHI CROSS-ARB CHECK ──
bayesian_prob = max(0.05, min(0.95, bayesian_prob + _kalshi_adj))  # KalshiArb
...
# TÜM EXTERNAL BOOST'LAR DEVRE DIŞI — ...
# Kapatılan: SPIKE, MTF, LEAD_LAG, FUNDING, LS_RATIO, LIQUIDATION,
#            SPX_CORR, FNG, ENHANCED (multi-exchange, options, whale, social)
bayesian_prob = _pre_boost_prob   # <- SmartMoney/TopTrader/OB_Depth/KalshiArb
                                   #    dahil HER ŞEYİ siliyor, listede olmasalar bile
```

`_pre_boost_prob`, SmartMoney/TopTrader/OB_Depth/KalshiArb bloklarından
**önce** yakalanıyordu. Bu dört sinyal koşup `bayesian_prob`'u değiştiriyor,
loglarına da (`"SmartMoney ... boost=+0.020 prob: 0.948→0.670"`) gerçekten
uygulanmış gibi yazıyordu — ama hemen ardından gelen reset satırı
`bayesian_prob`'u `_pre_boost_prob`'a, yani bu dört sinyalden önceki değerine
geri döndürüyordu. Resetin kendi yorumu SPIKE/MTF/LEAD_LAG/FUNDING/
LS_RATIO/LIQUIDATION/SPX_CORR/FNG/ENHANCED'i "Kapatılan" olarak sayıyor;
SmartMoney, TopTrader, OB_Depth, KalshiArb bu listede **yok** — hiç
kapatılmaları amaçlanmamış. Üstelik SmartMoney'nin kendi kod yorumu
("reduced: 0.05 → 0.02") hâlâ aktif bir boost öngörüyor, ve
`docs/architecture.md` açıkça "SmartTraderTracker -> +/-0.05 boost"'u canlı
sinyal hattının parçası olarak belgeliyor.

15. çalışma bu bloğun tam tersi bir hatasını bulmuştu (SPIKE/MTF/LEAD_LAG
resetten SONRA çalıştığı için hiç kapanmıyordu). O düzeltme uygulandıktan
sonra reset satırından sonraki HER ŞEY (SPIKE, MTF, LEAD_LAG dahil artık
hepsi) devre dışı olduğu için, resetin fiilî tek etkisi bugüne kadar
listelenmeyen bu dört sinyali sessizce silmek olarak kaldı — yani bugünkü
sim/canlı çalıştırmalarda SmartMoney, TopTrader, OB_Depth, KalshiArb boost'ları
hesaplanıp loglanıyor ama edge/Kelly boyutlandırmasını **hiçbir zaman**
etkilemiyordu.

### Etki
Somut senaryo (regresyon testinde reprodüklendi): güçlü bullish BTC 15dk
marketi, `SmartTraderTracker.get_signal()` net +1.0 sinyal (2 whale alıcı,
`volume_ratio` gate'i geçiyor) döndürdüğünde log `boost=+0.020,
prob: 0.948→0.670` yazıyor, ama fonksiyonun döndürdüğü nihai
`bayesian_prob` **0.650** — tam olarak nötr SmartTraderTracker ile aynı
sonuç. Whale/smart-trader sinyali CLAUDE.md/architecture.md'nin
belgelediği tek harici sinyal kaynaklarından biri; bu sessizce hiç
çalışmıyor olması, botun "büyük pozisyon takibi" temel özelliklerinden
birinin fiilen devre dışı olduğu anlamına geliyor.

### Düzeltme
`_pre_boost_prob = bayesian_prob` (ve `_TOTAL_BOOST_CAP` tanımı) yakalama
noktası, SmartMoney/TopTrader/OB_Depth/KalshiArb bloklarından **sonra**,
"TÜM EXTERNAL BOOST'LAR DEVRE DIŞI" reset satırının hemen öncesine
taşındı. Böylece:
- SmartMoney/TopTrader/OB_Depth/KalshiArb artık gerçekten `bayesian_prob`'u
  etkiliyor (listede olmadıkları için zaten kapatılmaları amaçlanmamıştı).
- Reset satırı hâlâ, yorumda listelenen 9 sinyali (hepsi zaten ayrı ayrı
  yorum satırına alınmış durumda) doğru şekilde sıfırlıyor — davranışları
  değişmedi (15. çalışmanın testi hâlâ geçiyor).
- Reset bloğuna, hangi 4 sinyalin listede olmadığı ve neden kasıtlı olarak
  açık bırakıldığı açıklayan bir not eklendi (gelecekteki incelemelerin
  aynı karışıklığa düşmemesi için).

`tests/test_smart_money_boost_survives_reset.py` eklendi: aynı
market/BinanceFeed senaryosunu (test_disabled_boosts_stay_disabled.py'deki
gibi) önce nötr bir `SmartTraderTracker` ile, sonra güçlü aynı yönlü sinyal
döndüren bir `SmartTraderTracker` ile çalıştırıp `bayesian_prob`'un
**değişmesi** gerektiğini doğruluyor (yaklaşık +0.02, ±0.04 agregat cap ile
sınırlı). Düzeltme öncesi kodda test **başarısız oluyor**
(`baseline=0.65 == boosted=0.65`, log'da boost uygulanmış görünse de nihai
prob aynı kalıyor), düzeltme sonrası **geçiyor**.

## Doğrulama
- `python3 -c "import ast; ast.parse(open('strategies/arbitrage_engine.py').read())"` → hatasız.
- Düzeltme öncesi (`git stash` ile geçici geri alma): yeni test **FAIL**
  (`AssertionError: ... baseline=0.65 == boosted=0.65 (reset wiped it out)`).
- Düzeltme sonrası: yeni test + 15. çalışmanın testi birlikte **2 passed**
  (SmartMoney artık etkiliyor, SPIKE/MTF/LEAD_LAG hâlâ etkilemiyor —
  regresyon yok).
- `pytest tests/` → **609 passed, 2 skipped** (608 → 609: 1 yeni test dosyası,
  mevcut testlerden hiçbiri bozulmadı).
- `git status` → yalnızca amaçlanan iki değişiklik
  (`strategies/arbitrage_engine.py` + yeni test dosyası); test çalıştırma
  yan etkisiyle değişen `data/autonomous_state.json` commit'lenmeden geri
  alındı.

## Sonuç
16. çalışma, görev talimatındaki 13 dosyayı grep ile canlı yola bağlılık
açısından doğruladı: `calibration/*` (3 dosya) main.py/orchestrator.py'nin
import zincirine hiç girmediği için (yalnızca test/generator script'lerinden
kullanılan `shadow_runner.replay`/`.runner` üzerinden erişiliyor) ölü kod
olarak bırakıldı; `control_plane/*` (5 dosya) ve `strategies/edge_model.py`,
`stoikov.py`, `spread_model.py`, `sum_monitor.py` gerçekten canlı ve satır
satır incelendi, tutarsızlık bulunamadı. Asıl bulgu yine
`arbitrage_engine.py`'deki 15. çalışmanın da değindiği reset bloğunda: bu
kez ters yönde bir hata — resetin kendi "Kapatılan" listesinde OLMAYAN
SmartMoney/TopTrader/OB_Depth/KalshiArb sinyalleri, `_pre_boost_prob`'un
onlardan önce yakalanması yüzünden sessizce siliniyordu. Düzeltme minimal
(yakalama noktasını 4 blok aşağı taşımak + açıklayıcı not) ve regresyon
testiyle kilitlendi (düzeltme öncesi kodda testin gerçekten fail ettiği
doğrulandı, önceki 15. çalışmanın testi de hâlâ geçiyor).
