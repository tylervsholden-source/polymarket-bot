# Günlük Strateji İncelemesi — 2026-09-19 (98. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = `a79a44a` (#173 sonrası, 97. tur konsolidasyonu
merge edilmiş). Açık PR yok.

## Bu turda yapılanlar

### Baseline doğrulama
- `pip install -r requirements.txt` ile bağımlılıklar kuruldu.
- `pytest tests/ calibration/tests crypto_directional/tests execution_realism/tests
  signal_bridge/tests -q` → **1749 passed, 4 skipped** (97. turdaki son
  doğrulamayla birebir aynı — regresyon yok).
- Not: test suite çalıştırıldığında `data/autonomous_state.json` yan etkisi
  oluşuyor (bilinen davranış, 92-97. turlarda da görülmüş); her çalıştırma
  sonrası `git checkout -- data/autonomous_state.json` ile geri alındı.

### Derin kod incelemesi (subagent ile)
Canlı karar/execution zincirindeki dosyalar tek tek tarandı:
`agents/orchestrator.py`, `agents/autonomous_engine.py`,
`strategies/arbitrage_engine.py`, `calibration/decision_policy.py` +
`edge_estimator.py`, `control_plane/live_gate.py` + `reentry_guard.py` +
`expiry_guard.py` + `entry_window_guard.py` + `approval_queue.py`,
`core/polymarket_client.py`, `core/position_manager.py`,
`execution_realism/*`, `strategies/kelly_criterion.py`,
`agents/subagents/*.py`, ve besleyen dosyalar (`binance_feed.py`,
`bayesian.py`, `ml_classifier.py`, `edge_model.py`, `monte_carlo.py`,
`spread_model.py`, `sum_monitor.py`, `stoikov.py`, `latency_arb.py`,
`resilience.py`, `walk_forward.py`).

Ana yönlü (directional) yolda yeni, temiz bir hata bulunamadı — önceki 97
tur tarafından zaten çok kapsamlı taranmış; bulunan adaylar ya daha önce
incelenip reddedilmiş (`_apply_consensus_filter()` ölü kod — 87b/94. turlarda
reddedildi; DEFENSIVE/SURVIVAL `min_edge_yes`'in statik tabanı hiç aşmaması
— `tests/test_adaptive_params_wiring.py` ile kasıtlı davranış olarak
kilitlenmiş) ya da hiç çağrılmayan ölü kod (`LatencyArbEngine._can_trade`/
`_find_market_for_spike`, `Stoikov.adjusted_entry_price`) olarak çıktı.

### Bulunan ve düzeltilen hata: `strategies/maker_engine.py` — committed capital hiç expire olmuyordu
`MakerEngine.on_fill()` bir standing order dolduğunda `MarketInventory.
yes_cost`/`no_cost`'u artırıyor, ama hiçbir yerde bu değerleri azaltmıyor
veya `self._inventory`'den kaydı silmiyordu. Bot'un quote verdiği her
BTC/ETH/SOL/... up-or-down marketi 5-15 dakikalık, benzersiz bir
`market_id` (`_select_markets()` sadece 20 dakika içinde kapanan
marketleri değerlendiriyor) — yani bir dolumdan yarım saat sonra gerçek
USDC zaten Polymarket tarafından ödenmiş/kaybedilmiş oluyor
(`Orchestrator._sync_real_balance()` bunu doğrudan cüzdan bakiyesinden
okuyor), ama `self._inventory[market_id]` kaydı sonsuza kadar yaşıyordu.

**Sonuç**: `get_committed_capital()` (56. turda eklenen
`maker_capital = pool_available("maker") - get_committed_capital()`
hesabının girdisi) her resolve olan market ile birlikte tekdüze büyüyor,
bir süre sonra `maker_capital` her döngüde $0'a clamp'leniyor ve
`MAKER_ENABLED=true` olduğunda MakerEngine gerçek cüzdanda bol miktarda
boş USDC olsa bile sessizce hiçbir quote veremez hale geliyordu.

**Düzeltme**: `MarketInventory`'ye oluşturulma anını tutan `first_seen`
alanı eklendi; `get_committed_capital()`'ın başında çağrılan
`_expire_resolved_inventory()`, `INVENTORY_MAX_AGE_SEC` (30dk — 20dk quote
penceresi + resolve gecikmesi payı) kadar eski kayıtları siliyor.

**Test**: `tests/test_maker_committed_capital_expiry.py` (3 test):
- `test_committed_capital_excludes_long_resolved_market_inventory` —
  düzeltme öncesi FAIL (7.2 döndürüyordu, 0.0 bekleniyordu), sonrası PASS.
- `test_committed_capital_still_includes_recent_unresolved_inventory` —
  regresyon karşıtı: taze/gerçekten açık kayıt hâlâ sayılmalı.
- `test_on_fill_stamps_first_seen_at_creation_time` — düzeltme öncesi FAIL
  (`AttributeError`), sonrası PASS.

**Doğrulama**:
- Düzeltme öncesi: yeni testlerden 2/3 FAIL (hatayı doğruladı).
- Düzeltme sonrası: yeni 3 test PASS; ilgili 17 maker testi
  (`test_maker_cancel_swallows_fill.py`, `test_maker_capital_committed.py`,
  `test_maker_cycle_daily_stop_and_lock_gate.py`,
  `test_maker_inventory_cap.py`) regresyon olmadan geçiyor.
- Tam suite: **1749 → 1752 passed, 4 skipped** (+3 yeni test, 0 regresyon —
  tam beklenen aritmetik).

### Kapsam notu
Bu hata, CLAUDE.md'nin dokümante ettiği mimarinin bir parçası olan ama
varsayılan olarak **kapalı** (`MAKER_ENABLED=false`) market-making
alt sisteminde yaşıyor — canlı "sıcak" yol yalnızca directional. Yine de
gerçek, canlı, daha önce düzeltilmemiş bir muhasebe hatası ve kendi mevcut
test paketi (`tests/test_maker_capital_committed.py`) committed-capital
hesabının doğru olduğunu varsayıyor.

## Sermaye/performans notu
Bu oturumda çalışan bir bot instance'ı yok (`data/status.json`/
`control.json`/`positions.json` mevcut değil, ağ erişimi yok) — %10
hedefine karşı gerçek ilerleme bu oturumdan doğrulanamıyor. Bu turun katkısı,
`MAKER_ENABLED=true` yapıldığında gerçek sermayenin bir kısmının hiçbir
gerekçe olmadan kalıcı olarak "kilitli" görünüp kullanılamaz hale gelmesini
önlemek.

## Sıradaki tur için notlar
- `agents/whale_tracker.py:48`'deki `market` query param sorusu hâlâ
  doğrulanamadı (ağ erişimi yok). Kalıcı açık madde, tekrar bildirilmeyecek.
- Ana directional yol (orchestrator/arbitrage_engine/autonomous_engine/
  decision_policy/kelly_criterion/position_manager/polymarket_client/
  execution_realism) bu turda tekrar çok derin tarandı ve temiz çıktı —
  önümüzdeki turlarda zaman kazanmak için önce daha az taranmış alanlara
  (maker_engine, dashboard/web, scripts/) bakmak daha verimli olabilir.
