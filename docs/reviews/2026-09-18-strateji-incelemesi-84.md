# Günlük Strateji İncelemesi — 2026-09-18 (84. tur)

## Durum
Oturum başında: 83. incelemenin PR'ı (#145, OPT-7 bounce guard'ın sim/paper
modunda `_side` alanını genişletmesi) doğrulanıp (`853 passed, 2 skipped`)
`main`'e merge edildi (`3355a01`). Bu tur o merge'den sonraki bağımsız
tazesüpürme.

## Bu turda yapılanlar
83. turda ele alınmayan alanlara odaklanıldı: `strategies/kelly_criterion.py`
sizing matematiği, `core/position_manager.py` P&L/capital muhasebesi, risk
kurallarının (max %20 pozisyon, günlük -%15 stop, max 5 açık pozisyon, min
$5,000 hacim, min 0.05 edge) fiilen uygulandığı çağrı noktaları,
`agents/subagents/*.py`, `agents/resilience.py`, `strategies/arbitrage_engine.py`
(1985 satır — yön/edge seçimi, tüm boost gate'leri), sim/live state kaynağı
ayrışması pattern'i (bu repoda tekrar eden bug ailesi) başka yerlerde var mı.

### Bulunan ve düzeltilen hata: `KalshiArbTracker.get_edge_adjustment()` en güncel değil, cache'teki İLK eklenen Kalshi fiyatını kullanıyordu

`agents/kalshi_arb.py`, canlı yola `strategies/arbitrage_engine.py:770`
üzerinden bağlı (`bayesian_prob`'a doğrudan eklenen ±0.04 dış-sinyal
tavanının bir parçası) — SmartMoney/TopTrader/OB_Depth ile birlikte hâlâ
aktif olan 4 dış sinyal kaynağından biri.

`self._cache`, `refresh()` tarafından `(asset, ticker)` başına tek girdi
olarak dolduruluyor; Kalshi'nin `status=open, limit=5` sorgusu bir asset için
aynı anda birden fazla açık kontrat (farklı ticker/vade) döndürebiliyor, bu
girdiler farklı 2 dakikalık refresh döngülerinde cache'e ekleniyor.
`get_edge_adjustment()` yorumunda "en güncel Kalshi fiyatı" yazsa da kod
`matching[0]["yes_price"]` okuyordu — yani dict'in insertion-order'daki
pozisyonel ilk eşleşmesi, `timestamp`'i en yeni olan değil. Sonuç: 300sn
tazelik penceresi içindeki ama daha eski bir ticker'ın fiyatı, gerçekten daha
taze bir fiyatın önüne geçip yanlış (hatta işaret ters dönmüş) bir
`_kalshi_adj` üretip canlı `bayesian_prob`'a besleniyordu.

**Somut senaryo**: cache'te "bitcoin" için önce eklenmiş eski bir girdi
(`yes_price=0.90`) ve sonra eklenmiş taze bir girdi (`yes_price=0.10`) var,
ikisi de 300sn penceresinde. `get_edge_adjustment("bitcoin", poly_yes=0.50)`
taze 0.10 fiyatını yansıtmalı (Kalshi çok ucuz → Poly pahalı görünüyor →
edge azalmalı, negatif adjustment). Kod eski 0.90'ı seçip **+0.02**
döndürüyordu — taze verinin işaret ettiğinin tam tersi.

**Fix** (`agents/kalshi_arb.py::get_edge_adjustment`):
```python
# önce: kalshi_yes = matching[0]["yes_price"]
# sonra:
kalshi_yes = max(matching, key=lambda v: v["timestamp"])["yes_price"]
```
Başka bir mantık değiştirilmedi.

**Test**: `tests/test_kalshi_edge_adjustment_picks_stale_entry.py` — eski
(önce eklenmiş) ve taze (sonra eklenmiş) iki "bitcoin" girdisiyle cache
dolduruluyor, fix öncesi pozitif/yanlış işaretli, fix sonrası
`adjustment == -0.02` (taze fiyattan beklenen) doğrulanıyor. Tam suite:
**854 passed, 2 skipped** (baseline 853/2 + 1 yeni test).

### İncelenip reddedilen adaylar (yeni hata bulunamadı)

`agents/orchestrator.py` (compute_bet_size / apply_risk_size_multiplier /
apply_adaptive_bet_multiplier / MAX_DIRECTIONAL / cycle-risk-budget),
`core/position_manager.py` (P&L/capital, YES/NO değerleme, günlük stop),
`strategies/kelly_criterion.py`, `agents/autonomous_engine.py`,
`strategies/bayesian.py`, `strategies/edge_model.py`,
`strategies/maker_engine.py`+`stoikov.py` (varsayılan kapalı —
`MAKER_ENABLED=false`), `strategies/bond_scanner.py` (varsayılan kapalı —
`BOND_ENABLED=false`), `core/polymarket_client.py`,
`agents/subagents/{coordinator,research_agent,signal_agent_v2,reviewer_agent,
orderflow_agent}.py`, `agents/resilience.py`, `agents/whale_tracker.py`,
`agents/smart_trader_tracker.py`, `agents/top_trader_signal.py`,
`strategies/ml_classifier.py`, `strategies/walk_forward.py`,
`strategies/orderbook_analyzer.py`, `strategies/sum_monitor.py`,
`strategies/spread_model.py`, `strategies/monte_carlo.py`,
`control_plane/{live_gate,entry_window_guard,reentry_guard,expiry_guard,
approval_queue}.py`, `execution_realism/core.py`, `backtesting/engine.py` —
satır satır/çağrı zinciri takip edilerek okundu, yeni kusur bulunamadı.
CLAUDE.md'nin beş sabit risk kuralı her birinin fiili çağrı noktasında
uygulandığı doğrulandı (sadece hesaplanıp kullanılmayan bir gate değil).
Zaten kapalı olduğu bilinen sinyal yolları (SPIKE, MTF, LIQUIDATION,
SPX_CORR, ENHANCED çoklu-borsa/opsiyon/whale/sosyal, SUM_MONITOR) gerçekten
inert (sadece log). `core/approval_queue.py`'nin manuel onay kuyruğu hâlâ
kasıtlı ölü kod (canlı yol direkt execution kullanıyor) — 82. turda
reddedilen aday, mimari karar, tekrar litige edilmedi.

## Sonuç
Bir gerçek hata bulundu ve düzeltildi: Kalshi cross-venue edge
adjustment'ının, dict insertion-order'a göre değil `timestamp`'e göre en
taze cache girdisini seçmesi gerekiyordu — düzeltilmeden önce eski bir
fiyat sessizce daha taze bir fiyatın yerini alıp gerçek trade kararına
yanlış (bazen işaret ters) bir sinyal besleyebiliyordu. Tam test suite
**854 passed, 2 skipped**. CLAUDE.md'nin risk kuralları (max %20 pozisyon,
günlük -%15 stop, max 5 açık pozisyon, min $5,000 hacim, min 0.05 edge) kod
tarafında değiştirilmedi.

## Sıradaki tur için notlar
- 83. turun bıraktığı not hâlâ geçerli ve kapsam dışı: `_cycle()`'ın
  `closed_trades = position_manager.data.get("closed", [])` değişkeni
  (satır ~611) sim/paper modunda hep boş — `AutonomousDecisionEngine.
  _update_performance()`, Dynamic Kelly streak multiplier ve walk-forward
  STOP önerisi sim modunda hiç çalışmıyor. Düzeltme OPT-7 ile aynı desende
  olurdu ama üç ayrı tüketicinin şema farklarının (özellikle `pnl` alanının
  sim kayıtlarında olmaması) ayrı ayrı doğrulanması gerekiyor.
- `agents/whale_tracker.py:48`'deki `market` query param sorusu bu turda da
  doğrulanamadı (`data-api.polymarket.com`'a erişim yine EGRESS_BLOCKED).
