# Günlük Strateji İncelemesi — 2026-09-19 (100. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = `claude/brave-faraday-0lhq5f` = `7aaf0ae`
(#175, 99. tur sonrası — `agents/orchestrator.py`'deki cycle risk-budget
tavanının `available_capital()` üzerinden kilitli sermayeyi iki kez
düşmesi düzeltilmiş). Konteynerde çalışan bir bot instance'ı yok
(`data/status.json`/`control.json`/`positions.json` boş, `.env` yok) —
görev önceki turlarla aynı şekilde bağımsız kod/strateji incelemesi olarak
yürütüldü.

Baseline: sistem python'unda `loguru` dahil bağımlılıklar eksikti
(`pytest` ayrı bir uv-tool venv'inde çalıştığı için `pip install -r
requirements.txt` de görünmüyordu) — `python3 -m pytest tests/ -q` ile
doğru yorumlayıcı kullanılarak çalıştırıldı: **917 passed, 2 skipped**.

## Bu turda yapılan: bağımsız temiz tarama, yeni hata bulunamadı

99 önceki turun bulduğu ve düzelttiği hata sınıflarına özellikle bakılarak
("hesaplanıp loglanan bir gate/cap'in EXECUTE karar yoluna hiç
uygulanmaması" deseni — ML_BOOST, bid_sum/one-sided-ask sanity, pricing
sanity, cycle risk-budget double-count, MakerEngine committed-capital gibi
örnekler) canlı yürütme hattı baştan sona yeniden izlendi:

`strategies/kelly_criterion.py`, `core/position_manager.py`,
`agents/autonomous_engine.py`, `agents/orchestrator.py` (tam direktif
yürütme döngüsü: `_execute_approved_orders`, `_bond_cycle`, `_pre_filter`,
`_limit_coins_per_period`, `_update_loss_streak`, `_sync_real_balance`,
`_check_sim_resolutions`, `_record_shadow_decisions` pricing-sanity
bloğu), `strategies/maker_engine.py`, `strategies/stoikov.py`,
`strategies/bond_scanner.py`, `strategies/edge_model.py`,
`strategies/walk_forward.py`, `strategies/ml_classifier.py`,
`strategies/bayesian.py`, `strategies/arbitrage_engine.py`,
`strategies/quality_filter.py`, `control_plane/{entry_window_guard,
live_gate,reentry_guard,expiry_guard,approval_queue}.py`,
`core/approval_queue.py`, `calibration/decision_policy.py`,
`execution_realism/{core,fill_simulator}.py`,
`monitoring/readiness_checks.py`, `shadow_runner/readiness.py`,
`agents/subagents/{coordinator,reviewer_agent,research_agent,
signal_agent_v2}.py`, `core/polymarket_client.py`,
`agents/{kalshi_arb,top_trader_signal}.py`.

Her dosyada önceki 99 turdan kalan, hangi hatanın nerede düzeltildiğini
anlatan yoğun inline yorumlar var; bu turda her biri tek tek doğrulandı
(hepsi doğru bağlanmış durumda). "Computed-but-never-applied" deseninin
canlı ve düzeltilmemiş başka bir örneği aranmasına rağmen bulunamadı.

### İncelenip reddedilen adaylar (gerçek hata eşiğini geçmedi)

1. **`strategies/edge_model.py`** — büyüklük-farkında `execution_cost()`/
   `_dynamic_slippage()` modeli `strategies/arbitrage_engine.py` tarafından
   hiç çağrılmıyor (hep sabit eski `total_cost()` kullanılıyor). Ancak
   ≤$10 emirler için `_dynamic_slippage()` zaten sabit maliyetle aynı
   değeri döndürüyor ve botun `HARD_MAX_BET=$4` olması nedeniyle bugün
   hiçbir gerçek trade üzerinde sayısal etkisi yok. Etkisiz — atlandı.
2. **`strategies/maker_engine.py::check_paired_profit()`** — garantili-kâr
   tespiti hesaplanıyor ama hiçbir yerde tüketilmiyor. `MAKER_ENABLED=false`
   varsayılan olduğu için MakerEngine zaten pasif — bugün etkisiz.
3. **`control_plane/approval_queue.enqueue()`** hiçbir canlı yoldan
   çağrılmıyor (`_execute_approved_orders()`/dashboard-onay yolu hep boş
   bir kuyruk okuyor). Bu manuel-onay alt sistemi (o yoldaki
   `add_position()` çağrısının `confluence_score`/`risk_flags`/
   `whale_direction`/`regime_direction` metadata'sını hiç geçirmemesi
   dahil) bugünkü tasarımda ölü kod — `orchestrator.py` onay kuyruğunu
   açıkça bypass ediyor ("DOĞRUDAN EMİR VER — onay kuyruğu bypass").
   Gerçek bir boşluk ama yalnızca bu alt sistem yeniden aktifleştirilirse
   ısırır; bugün canlı karara sıfır etkisi var.
4. **`agents/kalshi_arb.py::get_edge_adjustment()`** — Polymarket'in 5dk
   up/down fiyatını, aynı zaman penceresine/strike'a karşılık gelmesi
   garanti olmayan cache'lenmiş bir Kalshi ticker'ıyla kıyaslıyor. Bu bir
   model-geçerliliği endişesi (±0.02 ile sınırlı), kodlama/bağlama hatası
   değil.
5. **`strategies/walk_forward.py::validate()`**'in early-return dalı
   (yetersiz trade sayısı) `self._last_check`'i hiç set etmiyor, ama tek
   tüketici (`orchestrator.py`'deki `wf_mult`) zaten `1.0` varsayılanına
   düşüyor ve kapanan trade sayısı yalnızca büyüyor — davranışsal olarak
   etkisiz.

## Sonuç
Gate'ler/cap'ler/çarpanlar EXECUTE kararına kadar gerçekten izlenerek,
canlı ve sim yollarının her ikisinde de yeni, önceden düzeltilmemiş,
canlı-etkili bir doğruluk hatası bulunamadı. 100 turluk tekrarlayan bir
incelemenin (aynı deseni zaten kapsamlıca tarayan, iki önceki "yeni hata
yok" turu dahil) bu noktada temiz bir tarama vermesi meşru bir sonuç.

Test suite değişmedi: **917 passed, 2 skipped** (kod değişikliği yok, bu
yüzden regresyon riski sıfır). `data/autonomous_state.json`'daki test yan
etkisi commit öncesi `git checkout --` ile geri alındı.

## Sıradaki tur için notlar
- Yukarıdaki 3. madde (`control_plane/approval_queue.py` ölü kodu ve
  `add_position()`'a eksik metadata aktarımı) bir bakıcı kararı gerektirir:
  bu alt sistem hiç yeniden aktifleştirilmeyecekse kaldırılabilir; aktif
  edilecekse metadata boşluğu o zaman gerçek bir hataya dönüşür.
- `agents/whale_tracker.py:48`'deki `market` query param sorusu bu turda da
  doğrulanamadı (ağ erişimi yok) — kalıcı açık madde.
