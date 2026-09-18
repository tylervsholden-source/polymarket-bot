# Günlük Strateji İncelemesi — 2026-09-18 (konsolidasyon turu #9, 78. oturum)

## Hedef
Canlı/shadow trading yolunda tek, somut, gerçek bir hata bulup regresyon
testiyle kanıtlayarak düzeltmek (78. günlük inceleme).

## Durum
Oturum başında branch `claude/brave-faraday-yw3vfn` = `origin/main` (94f9468,
#135 merge). Aynı anda iki başka oturum bugünün ("78. inceleme") gerçek
hatalarını zaten bulup PR açmış durumda:
- PR #136 — `strategies/arbitrage_engine.py`: LEAD_LAG cross-exchange boost,
  "tüm external boost'lar devre dışı" reset noktasından ÖNCE
  `cross_exchange_boost` parametresiyle doğrudan `BayesianEstimator.estimate()`'e
  besleniyordu, reset'i sessizce atlıyordu.
- PR #137 — `agents/orchestrator.py::_sync_open_orders_from_clob()`: restart
  sonrası CLOB'dan geri yüklenen pozisyonların `outcome` alanı gerçek emir
  yönünden bağımsız olarak hep `"YES"` hardcode ediliyordu.

Görev tanımı bu ikisini tekrar bulmamamı/düzeltmememi açıkça istiyor. Bu turda
farklı, bağımsız bir hata arandı.

## Bu turda yapılanlar

Canlı yoldaki neredeyse tüm modüller satır satır tekrar tarandı — 77 önceki
günlük inceleme + bugünün 2 PR'ı tarafından zaten kapsanmayan yeni bir hata
aranarak:

1. **`strategies/arbitrage_engine.py`** (tam dosya, ~1970 satır): Bayesian
   girişi, external boost reset mekanizması (SPIKE/MTF/LEAD_LAG/FUNDING/
   LS_RATIO/LIQUIDATION/SPX_CORR/ENHANCED — hepsi ayrı ayrı kontrol edildi,
   `cross_exchange_boost` dışında hepsi doğru şekilde devre dışı), NO/YES
   fiyat kaynağı seçimi, direction diagnostics, candlestick pattern
   entegrasyonu, ML/Kelly/Monte Carlo gate'leri, golden-hour boost + hard cap
   sırası.
2. **`strategies/bayesian.py`, `edge_model.py`, `kelly_criterion.py`,
   `spread_model.py`, `stoikov.py`, `monte_carlo.py`, `ml_classifier.py`,
   `sum_monitor.py`, `orderbook_analyzer.py`, `walk_forward.py`**: formüller
   ve NEUTRAL-trade dışlama mantığı tek tek doğrulandı. Tek somut bulgu
   `orderbook_analyzer.py::_estimate_slippage()`'daki bir birim hatası
   (dolar-nominal toplamı fiyat gibi kullanılıyor, `avg_price` her zaman 1.0'a
   yakınsıyor) — ama `slippage_5`/`slippage_10` alanları hiçbir yerde
   (loglanmıyor, shadow journal'a yazılmıyor, hiçbir gate'te okunmuyor)
   tüketilmiyor; canlı bir trading etkisi yok, "ölü" hesaplama. Görev
   tanımının istediği "gerçek trading-impact senaryosu" kriterini
   karşılamadığı için düzeltme olarak sunulmadı.
3. **`core/position_manager.py`, `core/polymarket_client.py`**: tam dosya —
   NO/YES current_price fallback zinciri, `_close_position`/
   `_close_position_neutral`, `_check_order_filled` partial-fill mantığı,
   GTC price bump, rounding config, tümü önceki review'ların yorumlarıyla
   birebir tutarlı ve doğru.
4. **`agents/orchestrator.py`** (tam dosya, ~2700 satır): `_cycle()` ana
   döngüsü baştan sona (watchdog, loss-streak, walk-forward, coordinator
   çağrısı, COIN_LIMIT, TOPLAM RİSK LİMİTİ, autonomous engine entegrasyonu,
   bet-size zinciri — compute_bet_size → REDUCE → AUTONOMOUS → walk-forward
   → adaptive multiplier → HARD_MAX_BET → capital yeterlilik → mum-içi
   zamanlama → LiveGate → FRESH_PRICE_ABORT → place_order), `_pre_filter`,
   `_limit_coins_per_period`, `_fresh_price_ok`, `_execute_approved_orders`,
   `_sync_real_balance`, `_is_live_trading`/`_readiness_clears_live`. Ayrı
   bir aday incelendi ve reddedildi (bkz. aşağıda).
5. **`agents/subagents/*`** (coordinator, research_agent, signal_agent_v2,
   reviewer_agent, orderflow_agent, base_agent): confluence/risk-flag
   hesabı, whale/regime/smart-money/orderflow enrichment, Claude review
   parse/fallback mantığı, timeout/hata yönetimi — hepsi tutarlı.
6. **`agents/whale_tracker.py`, `smart_trader_tracker.py`,
   `top_trader_signal.py`, `kalshi_arb.py`, `latency_arb.py`,
   `binance_feed.py`** (indikatör fonksiyonları + `get_signal`/
   `get_market_regime`/`refresh`/`_fetch_symbol`): side/outcome eşleştirmesi,
   OBI/CVD yön işaretleri, fiyat geçmişi penceresi, cache/refresh mantığı —
   hata bulunamadı.
7. **`control_plane/*`** (entry_window_guard, reentry_guard, expiry_guard,
   live_gate, process_lock, approval_queue), **`execution_realism/*`**
   (core, slippage_model, staleness_penalty, fill_simulator),
   **`shadow_runner/*`** (journal, summary_metrics, readiness),
   **`monitoring/*`** (readiness_checks, regime_review, metrics,
   drift_monitor, daily_review): 11 noktalı live-gate zinciri ve pilot
   readiness verdict hesabı uçtan uca izlendi, fail-safe davranış (eksik
   veri → CONDITIONAL_REVIEW/NO_GO, asla sessizce GO) doğrulandı.
8. **`strategies/maker_engine.py`**: ciddi bir aday bulundu ve derinlemesine
   incelendi — `_cancel_all_standing()`'de `cancel_order()` False dönüp
   (örn. geçici ağ hatası) emrin CLOB'da hâlâ LIVE olduğu durumda, kod emri
   `self._standing`'den siliyor (böylece `get_committed_capital()` artık bu
   emrin notional'ını saymıyor ve bir sonraki cycle aynı capital'i tekrar
   commit edebilir). Ancak `tests/test_maker_cancel_swallows_fill.py::
   test_cancel_all_standing_drops_order_that_is_still_live` bu davranışı
   AÇIKÇA beklenen/istenen davranış olarak sabitliyor
   (`assert "order-1" not in engine._standing`) — yani bu önceki bir
   review'da bilinçli tasarım kararı olarak zaten değerlendirilmiş ve kabul
   edilmiş. Buradaki bir "düzeltme" mevcut, kasıtlı olarak yazılmış bir testi
   kıracaktı; bu yüzden hata olarak sunulmadı. `MAKER_ENABLED` varsayılan
   `false` (KAPALI) olduğu için bugün canlı etkisi de yok.
9. **`agents/latency_arb.py`**: `_find_market_for_spike()`/`_can_trade()`
   fonksiyonları tanımlı ama hiçbir yerden çağrılmıyor (dosyanın kendi
   docstring'inde anlatılan "spike → anında emir" akışı hiç bağlanmamış,
   sadece `get_spike_boost()` kullanılıyor). Bu, orchestrator'ın zaten
   bildiği ve kabul ettiği bir mimari boşluk sınıfına giriyor (bkz.
   `-44.md`/`-47.md`/`-consolidation-6.md`'deki MakerEngine envanteri /
   approval_queue benzeri boşluklar) — minimal bir "bug fix" ile
   kapatılamayacak kadar büyük bir özellik eksikliği, günlük review
   kapsamına girmiyor.
10. **`core/dashboard.py`, `core/web_server.py`, `core/status_writer.py`**:
    hiçbiri gerçek trade kararlarını etkilemiyor (sadece görüntüleme/kontrol
    API'si); kontrol whitelisti ve `min_bet` akışı doğru.

## Sonuç
Bugün için yeni bir kod değişikliği gerekmedi. İki güçlü aday (orderbook
slippage birim hatası, maker cancel-fail order kaybı) bulundu, derinlemesine
incelendi ve ikisi de reddedildi: ilki hiçbir yerde tüketilmeyen ölü kod,
ikincisi önceki bir review'ın bilinçli tasarım kararı olarak zaten test ile
sabitlenmiş. Bugünün gerçek düzeltmeleri PR #136 ve #137'de zaten açık,
mükerrer çalışma yapılmadı. Tam test suite: **841 passed, 2 skipped** —
regresyon yok. CLAUDE.md'nin risk kuralları (max %20 pozisyon, günlük -%15
stop, max 5 açık pozisyon, min $5,000 hacim, min edge eşiği) kod tarafında
değiştirilmedi.
