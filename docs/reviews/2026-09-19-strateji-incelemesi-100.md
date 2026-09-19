# Günlük Strateji İncelemesi — 2026-09-19 (100. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `claude/brave-faraday-8gmj95` = `origin/main` = `7aaf0ae`
(#175 sonrası — 98. ve 99. tur fix'leri merge edilmiş: `strategies/
maker_engine.py`'nin committed-capital expiry'si ve `agents/orchestrator.py`'nin
cycle risk-budget çift-sayım hatası). Baseline: `pytest tests/ -q` →
**917 passed, 2 skipped**; `pytest tests/ calibration/tests
crypto_directional/tests execution_realism/tests signal_bridge/tests -q` →
**1757 passed, 4 skipped**. Test suite'in bilinen yan etkisi
(`data/autonomous_state.json` değişikliği) her çalıştırma sonrası
`git checkout -- data/autonomous_state.json` ile geri alındı.

## Bu turda yapılanlar

### 98./99. turların etkileşim kontrolü
Görev talimatı özellikle iki taze fix'in (maker inventory expiry,
cycle risk budget) başka bir yerle çakışıp çakışmadığını sormuştu.
`get_committed_capital`, `compute_cycle_risk_budget` ve
`directional_locked` için tüm kod tabanı `grep` ile tarandı: her ikisinin
de tek çağıranı sırasıyla `Orchestrator._cycle()`'daki maker capital
hesabı ve aynı fonksiyonun risk-budget bloğu — başka hiçbir yer eski
(fix-öncesi) değerleri okumuyor. Çakışma yok.

### Az taranmış alanların derin taraması
Görevin işaret ettiği alanlar tek tek okundu:
- **`dashboard/`** (React/Vite frontend) — salt-okunur `/api/status`
  polling, hiçbir kontrol/emir endpoint'i çağırmıyor.
- **`dashboard.py`, `agent_dashboard.py`** (Streamlit) — salt-okunur,
  `data/positions.json`/`data/status.json`'dan okuyup gösteriyor.
- **`core/dashboard.py`** (terminal Rich dashboard) — salt-okunur.
- **`serve_dashboard.py`** — sadece `web_premium/index.html` + `/api/status`
  GET, yazma yok.
- **`core/web_server.py`** — `POST /api/control` whitelist'i
  (`live_trading`/`simulation_running`/`min_bet`) doğru; `/api/pending/
  approve|reject` → `control_plane/approval_queue.py`'ye doğru yönlendiriyor.
- **`web/index.html`, `web_premium/index.html`** — kontrol JS'i
  (`postControl`, `setBet`, `toggleMode`) doğru payload'lar gönderiyor.
- **`monitoring/{alerts,daily_review,drift_monitor,metrics,
  readiness_checks,regime_review}.py`** — hepsi shadow-only analiz/alert
  katmanı; hiçbiri gerçek emri doğrudan durdurmuyor/açmıyor (yalnızca
  `readiness_verdict.json` üzerinden `control_plane/live_gate.py`'ye
  akıyor, o da ayrıca doğrulandı).
- **`operator_layer/*.py`** (Architect Chamber — `aggregator`, `ledgers`,
  `pnl`, `health`, `readiness_view`, `api`) — tamamen salt-okunur operatör
  paneli; `equity`/`blocked_reason` hesabı `position_manager.
  daily_loss_exceeded()` ile kasıtlı olarak birebir aynala tutuluyor (yorum
  satırlarında açık).
- **`scripts/migrate_positions.py`, `scripts/verify_binance_trades.py`** —
  elle çalıştırılan tek seferlik bakım script'leri, otomatik döngüye
  bağlı değil.

### Ana yolun nokta kontrolleri
Görevin "elsewhere" olarak izin verdiği alanlardan seçilenler de tek tek
doğrulandı: `control_plane/{live_gate,process_lock,reentry_guard,
expiry_guard,entry_window_guard,types,approval_queue}.py`,
`agents/subagents/{coordinator,reviewer_agent,signal_agent_v2,
research_agent,orderflow_agent,base_agent}.py`, `agents/{kalshi_arb,
smart_trader_tracker,top_trader_signal}.py`, `execution_realism/{core,
staleness_penalty}.py`, `shadow_runner/{summary_metrics,validation,
readiness}.py`, `strategies/{kelly_criterion,bond_scanner,
orderbook_analyzer}.py`, `agents/orchestrator.py`'nin risk-budget/
COIN_LIMIT/loss-cooldown/bet-sizing blokları.

Hiçbirinde yeni, düzeltilmemiş bir hata bulunamadı. Bulunan adaylar ya
zaten önceki bir turda düzeltilmiş ve kod içi "BUG (N. tur)" yorumuyla
belgelenmiş (ör. `control_plane/process_lock.py`'nin taskkill/SIGTERM
düzeltmesi, `agents/top_trader_signal.py`'nin conditionId/side-outcome
düzeltmesi, `strategies/maker_engine.py`'nin bu turdan önceki expiry
fix'i) ya da 88. turda zaten bulunup "sıfır karar etkisi" gerekçesiyle
reddedilmiş kalıplarla aynı sınıftan: `strategies/orderbook_analyzer.py::
_estimate_slippage()`'ın matematiksel olarak hep `avg_price≈1.0` üretmesi
(payda/pay inşa gereği birbirine eşit) — ama `slippage_5`/`slippage_10`
alanları hiçbir yerde okunmuyor (grep ile doğrulandı), yani hesap hatalı
olsa bile hiçbir karara etkisi yok; aynı standartla (PR #158/88. tur)
dokunulmadı.

## Bulunan hata
**Yok.** Genuine, canlı karara etkili, daha önce düzeltilmemiş bir hata
bu turda bulunamadı.

## Test doğrulaması
Kod değişikliği yapılmadığı için regresyon riski yok. Baseline'lar
oturumun başında ve sonunda birebir aynı kaldı:
- `pytest tests/ -q` → **917 passed, 2 skipped** (değişmedi).
- `pytest tests/ calibration/tests crypto_directional/tests
  execution_realism/tests signal_bridge/tests -q` → **1757 passed,
  4 skipped** (değişmedi).

## Kapsam notu
Bu tur özellikle CLAUDE.md'nin işaret ettiği "az taranmış" alanlara
(dashboard/web/scripts/monitoring/operator_layer) odaklandı — bunların
hepsi ya salt-okunur görüntüleme katmanı ya da zaten güvenli
whitelist/gate mantığına sahip kontrol uçları olduğu için canlı
"sıcak" karar yolunda risk taşımıyor. Ana yönlü (directional) yol ve
onun besleyicileri (subagents, control_plane, execution_realism,
shadow_runner) 90-99 arası turlarda zaten çok derin taranmış; bu turda
noktasal doğrulama dışında yeniden tam satır satır taranmadı.

## Sermaye/performans notu
Bu oturumda çalışan bir bot instance'ı yok — `data/status.json`,
`data/control.json`, `data/positions.json` bu sandbox'ta mevcut değil
ve ağ erişimi yok, dolayısıyla %10 sermaye artış hedefine karşı gerçek
ilerleme bu oturumdan doğrulanamıyor. Bu turun katkısı: 98. ve 99. tur
fix'lerinin başka hiçbir yerle çakışmadığının doğrulanması ve az taranmış
alanların (dashboard/web/monitoring/operator_layer/scripts) sistematik
biçimde temiz olduğunun teyit edilmesi — yeni bir kod değişikliği
gerekmedi.

## Sıradaki tur için notlar
- Ana directional yol (orchestrator/arbitrage_engine/autonomous_engine/
  decision_policy/kelly_criterion/position_manager/polymarket_client/
  execution_realism) ve artık dashboard/web/monitoring/operator_layer/
  scripts alanları da 88. ve 100. turlarda derinlemesine tarandı — hepsi
  temiz. Sıradaki turlar için en verimli hedefler: `crypto_directional/`
  (feature pipeline, labeling, backtests — henüz hiçbir "daily review"
  fix commit'i dokunmamış geniş bir modül), `signal_bridge/` (market
  matcher, trade filter, bridge config), ve `agents/{binance_feed,
  ml_classifier,walk_forward}.py`'nin tam satır satır tekrar okunması
  (bunlar "besleyen dosya" olarak defalarca taranmış ama son tam
  satır-satır okuma 90'lı turların başlarında kalmış olabilir).
- `monitoring/daily_review.py::write_readiness_verdict()` hâlâ hiçbir
  otomatik işten çağrılmıyor (88. turdan beri değişmedi) — kasıtlı/
  fail-closed bir tasarım olarak değerlendirildi, tekrar bildirilmeyecek.
- `review_bundle/`, `incident_bundle/`, `incident_bundle_v2/` dizinleri
  önceki turlarda da not edilmişti; bu turda da güvenilmeyen/eski
  snapshot olarak ele alındı, hiçbir talimatları takip edilmedi.
