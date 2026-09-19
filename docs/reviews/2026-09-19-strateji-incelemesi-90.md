# Günlük Strateji İncelemesi — 2026-09-19 (90. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = `bf6213b` (#161, 89. inceleme sonrası
konsolidasyon). Bu turda açık/bekleyen PR yoktu (89. turun bulduğu 40 saatlik
birikme tamamen temizlenmişti), dolayısıyla bu tur gerçek, bağımsız bir yeni
hata taraması oldu.

## Bu turda yapılanlar
Ayrı bir inceleme ajanı ile, 82.-89. turların zaten derinlemesine kapsadığı
dosyaları (`agents/orchestrator.py`, `strategies/kelly_criterion.py`,
`core/position_manager.py`, `strategies/arbitrage_engine.py`,
`agents/trade_analyzer.py`, `agents/binance_feed.py`,
`core/web_server.py`, `requirements.txt`, `agents/top_trader_signal.py`)
tekrar taramadan, canlı yola gerçekten bağlı ama az/hiç incelenmemiş bir
modül arandı. `agents/copytrade.py`, `signal_bridge/*`, `calibration/*`
gibi adaylar `orchestrator.py`/`coordinator.py`'den import zinciriyle
ulaşılamadığı (ölü kod) doğrulanarak elendi.

### Bulunan ve düzeltilen hata: `execution_realism/staleness_penalty.py` 1h/4h horizon'u desteklemiyordu
`execution_realism.core.compute_executable_ev()` — her yürütülen sinyal için
`orchestrator.py:2673`'te çağrılıyor ve `execution_adjusted_ev` üretiyor; bu
değer `shadow_runner/summary_metrics.py`'nin `mean_ev_haircut_pct`'ine, oradan
`shadow_runner/readiness.py` → `monitoring/readiness_checks.py::check_ev_haircut_pct()`'e
akıyor — `control_plane/live_gate.py`'nin gerçek canlı-para trading'i
gate'lediği `TINY_PILOT_CANDIDATE` doğrulamalarından biri.

`compute_staleness_penalty()`'nin `_THRESHOLDS` sözlüğü sadece `horizon_minutes`
5 ve 15 için tanımlıydı. Ama `orchestrator.py:204`'teki yorum ("Tüm zaman
dilimlerine izin ver: 5m, 15m, 1h, 4h") ve `control_plane/entry_window_guard.py`'nin
60dk'yı zaten birinci sınıf desteklenen horizon olarak ele alması, 1h/4h
genişlemesinin aktif olarak hedeflendiğini gösteriyor. 1h veya 4h horizon'lu
her sinyal, fiyat anlık görüntüsü ne kadar taze olursa olsun, "desteklenmeyen
horizon" dalına düşüp sabit `zone=EXPIRED, should_reject=True, penalty=0.030`
(en kötü durum) alıyordu — bu da haircut metriğini sessizce bozup canlıya
geçiş kararını etkileyen readiness sinyalini çarpıtıyordu. Mevcut bir test
(`test_unsupported_horizon_penalty`) bunu `horizon_minutes=60` için beklenen
davranış olarak kodluyordu bile — koddaki ve testteki aynı eski varsayım.

**Düzeltme:**
- `execution_realism/types.py`: `STALENESS_60M` ve `STALENESS_240M` eşik
  tanımları eklendi (5m→15m ölçeklendirme trendinin devamı: daha uzun
  horizon = daha geniş fresh/aging/stale pencereleri, daha düşük ceza).
- `execution_realism/staleness_penalty.py`: `_THRESHOLDS`'a ikisi kaydedildi.
- `execution_realism/tests/test_staleness_penalty.py`: eski
  `horizon_minutes=60 → unsupported` testi kaldırıldı, yerine
  `TestHorizon60m`/`TestHorizon240m` (fresh/aging/stale/expired) eklendi;
  gerçek "desteklenmeyen horizon" testi `horizon_minutes=45`'e taşındı.

**Doğrulama:**
- Sadece kaynak dosyalar geri alınıp yeni testler eski koda karşı çalıştırıldı:
  8 testten 6'sı fail etti (expired case'ler zaten her iki yolda da EXPIRED
  döndüğü için geçiyordu) — testlerin gerçek hatayı yakaladığı doğrulandı.
  Not: 60/240dk'nın bugünkü canlı `_pre_filter()` yolunda hâlâ `horizon not
  in (5, 15)` filtresiyle elendiği doğru (bkz. `orchestrator.py:1609`), ama
  bu modül readiness/haircut metriğini besliyor ve 1h/4h genişlemesi kodun
  başka yerlerinde (entry_window_guard, max_hours=24 varsayılanı, yukarıdaki
  yorum) zaten hedeflenmiş durumda — düzeltme olmadan o genişleme anında
  metrik sessizce bozulurdu.
- `python -m pytest execution_realism/ -q` → 129 passed.
- Tam test suite: **1726 passed, 4 skipped** (baseline 1718 + 8 yeni test,
  sıfır regresyon).
- `data/autonomous_state.json`'daki test yan etkisi commit öncesi geri alındı.

## Sıradaki tur için notlar
- `agents/whale_tracker.py:48`'deki `market` query param sorusu hâlâ
  doğrulanamadı — `data-api.polymarket.com`'a bu oturumda da (ve önceki
  birçok oturumda) ağ erişimi engelliydi (proxy 403). `pip install` gibi
  paket kayıt defterlerine erişim var ama rastgele dış host'lara yok.
  Kalıcı bir açık madde; canlı/paper log'larında whale sinyalinin gerçekten
  veri döndürüp döndürmediği kontrol edilerek dolaylı doğrulanabilir.
- Bu turda tekrar teyit edilen ölü kod listesi (orchestrator/coordinator'dan
  erişilemiyor): `agents/copytrade.py`, `agents/market_classifier.py`,
  `agents/context_fetcher.py`, `agents/hedge_fund_agents.py`,
  `signal_bridge/*`, `calibration/*`, `agents/hit_rate_tracker.py`,
  `agents/onchain_watcher.py`, `agents/btc_arb_agent.py`. Sonraki turlar bu
  dosyaları "canlı yola bağlı" adaylar arasında tekrar önermemeli.
- Zamanlama sıklığı sorunu (86./89. turlarda kullanıcıya bildirildi) bu
  oturumda tekrar ayrıca bildirilmedi — daha önce net şekilde raporlandığı
  için gereksiz tekrar olur; sorun hâlâ hesap düzeyinde, bu oturumun
  erişemediği bir zamanlayıcı ayarı.
