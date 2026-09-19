# Günlük Strateji İncelemesi — 2026-09-19 (88. tur)

## Durum
Oturum başında `origin/main` = bu branch = `f69f318` (#155, 86. inceleme
sonrası — `CandlestickAnalyzer.analyze()`'daki THREE_WHITE_SOLDIERS/
THREE_BLACK_CROWS `range3` hatası düzeltilmiş). Baseline:
`python3 -m pytest -q` → **1699 passed, 4 skipped** (86. turun bıraktığı
sayıyla birebir aynı). `pytest`/`scikit-learn` sistem python'unda kurulu
değildi, `pip install -r requirements.txt` + `pip install scikit-learn`
ile kurulup baseline doğrulandı (`data/autonomous_state.json`'daki test
yan etkisi `git checkout --` ile geri alındı).

Bugün aynı "87. tur" etiketiyle üç bağımsız oturum zaten PR açmıştı
(hâlâ merge edilmemiş, `main`'e girmemiş):
- **PR #156** — `agents/binance_feed.py::get_recent_change()` REALTIME LAG
  PREVENTION gate'inin cycle-kadanslı `_price_history`'den okuduğu için
  pratikte hep `0.0` dönüp hiç tetiklenmediği bug'ı, `agents/ws_feed.py`'ye
  per-trade WS geçmişi eklenerek düzeltiliyor.
- **PR #157** — `_execute_approved_orders()`'ın `position_manager.add_position()`'a
  `edge=` geçirmediği (dashboard-onay yolunda `TradeAnalyzer` her zaman
  edge=0 görüyordu) bug'ı düzeltiliyor.
- **PR #158** — çok geniş bir "hata bulunamadı" taraması
  (`core/position_manager.py`, `agents/autonomous_engine.py`,
  `agents/subagents/{coordinator,reviewer_agent,research_agent,
  orderflow_agent,signal_agent_v2}.py`, whale/smart-trader/top-trader/
  kalshi/latency arb dosyaları, `strategies/{kelly_criterion,edge_model,
  orderbook_analyzer,sum_monitor,monte_carlo,stoikov,spread_model,
  ml_classifier,walk_forward,bayesian}.py`, `core/candlestick_analyzer.py`,
  `control_plane/{entry_window_guard,expiry_guard,reentry_guard,
  live_gate}.py`, `core/polymarket_client.py`, `shadow_runner/
  {summary_metrics,validation,readiness}.py`, `monitoring/
  {readiness_checks,regime_review}.py`, `agents/orchestrator.py`,
  `strategies/arbitrage_engine.py`).

Üç PR'ın diff'i okunup hangi dosya/fonksiyonlara dokunduğu doğrulandı; bu
turda o dosyalara **hiç dokunulmadı**, tamamen farklı bir dosya kümesi
tarandı (aşağıda). Numaralandırma çakışması (üç PR de "87. tur" diyor)
nedeniyle bu tur **88.** olarak numaralandırıldı.

## Bu turda yapılanlar

### Az/hiç incelenmemiş, gerçekten canlı yola bağlı dosya taraması

`docs/reviews/*.md` içinde dosya adı geçiş sayımı + PR #156/#157/#158'in
dokunduğu/incelediği dosyalar elenerek, kalan adaylardan **gerçekten canlı
karara bağlı olanlar** (dead-code adaylar `grep` ile doğrulanıp elendi)
derinlemesine okundu:

1. **`control_plane/process_lock.py`** (3 review mention) — tek-instance
   kilidi; `_bond_cycle()`/`_execute_approved_orders()` içinde
   `self._process_lock.is_mine()` ile doğrudan emir yürütmeyi gate'liyor.
   `acquire()`'daki eski-process SIGTERM/SIGKILL + `sys.exit(1)` güvencesi
   (önceki bir turun düzeltmesi, INC-2026-03-15-001) satır satır yeniden
   doğrulandı — doğru.
2. **`agents/market_index_watcher.py`** (4 mention) — `orchestrator.py`'de
   `market_watcher.run()` başlatılıyor ve `_sw.update_indices(...)` ile
   sadece dashboard'a besleniyor; `get_context()` metodunun tek olası
   tüketicisi `agents/signal_agent.py` (bağımsız doğrulandı: `orchestrator.py`/
   `main.py`'den hiç import edilmiyor, sadece `scan_markets.py` kullanıyor —
   ölü yol). Karar zincirine bağlı değil.
3. **`agents/enhanced_signals.py`** (multi-exchange orderflow, Deribit
   options, on-chain whale, social sentiment — `binance_feed.py:95`'te
   `self.enhanced` olarak her cycle `refresh()` ediliyor, gerçekten canlı).
   Derinlemesine okundu; iki gözlem yapıldı, ikisi de "gerçek hata" eşiğini
   geçmedi (bkz. reddedilen adaylar).
4. **`core/web_server.py`** (5 mention) — `POST /api/control` whitelist'i
   (`live_trading`/`simulation_running`/`min_bet`) ve `docs/architecture.md`'nin
   tarif ettiği `min_bet` akışı doğrulandı, sorun yok.
5. **`control_plane/types.py`** (3 mention) — `ApprovalState` state-machine
   geçiş tablosu, `LiveGateResult.blockers` doğru.
6. **`strategies/bond_scanner.py`** — PR #158'in listesinde YOK (tek
   incelenmemiş canlı `strategies/*.py` dosyası — `orchestrator.py`'de
   `_bond_cycle()` her 5. cycle'da gerçek `place_passive_order()` çağırıyor).
   Derinlemesine okundu (bkz. reddedilen adaylar).
7. **`monitoring/daily_review.py`** — `write_readiness_verdict()`,
   `data/readiness_verdict.json`'ı yazıyor; bu dosya
   `agents/orchestrator.py::_readiness_clears_live()`'ın canlı işlemi
   açıp açmayacağına karar verdiği gerçek gate. PR #158'in listesi
   `monitoring/{readiness_checks,regime_review}.py`'yi kapsıyordu ama
   `daily_review.py`'yi kapsamıyordu — bağımsız olarak incelendi.
8. **`shadow_runner/journal.py`** — `JournalWriter`/`JournalReader`'ın
   `_to_dict()`/`_from_dict()` serileştirme simetrisi alan alan
   karşılaştırıldı.
9. **`strategies/base_strategy.py`** (0 mention, hiç incelenmemiş) — trivial
   ABC, sadece `kelly_criterion.py` inherit ediyor, hata yüzeyi yok.
10. **`main.py`** — `--status`/`--backtest`/`--dashboard`/canlı mod dalları.

Ayrıca CLAUDE.md'nin "değiştirme" dediği 5 risk kuralının kod tarafında
hâlâ doğru uygulandığı bağımsız olarak yeniden noktasal doğrulandı:
`max_open_positions` varsayılanı 5 (`agents/orchestrator.py:200`),
`min_market_volume` artık hardcoded `0` değil (satır 660-664'teki önceki
tur yorumu + kod eşleşiyor), `daily_loss_exceeded()`'in
`day_start_capital`/`loss_pct` hesabı doğru.

### İncelenip reddedilen adaylar (yeni hata bulunamadı — gerekçeli)

1. **`agents/enhanced_signals.py::get_options_signal()`/`get_social_signal()`
   — "max_pain" ve "fear_greed" anahtarları hiç üretilmiyor.**
   `fetch_options_data()` sadece `pcr_oi`/`pcr_volume`/`avg_iv`/`call_oi`/
   `put_oi` hesaplıyor (max_pain hiç hesaplanmıyor), `fetch_social_sentiment()`
   sadece `social_score` hesaplıyor (fear_greed hiç hesaplanmıyor). Buna
   rağmen `agents/subagents/research_agent.py:312,317`
   (`max_pain=opts.get("max_pain")`, `fear_greed_index=social.get("fear_greed")`)
   bu anahtarları okumaya çalışıyor — her zaman `None`. Zincir izlendi:
   `EnrichedSignal.fear_greed`/`max_pain` alanlarına kadar taşınıyor
   (`signal_agent_v2.py:149`, `coordinator.py:413`), ama `_compute_confluence()`,
   `_detect_risk_flags()` ve `to_review_summary()`'nin HİÇBİRİ bu iki alanı
   okumuyor (grep ile doğrulandı — sadece atama var, hiç kullanım yok).
   Gerçek fear/greed verisi zaten ayrı ve doğru çalışan bir yoldan geliyor
   (`agents/binance_feed.py::_fetch_fear_greed()`/`get_fear_greed()`,
   kompozite %5 ağırlıkla giriyor, satır 1213) — bu yüzden botun gerçek bir
   fear/greed sinyalinden mahrum kalması söz konusu değil, sadece
   `enhanced_signals` tarafındaki paralel/yedek alan hiç dolmuyor. Sıfır
   karar etkisi (PR #158'in `orderbook_analyzer._estimate_slippage()`
   için kullandığı "hiçbir yerde okunmuyor" standardıyla aynı gerekçe) —
   dokunulmadı.
2. **`agents/enhanced_signals.py`'nin `multi_exchange_imbalance`/
   `put_call_ratio` çıktıları hiçbir tüketici tarafından okunmuyor.**
   `research_agent.py::get_market_context()` bu iki alanı `ctx` dict'ine
   koyuyor (satır 104, 107), ama `signal_agent_v2.py`'nin enrichment
   adımı bu iki `ctx` anahtarını hiç okumuyor (sadece whale/smart-money/
   regime/fear_greed/social/rsi/orderflow okunuyor — grep ile tüm
   kullanım noktaları listelenip doğrulandı). Ayrıca `strategies/
   arbitrage_engine.py:930-990`'da bu sinyallerin (multi-exchange,
   options, whale, social) hepsinin boost uygulaması önceki bir turda
   **bilinçli olarak devre dışı bırakılmış** (`# FIX: ... boost DISABLED`
   yorumları, sadece log basıyorlar, `_enhanced_total_boost`'a hiç
   eklenmiyor). Yani `EnhancedSignals` şu an kasıtlı olarak sadece
   gözlem/log amaçlı — canlı yön/boyut kararına hiçbir etkisi yok. Kod
   hatası değil, kasıtlı devre dışı bırakma — dokunulmadı.
3. **`strategies/bond_scanner.py` + `core/position_manager.py`'nin havuz
   sistemi — `DEFAULT_POOLS = {"maker": 0.00, "bond": 0.00,
   "directional": 1.00}`.** İlk bakışta "bond scanner hiç para
   harcayamıyor" gibi göründü, ama kod ve yorum (`# DIRECTIONAL ONLY
   mode`) açıkça bunun `BOND_CAPITAL_PCT`/`MAKER_CAPITAL_PCT` env
   değişkenleriyle açılan kasıtlı bir feature flag olduğunu gösteriyor —
   varsayılan olarak bond/maker havuzları kapalı. `_bond_cycle()`'ın
   kendisi (MAX_POSITIONS, pool_available, process_lock, daily
   stop-loss, hesap-geneli pozisyon limiti kontrolleri) doğru sırada ve
   doğru yönde kontrol ediyor; NO-side sentetik fiyat tahmini
   (`no_price_est = 1.0 - yes_price + 0.02`) sınır durumlarda (best_ask
   eksik/sıfır) elle izlenip doğru şekilde filtrelendiği doğrulandı. Bug
   değil.
4. **`monitoring/daily_review.py::write_readiness_verdict()`/
   `generate_daily_review()` hiçbir canlı kod yolundan çağrılmıyor**
   (sadece dosyanın kendi `__main__` bloğu ve `operator_layer/ledgers.py`'nin
   bir docstring referansı) — yani `data/readiness_verdict.json` botun
   kendisi tarafından hiç üretilmiyor, repo'da da mevcut değil. Ama
   `agents/orchestrator.py::_readiness_clears_live()` dosya yokken
   **fail-closed** davranıyor ("canlı işlem engellendi" logu, satır 2235)
   — yani eksik veri canlı trading'i AÇMAK yerine KAPALI tutuyor. Bu
   ortamın zaten paper/sim modda çalıştığı (görev bağlamında belirtilen)
   gerçeğiyle tutarlı, kasıtlı bir güvenlik varsayılanı — kod hatası
   değil.

## Sonuç
Bugün için yeni bir kod değişikliği gerekmedi. On dosyalık bağımsız bir
tarama (`control_plane/process_lock.py`, `agents/market_index_watcher.py`,
`agents/enhanced_signals.py`, `core/web_server.py`, `control_plane/types.py`,
`strategies/bond_scanner.py`, `strategies/base_strategy.py`,
`monitoring/daily_review.py`, `shadow_runner/journal.py`, `main.py`) yapıldı
— bunların hiçbiri bugünkü üç PR'ın (#156/#157/#158) dokunduğu veya
listelediği dosyalarla çakışmıyor. Dört aday derinlemesine izlendi
(`enhanced_signals`'ın max_pain/fear_greed alanlarının hiç dolmaması,
multi_exchange_imbalance/put_call_ratio'nun hiç okunmaması, bond havuzunun
varsayılan %0 olması, readiness_verdict.json'ın hiç üretilmemesi) ve
dördü de reddedildi — ya sıfır karar etkisi (dead output/wiring, PR #158
ile aynı standart) ya da kasıtlı/güvenli tasarım (devre dışı boost,
fail-closed gate). Tam test suite değişmeden **1699 passed, 4 skipped**
kaldı. CLAUDE.md'nin risk kuralları (max %20 pozisyon, günlük -%15 stop,
max 5 açık pozisyon, min $5,000 hacim, min 0.05 edge) kod tarafında
noktasal olarak yeniden doğrulandı, değiştirilmedi.

## Sıradaki tur için notlar
- Bugün açık üç PR (#156, #157, #158) merge edilmeyi bekliyor — bir
  sonraki tur önce bunların durumunu kontrol etmeli (merge olduysa
  baseline test sayısı 1699'dan 1706'ya çıkacak, PR #156'nın 7 yeni
  testiyle).
- `agents/enhanced_signals.py`'nin dört sinyal kaynağının (multi-exchange,
  options, whale, social) TÜMÜ `strategies/arbitrage_engine.py:930-990`'da
  kasıtlı olarak devre dışı — sadece log basıyorlar. Bu, sistemin
  ciddi miktarda ağ çağrısı (ccxt/Deribit/blockchain.info/CryptoCompare)
  yapıp sonucunu hiç kullanmadığı anlamına geliyor — kod hatası değil ama
  performans/maliyet açısından temizlenmeye değer bir gözlem (canlı
  karara etkisi olmadığı için bu turda dokunulmadı).
- `agents/whale_tracker.py:48`'deki `market` query param sorusu hâlâ ağ
  erişimiyle doğrulanamadı (`EGRESS_BLOCKED`, bu turda tekrar denenmedi —
  86./87. turların bulgusu hâlâ geçerli: WhaleTracker canlı yola bağlı,
  `docs/architecture.md`'nin "DEVRE DISI" iddiası güncel değil).
- `monitoring/daily_review.py`'nin `write_readiness_verdict()`'i hiçbir
  otomatik/zamanlanmış işten çağrılmıyor — bu muhtemelen kasıtlı (harici
  bir operatör süreci canlıya geçmeden önce elle çalıştırmalı), ama
  bir sonraki tur bunun gerçekten kasıtlı mı yoksa eksik bir
  entegrasyon mu olduğunu (ör. `main.py`'ye `--daily-review` gibi bir
  bayrak eksik mi) sorgulayabilir — şu an botun paper/sim modda
  olması nedeniyle aciliyeti yok.
- `review_bundle/`, `incident_bundle/`, `incident_bundle_v2/` dizinleri
  86./87. turlarda not edilmişti, bu turda da güvenilmeyen veri olarak
  ele alındı, hiçbir talimatı takip edilmedi — repo hijyeni sorusu hâlâ
  açık.
