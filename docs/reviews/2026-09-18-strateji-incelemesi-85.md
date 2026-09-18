# Günlük Strateji İncelemesi — 2026-09-18 (85. tur)

## Durum
Oturum başında `origin/main` = `8ee14c5` (#148, konsolidasyon turu #12'nin
84. incelemenin iki PR'ını — Kalshi cross-arb timestamp fix'i ve sim-mode
kelly/walk-forward/autonomous_engine routing fix'i — birleştirdiği son
durum). `pip install -r requirements.txt` + `pip install pytest
pytest-asyncio scikit-learn` sonrası taban test suite'i doğrulandı:
`tests/` alt kümesi **860 passed, 2 skipped**; pytest.ini'nin kapsadığı tüm
dizinler (`tests`, `calibration/tests`, `execution_realism/tests`,
`crypto_directional/tests`, `signal_bridge/tests`) ile tam koşum **1692
passed, 4 skipped** — 84. tur/konsolidasyon #12'nin bildirdiği rakamla
birebir.

## Bu turda yapılanlar
84. turun düzelttiği sim/paper-mode `closed_trades` routing bug'ının
(`Orchestrator._current_closed_trades()`) TÜM kardeş çağrı noktalarına
uygulanıp uygulanmadığı doğrulandı — `grep -n 'data\.get("closed"'
agents/orchestrator.py` ile dosyadaki her ham okuma tek tek izlendi. Ayrıca
`strategies/arbitrage_engine.py`'nin OPT-3 (momentum deceleration) hesap
zincirinin `agents/binance_feed.py::get_signal()` ile alan uyumu, `core/
position_manager.py`'nin partial-fill reconciliation'ı (80. tur fix'i),
restart-recovery (`_sync_open_orders_from_clob`, 78. tur fix'i),
`_extract_time_slot`/`_is_adjacent_to_loss_slot`'un gece yarısı (AM/PM)
sınır durumu ve `strategies/walk_forward.py`'nin sim-mode trade şemasıyla
(`pnl` alanı eksik) uyumu satır satır yeniden kontrol edildi — bunların
hiçbirinde yeni bir kusur bulunamadı (detay "İncelenip reddedilen
adaylar"da).

### Bulunan ve düzeltilen hata: `_analyze_new_closed_trades()` sim/paper modunda `TradeAnalyzer`'i hiç çağırmıyordu

`agents/orchestrator.py:1450` — `Orchestrator.run()`'ın her döngü sonunda
çağırdığı (`agents/orchestrator.py:440`) tek TradeAnalyzer giriş noktası:

```python
def _analyze_new_closed_trades(self):
    closed_trades = self.position_manager.data.get("closed", [])
    new_count = len(closed_trades) - self._last_analyzed_count
    if new_count <= 0:
        return
    ...
```

Bu satır, 82./83./84. turların `_update_loss_streak()`, `kelly.
update_streak()`, `walk_forward.validate()` ve `autonomous_engine.
evaluate()`/`get_adaptive_params()` için düzelttiği **aynı bug sınıfı**:
koşulsuz olarak SADECE `position_manager.data["closed"]` (gerçek CLOB
pozisyonları) okuyordu. `_is_live_trading()==False` iken (varsayılan/güncel
çalışma modu — bkz. CLAUDE.md, bu ortamda canlı `control.json` yok)
`_cycle()`'ın emir yerleştirme dalı `position_manager`'a hiç dokunmuyor —
sonuçlar `self._sim_trades`/`self._sim_results`'a yazılıyor (bkz.
`_check_sim_resolutions()`). Yani `position_manager.data["closed"]`
sim/paper modunda kalıcı olarak `[]` kalıyor.

Sonuç: `new_count = len([]) - self._last_analyzed_count` başlangıçta
`0 - 0 = 0`, hep `<= 0` — fonksiyon her döngüde ilk satırlarda `return`
ile çıkıyor. `self.trade_analyzer.analyze_trade()` — CLAUDE.md'nin belgelediği
mimarinin bir parçası olan post-trade root-cause analizi, sinyal doğruluk
kontrolü, 10+ pattern eşleştirmesi ve adaptif parametre önerileri — bot kaç
sim trade kapatırsa kapatsın **hiçbir zaman çalışmıyordu**. `analyzer_stats`
(`total_analyzed`, `pattern_count`) her zaman 0 kalıyor, 10 döngüde bir
loglanan `RECOMMENDATION` satırları hiç üretilmiyordu.

Bu, 84. turun tespit ettiği dört kardeş bug'la (kelly streak, walk-forward
confidence multiplier, autonomous engine risk sınıflandırması, adaptive
params) aynı kaynaktan geliyor ama 84. turun `_current_closed_trades()`
konsolidasyonu bu beşinci tüketiciyi kapsamamıştı — `grep -n
'position_manager.data.get("closed"'` ile taranınca (satır 654, 1453, 1524
ve 2334) `_analyze_new_closed_trades()`'in (1453) hâlâ ham okuma yaptığı
görüldü. Diğer üçü (654 dashboard-only status snapshot, 1524
`_finalize_cycle()`'ın dashboard yazımı, 2334 ölü kod — `return` sonrası
erişilemez satır) sadece görüntüleme amaçlı, trading kararına girmiyor;
sadece 1453 gerçek bir "risk/analiz mekanizması sessizce inert" örneğiydi.

**Somut senaryo**: Bot 20 sim trade kapatır, hepsi `self._sim_results`'a
yazılır (5'i LOSS, aynı "NO_TRAP" root-cause pattern'ine uyan). Fix
öncesinde `_analyze_new_closed_trades()` bunların hiçbirini görmez —
`get_recommendations()` boş kalır, `HEALTH` logundaki `ANALYZER:
analyzed=0 patterns=0` hiç değişmez, operatör pattern raporundan hiçbir
şey öğrenemez. Fix sonrası her sim trade kapanışında `analyze_trade()`
çağrılır, `total_analyzed` artar, pattern istatistikleri birikir.

**Fix** (`agents/orchestrator.py::_analyze_new_closed_trades`):
```python
# önce: closed_trades = self.position_manager.data.get("closed", [])
# sonra:
closed_trades = self._current_closed_trades()
```
84. turda eklenen `_current_closed_trades()` helper'ı (canlı modda
`position_manager.data["closed"]`, sim/paper modda `self._sim_results`)
kullanıldı — başka bir mantık değiştirilmedi. Sim trade sözlüklerinin
alanları (`market_id`, `question`, `direction`, `result`, `edge`,
`confluence_score`, `risk_flags`, `ts`, `closed_at`) `TradeAnalyzer.
analyze_trade()`'in tamamı `.get()` ile okuduğu alanlarla doğrudan uyumlu
olduğu doğrulandı (`order_id` sim trade'lerde yok, ama kod `if order_id:`
ile bunu zaten güvenli şekilde ele alıyor — dedup sadece gerçek CLOB
emirlerinde order_id doluyken devreye giriyor).

**Test**: `tests/test_sim_mode_trade_analyzer_source.py` — biri sim/paper
modda tek bir LOSS sim trade'in `_current_closed_trades()` üzerinden
`TradeAnalyzer.analyze_trade()`'e ulaştığını (ve `total_analyzed==1`
olduğunu) doğruluyor, diğeri `_analyze_new_closed_trades()`'in kaynak
kodunun artık `self._current_closed_trades()` kullandığını ve ham
`position_manager.data.get("closed", [])` okumasının kalmadığını
`inspect.getsource` ile doğruluyor (fix öncesi kaynağa karşı çalıştırılıp
başarısız olduğu, fix sonrası geçtiği elle doğrulandı). Test, gerçek
`data/trade_analyses.json`/`data/trade_patterns.json` dosyalarına
dokunmaması için `tests/test_trade_analyzer_neutral_not_loss.py`'nin
`monkeypatch`+`tmp_path` desenini takip ediyor (ilk denemede bu izolasyon
atlanmış, `TradeAnalyzer._save_history()` gerçek `data/trade_analyses.json`
dosyasının 3000+ satırlık geçmişini ezmişti — `git checkout --` ile geri
alındı, test dosyası düzeltilip yeniden doğrulandı).

Tam suite: `tests/` **862 passed, 2 skipped** (860/2 taban + 2 yeni test),
tüm dizinler dahil tam koşum **1694 passed, 4 skipped** (1692/4 taban + 2
yeni test).

## İncelenip reddedilen adaylar (yeni hata bulunamadı)

- **`agents/orchestrator.py`'nin diğer üç `data.get("closed", [])` çağrı
  noktası** (satır 654, 1524, 2334) — 654 ve 1524 sadece `_sw.update(...,
  closed=...)` dashboard status snapshot'ına besleniyor (görüntüleme amaçlı,
  hiçbir trading kararına girmiyor); sim modunda boş görünmesi kozmetik.
  2334, `_sync_real_balance()`'ın "Polymarket data API pozisyon sync'i
  DEVRE DIŞI" yorumundan hemen sonraki koşulsuz `return`'ün ALTINDA —
  erişilemez ölü kod, hiç çalışmıyor.
- **`strategies/arbitrage_engine.py:473-485`'in OPT-3 momentum-decel
  fallback zinciri** (`spot.get("momentum_decelerating", False)` sonra
  `"candle_changes" in spot` kontrolü) — `agents/binance_feed.py::
  get_signal()`'in döndürdüğü dict'te `"candle_changes"` anahtarı hiç yok
  (sadece zaten hesaplanmış `"momentum_decelerating"` booleanı var), yani
  bu dallardan ilki asla tetiklenmiyor, ikinci (`elif`) dal her zaman
  `volatility`/`change_pct` fallback'ini çalıştırıyor. Birim uyumsuzluğu
  şüphesiyle (`volatility` = `abs(trend_pct)/100 + 0.005`, yani yüzde
  DEĞİL kesir; `change_pct` ise yüzde puanı cinsinden, örn. `0.3` =
  `%0.3`) derinlemesine incelendi: pratikte `change_pct` neredeyse her
  zaman `volatility*0.5`'ten büyük (yüzde-puanı ölçeği kesir ölçeğinden
  ~100x büyük), yani fallback fiilen neredeyse hiç tetiklenmiyor —
  `binance_feed`'in gerçek OPT-3 hesabını ezmiyor. Riskli/karışık kod ama
  ölçülebilir bir yanlış davranış (before/after somut senaryo)
  kanıtlanamadı; zorlanmadı.
- **`core/position_manager.py`'nin partial-fill reconciliation'ı**
  (`_check_order_filled`, 80. tur fix'i) — `size_matched`'in her status'ta
  önce reconcile edilip sonra "freeze" kararının verildiği akış tekrar
  satır satır izlendi, doğru.
- **`agents/orchestrator.py::_sync_open_orders_from_clob`** (restart
  recovery, 78. tur fix'i) — `raw_outcome` eşlemesi (`YES/UP`, `NO/DOWN`,
  bilinmiyorsa eski varsayılan `YES`) hâlâ doğru.
- **`_extract_time_slot`/`_is_adjacent_to_loss_slot`'un gece yarısı sınırı**
  — `12:00AM` → `0` dakika, `12:00PM` → `720` dakika şeklinde dakika-since-
  midnight temsili doğru; `11:55PM-12:00AM` (kayıp) → `12:00AM-12:05AM`
  (sonraki slot) geçişi `loss_end==0 == slot_start` ile doğru tespit
  ediliyor — round edge-case testi yok ama mantık matematiksel olarak
  sağlam.
- **`strategies/walk_forward.py`'nin sim-mode trade şemasıyla `pnl` alanı
  eksikliği** — sim trade dict'lerinde `"pnl"` hiç yok, bu yüzden
  `train_avg_pnl`/`test_avg_pnl` sim modunda hep `0.0` raporlanıyor; ama bu
  iki değer sadece `result` dict'inin `train_avg_edge`/`test_avg_edge`
  görüntüleme alanlarına gidiyor — `recommendation`/`confidence_multiplier`
  kararı SADECE `test_wr`/`gap` (WR bazlı, `result` alanından) üzerinden
  veriliyor, `pnl`'e hiç dokunmuyor. Kozmetik, karar mekanizmasını
  etkilemiyor.
- **`agents/autonomous_engine.py::_update_performance`'in `pnl` alanına
  bağlı `session_pnl`/`hourly_pnl`** — aynı sebeple sim modunda hep `0.0`
  kalıyor, ama `drawdown_pct` (CRITICAL/SURVIVAL sınıflandırmasını
  belirleyen asıl alan) `capital` parametresinden (orchestrator'ın geçtiği
  gerçek `available_capital()`) hesaplanıyor, `pnl` listesinden değil —
  drawdown koruması etkilenmiyor. `session_pnl`/`hourly_pnl` hiçbir yerde
  okunmuyor (sadece set ediliyor), yani bu haliyle de trading kararına
  girmiyor.
- `agents/kalshi_arb.py::get_edge_adjustment()` (84. tur fix'i) —
  `max(matching, key=lambda v: v["timestamp"])` hâlâ doğru, başka bir
  cache-tazelik regresyonu bulunamadı.

## Sonuç
Bir gerçek hata bulundu ve düzeltildi: `_analyze_new_closed_trades()`,
82./83./84. turların dört kardeş çağrı noktası için düzelttiği aynı
sim/paper-mode `closed_trades` routing bug'ını taşıyordu — TradeAnalyzer'ın
post-trade root-cause analizi, pattern eşleştirmesi ve adaptif parametre
önerileri, botun fiili varsayılan çalışma modunda (sim/paper) hiçbir zaman
çalışmıyordu. Tam test suite `tests/` **862 passed, 2 skipped**, tüm
dizinler dahil **1694 passed, 4 skipped**. CLAUDE.md'nin risk kuralları
(max %20 pozisyon, günlük -%15 stop, max 5 açık pozisyon, min $5,000 hacim,
min 0.05 edge) kod tarafında değiştirilmedi.

## Sıradaki tur için notlar
- `agents/whale_tracker.py:48`'deki `market` query param sorusu bu turda da
  doğrulanamadı (`data-api.polymarket.com`'a erişim yine EGRESS_BLOCKED).
- `strategies/arbitrage_engine.py:473-485`'in OPT-3 fallback zinciri
  (yukarıda "reddedilen adaylar"da detaylı) — ölü/neredeyse-ölü kod, gerçek
  bir davranış farkı kanıtlanamadı ama okunması karışık; bir sonraki turda
  birim tutarlılığı (yüzde vs kesir) netleştirilip fallback tamamen
  kaldırılabilir veya düzeltilebilir (şu an `binance_feed`'in kendi
  hesabını fiilen hiç değiştirmiyor, sadece kafa karıştırıyor).
- Bu ortamda hâlâ canlı `data/positions.json`/`data/status.json` yok —
  test suite'in ötesinde gerçek pozisyon/sermaye durumuna dayalı bir
  değerlendirme yapılamadı.
