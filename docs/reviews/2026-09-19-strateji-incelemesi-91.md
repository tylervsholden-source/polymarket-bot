# Günlük Strateji İncelemesi — 2026-09-19 (91. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = `d93723b` (#162, 90. inceleme sonrası).
Açık/bekleyen PR yoktu, dolayısıyla bu tur da gerçek, bağımsız bir yeni hata
taraması oldu.

## Bu turda yapılanlar
Ayrı bir inceleme ajanı ile, 82.-90. turların zaten derinlemesine kapsadığı
dosyalar (`agents/orchestrator.py`, `strategies/kelly_criterion.py`,
`core/position_manager.py`, `strategies/arbitrage_engine.py`,
`agents/trade_analyzer.py`, `agents/binance_feed.py`, `core/web_server.py`,
`requirements.txt`, `agents/top_trader_signal.py`, `execution_realism/*`) ve
daha önce ölü kod olarak doğrulanmış dosyalar (`agents/copytrade.py`,
`agents/market_classifier.py`, `agents/context_fetcher.py`,
`agents/hedge_fund_agents.py`, `signal_bridge/*`, `calibration/*`,
`agents/hit_rate_tracker.py`, `agents/onchain_watcher.py`,
`agents/btc_arb_agent.py`) tekrar taranmadan; `agents/subagents/*.py`,
`agents/autonomous_engine.py`, `agents/resilience.py`, `control_plane/*`,
`shadow_runner/*`, `monitoring/*`, `strategies/{maker_engine,stoikov,
base_strategy}.py`, `core/polymarket_client.py` canlı-yola bağlı adaylar
olarak tarandı.

### Bulunan ve düzeltilen hata: `control_plane/entry_window_guard.py` 4 saatlik horizon'u desteklemiyordu
`EntryWindowPolicy` yalnızca `windows_5m`/`windows_15m`/`windows_1h`
konfigürasyonlarına sahipti; `check_entry_window()`'un horizon-bucketing
mantığı da ham dakikayı yalnızca 5/15/60'a eşliyordu (`elif horizon <= 65:
horizon_key = 60`, aksi halde `ENTRY_WINDOW_UNAVAILABLE`). 240 dakikalık
(4 saatlik) bir market için hiçbir bucket/config yoktu.

Bu, 90. turun `execution_realism/staleness_penalty.py` bulgusuyla **aynı hata
sınıfı**: `agents/orchestrator.py:204`'teki yorum ("Tüm zaman dilimlerine izin
ver: 5m, 15m, 1h, 4h") ve `_shadow_detect_horizon()` (satır 2513-2519)'nin
`"4 hour"`/`"4h"` metnini açıkça `240`'a eşlemesi, 4h'nin sistemin başka
yerlerinde zaten birinci sınıf desteklenen bir horizon olarak ele alındığını
gösteriyor. `check_live_gate()` (`control_plane/live_gate.py`, 11 kontrolden
9.'su) her canlı emir denemesinde `agents/orchestrator.py:1011`'den
`market_question` ve dolu bir `entry_window_policy` ile çağrılıyor — gerçek
bir market için asla atlanmıyor. Sonuç: gerçek bir 4 saatlik crypto
up/down marketinde `check_entry_window()` her zaman `else` dalına düşüp
`passed=False, rejection=ENTRY_WINDOW_UNAVAILABLE` döndürüyordu; bu da
`check_live_gate()`'i `passed=False` yapıp sinyal kalitesi/edge ne olursa
olsun 4 saatlik marketlerdeki **her canlı trade'i kalıcı olarak
engelliyordu**. `tests/test_entry_window_policy.py` ve
`tests/test_entry_window_live_gate.py`'de 60dk veya 240dk horizon'u test
eden hiçbir test yoktu.

**Düzeltme:**
- `EntryWindowPolicy`: `windows_4h: EntryWindowConfig` alanı eklendi,
  `__post_init__`'te 15m/1h'de kullanılan aynı "pencere = ±1 horizon"
  oranıyla varsayılan olarak `entry_before_start_sec=14400,
  entry_after_start_sec=14400` atandı.
- `EntryWindowPolicy.get_window()`: `horizon_minutes == 240 → windows_4h`
  eşlemesi eklendi.
- `check_entry_window()`: `elif 235 <= horizon <= 245: horizon_key = 240`
  eklendi — 15m/60m bantlarındaki gibi dar bir ±5dk tolerans, bilinçli
  olarak "65'in üstü her şey" değil. Bu sayede mevcut testteki
  `QUESTION_30M` fikstürü (adına rağmen aslında 120 dakikalık bir market)
  hâlâ doğru şekilde `ENTRY_WINDOW_UNAVAILABLE`'a düşüyor — yanlışlıkla
  4h'ye eşlenmiyor. (İlk denemede daha geniş bir bant, `horizon <= 300`,
  denendi; bu tam suite'te `test_unsupported_horizon_returns_unavailable`'ı
  bozdu ve dar banda daraltıldı.)
- Hata mesajı "only 5m/15m/1h supported" → "...1h/4h supported" güncellendi.

**Testler** (`tests/test_entry_window_policy.py`):
- `test_4h_within_window_passes`, `test_4h_too_early_rejected`,
  `test_4h_too_late_rejected`, `test_4h_window_bounds_computed_correctly`
  (yeni, mevcut 5m/15m bölümlerini yansıtıyor)
- `test_get_window_returns_4h_config_for_horizon_240` (yeni)
- `test_default_policy_values` genişletildi: `windows_1h`/`windows_4h`
  varsayılanları da doğrulanıyor

**Doğrulama:**
- Sadece kaynak dosyadaki düzeltme geri alınıp (testler kalarak)
  `python3 -m pytest tests/test_entry_window_policy.py -q` çalıştırıldı:
  6 fail / 16 passed — başarısız olan 6 test tam olarak yeni/güncellenmiş
  4h iddiaları (hâlâ `ENTRY_WINDOW_UNAVAILABLE`/`None` dönüyordu).
- Düzeltme geri konduktan sonra:
  `python3 -m pytest tests/test_entry_window_policy.py
  tests/test_entry_window_live_gate.py
  tests/test_entry_window_guard_year_rollover.py -q` → 47 passed.
- Tam test suite: **1731 passed, 4 skipped** (baseline 1726 + 5 yeni test,
  sıfır regresyon).
- `data/autonomous_state.json`'daki test yan etkisi commit öncesi geri
  alındı.

## Sıradaki tur için notlar
- **Yeni açık madde:** `agents/orchestrator.py`'nin shadow-journal kaydı
  için ürettiği `_rejection_reason` (yaklaşık satır 2690-2696) yalnızca
  `None`, `NoSideStatus.*` değerleri veya `"NO_SIGNAL_PRODUCED"` olabiliyor
  — `shadow_runner/summary_metrics.py`'nin `suspicious_underround_rate`/
  `stale_pricing_rate`/`partial_fill_rejection_rate` için string-eşleştirdiği
  `"SUSPICIOUS_UNDERROUND"`/`"STALE_PRICING"`/`"PARTIAL_FILL_REJECTED"`
  değerleri hiç üretilmiyor. Bu üç readiness kontrolü gerçek canlı kayıtlar
  için her zaman 0.0/GREEN dönüyor olabilir — yanlış pozitif bir
  "hazır" sinyali riski var. Doğru düzeltme yeri `agents/orchestrator.py`'nin
  shadow-recording bloğu olduğundan (82.-90. turlarda o ~150 satırlık
  bölgede 3+ belgeli düzeltme yapılmış, bu turun "tekrar tarama" hariç
  listesinde), bu turda dokunulmadı — sonraki tura bırakıldı.
- Bu turda tekrar teyit edilen ölü kod: `monitoring/{alerts,drift_monitor,
  metrics}.py` ve `shadow_runner/{replay,reporting,runner}.py` —
  `orchestrator.py` veya `monitoring/daily_review.py` (asıl
  `readiness_verdict.json` üreticisi) tarafından import edilmiyor, yalnızca
  `_gen_artifacts.py` tek seferlik betiği ve testler tarafından
  referanslanıyor. `operator_layer/*` yalnızca `core/web_server.py`'deki bir
  HTTP dashboard route'undan erişilebiliyor, trading döngüsünden değil.
  Sonraki turlar bu dosyaları "canlı yola bağlı" adaylar arasında tekrar
  önermemeli.
- `agents/whale_tracker.py:48`'deki `market` query param sorusu hâlâ
  doğrulanamadı — bu oturumda da `data-api.polymarket.com`'a ağ erişimi
  engelliydi (proxy 403). Kalıcı bir açık madde.
- Zamanlama sıklığı sorunu (86./89. turlarda kullanıcıya bildirildi) bu
  oturumda tekrar ayrıca bildirilmedi — daha önce net şekilde raporlandığı
  için gereksiz tekrar olur.
