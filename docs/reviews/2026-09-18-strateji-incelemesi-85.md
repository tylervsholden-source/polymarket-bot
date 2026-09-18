# Günlük Strateji İncelemesi — 2026-09-18 (85. tur)

## Durum
Oturum başında `origin/main` = bu branch = `8ee14c5` (#148, 84. inceleme
sonrası — `KalshiArbTracker.get_edge_adjustment()`'ın cache'te insertion-order
yerine `timestamp`'e göre en taze girdiyi seçmesi düzeltilmiş, baseline
**854 passed, 2 skipped**). Açık PR yoktu. Bu ortamda baseline:
`python3 -m pytest -q` → **1692 passed, 4 skipped** (sayı farkı 84. turdan
bu yana eklenen bağımsız/paralel testlerden, bu turun konusuyla ilgisiz).

## Bu turda yapılanlar
Talimatta işaretlenen, önceki 84 turda dosya adı geçmeyen alanlara
odaklanıldı: `strategies/arbitrage_engine.py` (1985 satır, yön/edge seçim
mantığı bağımsız olarak yeniden doğrulandı), `agents/enhanced_signals.py`,
`agents/hedge_fund_agents.py`, `agents/btc_arb_agent.py`,
`agents/latency_arb.py`, `agents/market_classifier.py`,
`agents/market_index_watcher.py`, `agents/onchain_watcher.py`,
`agents/context_fetcher.py`, `agents/copytrade.py`,
`agents/hit_rate_tracker.py`, `agents/binance_feed.py`, `agents/ws_feed.py`,
`agents/torture_agent.py`, `core/status_writer.py`,
`strategies/moonshot.py`, `strategies/quality_filter.py`. Özellikle 84.
turun "Sıradaki tur için notlar" bölümünde açıkça kapsam dışı bırakılan
`_cycle()`'ın `closed_trades = position_manager.data.get("closed", [])`
kalıbının başka kardeş çağrı noktalarında hâlâ var olup olmadığı — 82./83./84.
turlarda tekrar eden bug ailesi — `agents/orchestrator.py` genelinde grep
ile yeniden tarandı.

### Bulunan ve düzeltilen hata: `Orchestrator._analyze_new_closed_trades()` sim/paper modunda hiç çalışmıyordu — TradeAnalyzer (post-trade analiz) fiilen kalıcı olarak devre dışıydı

`agents/orchestrator.py::_analyze_new_closed_trades()`, `run()` döngüsünün
her turunda `_cycle()`'dan hemen sonra koşulsuz çağrılıyor (satır ~440) ve
CLAUDE.md'nin mimarisinde ayrıca adı geçen "`TradeAnalyzer.analyze_trade()`
← YENİ: Post-trade analiz — Root cause analysis, sinyal doğruluk kontrolü,
pattern eşleştirme, adaptif parametre önerileri" bileşenini besleyen tek
çağrı noktası. Fonksiyon, 83./84. turlarda aynı kök nedenden düzeltilen
kardeş kod bloklarının aksine, hâlâ doğrudan

```python
closed_trades = self.position_manager.data.get("closed", [])
new_count = len(closed_trades) - self._last_analyzed_count
```

okuyordu — `_current_closed_trades()` helper'ını (82./83. turlarda
`_update_loss_streak()` için, 84. turda `kelly.update_streak()` /
`walk_forward.validate()` / `autonomous_engine.evaluate()` /
`get_adaptive_params()` için yönlendirilmişti) hiç kullanmıyordu. Bu ortamda
ve CLAUDE.md/docs/architecture.md'nin belgelediği fiili varsayılan çalışma
biçiminde (`LIVE_TRADING_ENABLED` unset/false → `_is_live_trading()==False`)
`_cycle()`'ın emir-verme dalı `position_manager`'a hiç dokunmuyor — kapanan
trade sonuçları `self._sim_results`'a yazılıyor
(`_check_sim_resolutions()`). Dolayısıyla `position_manager.data["closed"]`
sim/paper modda kalıcı olarak `[]`, `_last_analyzed_count` sıfırdan
başlıyor, ve `new_count = 0 - 0 = 0` her döngüde erken dönüşe (`if new_count
<= 0: return`) yol açıyordu — kaç sim trade kapanırsa kapansın.

82. turun "İncelenip reddedilen adaylar" bölümünde bu fonksiyon bir kez
incelenmişti, ama sadece `_finalize_cycle()`'ın `update_positions()`'ı iki
kez çağırmasının `_analyze_new_closed_trades()`'in trade'leri kaçırıp
kaçırmadığı (sıralama) sorusu için — kapanan trade listesinin KAYNAĞININ
(sim vs. live) doğru seçilip seçilmediği o turda hiç sorulmamıştı, bu yüzden
bu tur yeni bir bulgu.

**Somut senaryo**: Bot varsayılan (sim/paper) modda saatlerce çalışıyor,
`self._sim_results` içinde onlarca WIN/LOSS trade birikiyor (loglarda
`SIM SONUC` satırları görülüyor). Ama `_analyze_new_closed_trades()` hiçbir
zaman `self.trade_analyzer.analyze_trade()`'i çağırmıyor — `data/
trade_analyses.json` ve `data/trade_patterns.json` hiç güncellenmiyor,
`KNOWN_PATTERNS` (BOUNCE_AFTER_STREAK, NO_TRAP, REGIME_OVEREXTEND_LOSS vb.)
hiçbir zaman eşleşmiyor, `get_recommendations()` her zaman boş liste
döndürüyor. Sonuç: CLAUDE.md'nin mimari olarak belgelediği post-trade
öğrenme/analiz katmanı, botun fiilen çalıştığı modda sessizce hiç
tetiklenmiyordu.

**Fix** (`agents/orchestrator.py::_analyze_new_closed_trades`):
```python
# önce:
closed_trades = self.position_manager.data.get("closed", [])
# sonra:
closed_trades = self._current_closed_trades()
```
Başka bir mantık değiştirilmedi; `_current_closed_trades()` zaten var olan
(84. turda eklenen), canlı/sim modu ayrıştıran tek doğru kaynak.

**Test**: `tests/test_trade_analyzer_sim_mode_closed_trades_source.py` (3
test) — sim modda `self._sim_results`'a iki trade eklenip
`_analyze_new_closed_trades()` çağrıldığında, fix öncesi
`trade_analyzer.analyze_trade()`'in hiç çağrılmadığı (`AssertionError:
assert [] == ['sim-1', 'sim-2']`), fix sonrası ikisinin de sırayla analiz
edildiği doğrulanıyor; ayrıca canlı mod davranışının (gerçek `closed`
okunur, `_sim_results` yoksayılır) ve "yeni trade yok" no-op durumunun
regresyonsuz kaldığı ayrı testlerle ekli. Tam suite: **1695 passed, 4
skipped** (baseline 1692/4 + 3 yeni test).

### İncelenip reddedilen adaylar (yeni hata bulunamadı)

`strategies/arbitrage_engine.py` — yön seçimi (`direction = "YES" if
bayesian_prob > market_price + costs else "NO"` ve edge işareti), 6 modelin
(Bayesian/Edge/Spread/Stoikov/Kelly/MC) sıralaması, dış sinyal tavanları
(SmartMoney/TopTrader/OB_Depth/Kalshi ±0.04 cap toplamı) uçtan uca izlendi —
84. turda Kalshi tarafı düzeltilmişti, bu tur geri kalan 3 dış sinyal
kaynağının toplanma/clamp mantığı ve `bet_size`'a giden zincir bağımsız
olarak yeniden doğrulandı, yeni kusur yok. `agents/enhanced_signals.py`
(SPIKE/MTF/LIQUIDATION/SPX_CORR sinyalleri — zaten bilinen şekilde inert,
sadece log, `arb_engine`'e beslenmiyor), `agents/hedge_fund_agents.py`
(hiçbir yerden import edilmiyor — orchestrator/main.py/subagents zincirinde
yok, ölü kod), `agents/btc_arb_agent.py` (`_is_live_trading()` gate'i doğru
uygulanmış, sim/live ayrımı zaten `_current_closed_trades()` benzeri kendi
iç state'ini kullanıyor, karışma yok), `agents/latency_arb.py` (spike-path
zaten 82. turda "kullanılmayan alt-sistem" olarak işaretlenmişti, tekrar
doğrulandı — `start()` çağrılıyor ama emir vermiyor, sadece izliyor),
`agents/market_classifier.py`, `agents/market_index_watcher.py`,
`agents/onchain_watcher.py`, `agents/context_fetcher.py`,
`agents/copytrade.py`, `agents/hit_rate_tracker.py`,
`agents/binance_feed.py`, `agents/ws_feed.py` (cache/staleness mantığı
84. turdaki Kalshi bug'ıyla aynı desen aranarak kontrol edildi — hepsi
tek-girdi cache veya doğru `timestamp`-bazlı seçim kullanıyor), `agents/
torture_agent.py` (yalnızca `anthropic` kuruluysa aktif, bu ortamda
devre dışı, canlı trade kararına dokunmuyor), `core/status_writer.py`
(salt yazma, dashboard'a gidiyor, geri besleme yok), `strategies/moonshot.py`,
`strategies/quality_filter.py` — satır satır okundu, gate'lerin gerçekten
çağrıldığı doğrulandı, yeni kusur bulunamadı.

## Sonuç
Bir gerçek hata bulundu ve düzeltildi:
`Orchestrator._analyze_new_closed_trades()`, kapanan trade listesini
`_current_closed_trades()` yerine doğrudan `position_manager.data["closed"]`
okuyordu — botun fiili varsayılan (sim/paper) çalışma modunda bu liste hep
boş kaldığı için TradeAnalyzer'ın post-trade root-cause analizi, pattern
eşleştirmesi ve adaptif öneri üretimi kalıcı olarak hiç tetiklenmiyordu. Bu,
82./83./84. turlarda aynı kök nedenden (sim/live state kaynağı ayrışması)
düzeltilen kardeş kod bloklarının kapsamadığı, o turlarda "reddedilen aday"
olarak sadece sıralama açısından incelenmiş ama kaynak seçimi açısından hiç
sorgulanmamış bir çağrı noktasıydı. Tam test suite: **1695 passed, 4
skipped**. CLAUDE.md'nin risk kuralları (max %20 pozisyon, günlük -%15
stop, max 5 açık pozisyon, min $5,000 hacim, min 0.05 edge) kod tarafında
değiştirilmedi.

## Sıradaki tur için notlar
- `_current_closed_trades()`'i tüketen tüm noktalar artık tek bir listede:
  `_update_loss_streak()` (82./83.), `kelly.update_streak()` /
  `walk_forward.validate()` / `autonomous_engine.evaluate()` /
  `get_adaptive_params()` (84.), `_analyze_new_closed_trades()` (85., bu
  tur). Kalan doğrudan `position_manager.data.get("closed", ...)` kullanım
  noktaları (`agents/orchestrator.py:654`, `~1524`, `~2334`) incelendi:
  `~654`/`~1524` sadece dashboard/status_writer'a `closed=` alanı olarak
  yazılıyor (salt görüntüleme, karar zincirine geri beslenmiyor), `~2334`
  `closed_order_ids` yalnızca restart sonrası CLOB'dan çekilen açık emirlerin
  zaten kapanmış olup olmadığını kontrol ediyor ve bu kontrol yapısal olarak
  sadece canlı modda anlamlı (sim modda CLOB'a hiç emir gitmiyor) — üçü de
  düzeltme gerektirmiyor, ama bir sonraki tur yine de bağımsız doğrulamalı.
- `agents/whale_tracker.py:48`'deki `market` query param sorusu (`data-api.
  polymarket.com`'a erişim `EGRESS_BLOCKED`) bu turda da doğrulanamadı,
  hâlâ açık.
