# Günlük Strateji İncelemesi — 2026-09-18 (79. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = bu branch = `349c18c` (#139 konsolidasyon
merge'i sonrası, 78. inceleme + 10 konsolidasyon turu dahil). Açık PR yoktu.
Baseline test: `python3 -m pytest tests/ -q` → 844 passed, 2 skipped
(`crypto_directional/` hariç — sklearn eksik, canlı yolla ilgisiz).

## Bu turda yapılanlar
Bağımsız, taze bir gözden geçirme ajanı ile canlı/shadow yol tekrar,
skeptik bir gözle tarandı; özellikle önceki iki gerçek bulgunun sınıfına
(restart/async/çoklu-cycle sıralama hataları) odaklanıldı:

1. **Sermaye/`daily.pnl` invaryantı** — `PositionManager._close_position()`
   ve `Orchestrator._sync_real_balance()`, canlı yoldaki tek iki sermaye
   mutasyon noktası olarak doğrulandı; ikisi de `daily["pnl"]`'i doğru
   güncelliyor, üçüncü bir nokta yok.
2. **`_sync_open_orders_from_clob()`** (dünkü PR #137 düzeltmesi) — gerçek
   CLOB verisiyle sağlam olduğu yeniden doğrulandı.
3. **`_roll_daily_if_needed()` / `_close_position()` sıralaması** — gece
   yarısı geçişinde kapanan bir pozisyonun doğru günün bucket'ına düştüğü
   doğrulandı.
4. **Üç bağımsız streak-sayma implementasyonu karşılaştırıldı**
   (`kelly_criterion.py::update_streak()`, `autonomous_engine.py::
   _update_performance()`, `orchestrator.py::_update_loss_streak()`):
   gerçek bir tutarsızlık bulundu — Kelly ve AutonomousEngine NEUTRAL
   trade'leri streak'i bozmadan atlıyor, ama Orchestrator'ın kendi
   `_update_loss_streak()`'i NEUTRAL'i streak-bozan olarak sayıyor
   (`elif result in ("WIN", "NEUTRAL"): break`). Sonuna kadar izlendi:
   bu döngünün hesapladığı `self._consecutive_losses` yalnızca bir
   `logger.info(CIRCUIT_BREAKER_INFO...)` satırında okunuyor; bunu
   gerçekten kullanacak olan cooldown bloğu "CIRCUIT_BREAKER devre dışı —
   kullanıcı talebi" notuyla zaten yorum satırı yapılmış. Yani bu
   tutarsızlığın bugün hiçbir trading kararına/sermayeye etkisi yok
   (sadece kozmetik log satırı) — görev tanımının "gerçek, sermayeyi
   etkileyen hata" kriterini karşılamadığı için düzeltme olarak
   sunulmadı.
5. **Pozisyon boyutlandırma zinciri** (`compute_bet_size` →
   `apply_risk_size_multiplier` → `apply_adaptive_bet_multiplier`,
   `KellyCriterion.position_size()`, `AutonomousDecisionEngine.evaluate()`/
   `_assess_risk()`/`get_adaptive_params()` — tüm dallar: CRITICAL/
   DRAWDOWN/LOSS_STREAK/WIN_STREAK/risk-seviyesi/REVIEWER VETO+REDUCE/
   rejim/edge-bonus/düşük-likidite-saatleri) elle yeniden türetildi; hepsi
   önceki review'ların satır içi dokümantasyonuyla (22./39. inceleme)
   tutarlı, yeni sorun yok.
6. **`strategies/bayesian.py`** log-odds/tanh ağırlıklandırma matematiği ve
   `_longshot_bias_correction()` parçalı fonksiyonu elle yeniden
   hesaplandı; `market_price=0.20`'de kozmetik ölçekte (~1 sent) bir
   süreksizlik bulundu ama sermayeyi bozacak boyutta değil.
7. **`agents/latency_arb.py`** — `_find_market_for_spike()`/`_can_trade()`
   (spike-tetikli emir yolu) tam repo grep ile hiçbir yerden çağrılmadığı
   doğrulandı; sadece `get_spike_boost()` kullanılıyor. Canlı etkisi yok,
   `enhanced_signals.py`'nin zaten kabul edilmiş ölü-boost sınıfıyla aynı
   kategori.
8. **`agents/subagents/orderflow_agent.py`** — `compute_bias_score()` ve
   her indikatör (OBI/CVD/walls/VWAP-dev/EMA-cross/HA-streak) elle
   doğrulandı, Binance `isBuyerMaker` → `is_buy` polaritesi dahil — doğru.
9. **`control_plane/expiry_guard.py`, `reentry_guard.py`,
   `entry_window_guard.py`**, `agents/market_index_watcher.py`
   (sadece dashboard görüntüleme) — yeniden tam okundu, yeni sorun yok.
10. `strategies/edge_model.py::execution_cost()` (size-aware slippage) hâlâ
    ölü kod — tüm gerçek çağrı noktaları sabit `total_cost()`'u kullanıyor;
    bu daha az hassas ama tehlikeli yönde yanlış fiyatlamıyor, düzeltme
    olarak sunulmadı.
11. `core/polymarket_client.py`'deki "Default 0.03" yorum satırı ile
    `_price_bump()`'ın gerçek varsayılanı (0.02) arasındaki tutarsızlık
    kontrol edildi — sadece bayat/yanıltıcı yorum, `edge_model.py`'nin
    `SPREAD_COST=0.02`'siyle fonksiyonel olarak uyumlu.

Ayrıca: `data-api.polymarket.com` / `api.binance.com` / `www.bitstamp.net`'e
erişim tekrar denendi — bu oturumun ağ politikası da engelliyor (403 CONNECT
tunnel failed). `agents/whale_tracker.py:48`'deki `market` query param sorusu
hâlâ doğrulanamadı, sıradaki turlara devrediliyor.

## Sonuç
Bugün için yeni bir kod değişikliği gerekmedi. Beş aday
(loss-streak NEUTRAL tutarsızlığı, longshot-bias süreksizliği, latency-arb
ölü spike-trade yolu, edge_model execution_cost ölü kodu, polymarket_client
yorum/kod uyumsuzluğu) derinlemesine incelendi ve hepsi reddedildi — ya
tamamen ölü kod ya da sermayeyi/kararı etkilemeyen kozmetik ölçekte.
Tam test suite baştan sona değişmeden **844 passed, 2 skipped** kaldı
(çalışma sırasında oluşan `data/autonomous_state.json` zaman damgası yan
etkisi `git checkout --` ile geri alındı, önceki turların notuyla tutarlı).
CLAUDE.md'nin risk kuralları (max %20 pozisyon, günlük -%15 stop, max 5
açık pozisyon, min $5,000 hacim, min 0.05 edge) kod tarafında
değiştirilmedi.

## Sıradaki tur için notlar
- `agents/whale_tracker.py:48`'deki `market` query param sorusu hâlâ
  doğrulanamadı — gerçek prod ağ erişimi olan bir oturum
  `data-api.polymarket.com`'a ulaşabiliyorsa kontrol etmeli.
- `Orchestrator._update_loss_streak()`'in NEUTRAL'i streak-bozan sayması
  (Kelly/AutonomousEngine'in aksine) şu an etkisiz (sadece log satırı) ama
  eğer gelecekte CIRCUIT_BREAKER bloğu yeniden aktif edilirse bu
  tutarsızlık gerçek bir davranış farkına dönüşür — o zaman düzeltilmeli.
