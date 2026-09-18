# Günlük Strateji İncelemesi — 2026-09-18 (81. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = bu branch = `9c6ee20` (79. inceleme sonrası).
Bu turun başlangıcında açık bir PR mevcuttu: **#141** ("80. günlük inceleme"),
`claude/brave-faraday-2ck2ea` branch'inde, başka bir oturum tarafından bugün
zaten açılmış, `mergeable_state=clean`, henüz merge edilmemiş. O PR gerçek bir
hata buldu ve düzeltti: `PositionManager._check_order_filled()`,
`clob_status in ("MATCHED","FILLED")` durumunda erken dönüyordu ve daha önceki
bir cycle'da kısmi dolum nedeniyle küçültülmüş `pos["amount"]`'ı, tam dolum
raporlandığında `size_matched`'e göre yeniden uzlaştırmıyordu — bu da
`available_capital()`'ı ve kapanışta gerçekleşen P&L'i bozuyordu. Bu PR'ye
dokunulmadı (başka bir branch/oturuma ait); bu turun amacı onu tekrarlamak
değil, **bağımsız ve taze bir gözle** kapsamadığı alanları taramaktı.
Baseline test: `python3 -m pytest tests/ -q` → 844 passed, 2 skipped
(`crypto_directional/` hariç — sklearn eksik, canlı yolla ilgisiz).

## Bu turda yapılanlar
Ayrı bir inceleme ajanı ile, PR #141'in bulduğu hatayı ve önceki 79 turun
reddettiği tüm adayları (bkz. önceki review dosyaları) tekrar raporlamadan,
canlı/shadow yol tam kapsamlı yeniden tarandı:

- `core/position_manager.py` (tüm fonksiyonlar), `core/polymarket_client.py`
- `strategies/kelly_criterion.py`, `strategies/arbitrage_engine.py` (~1985
  satır: boost yığını, yön seçimi, tüm gate'ler, Kelly/güven/altın-saat
  boyutlandırma), `strategies/bayesian.py`, `edge_model.py`, `spread_model.py`,
  `walk_forward.py`, `ml_classifier.py`
- `agents/orchestrator.py` (~2685 satır: ana döngü, `_cycle`, emir yürütme,
  `_sync_real_balance`, `_sync_open_orders_from_clob`, çözünürlük kontrolleri,
  `_update_loss_streak`, `_limit_coins_per_period`, `_fresh_price_ok`,
  `_pre_filter`, `_record_shadow_decisions`)
- `agents/autonomous_engine.py`, `agents/trade_analyzer.py`
- `agents/subagents/{coordinator,signal_agent_v2,research_agent,
  reviewer_agent,orderflow_agent,base_agent}.py`, `agents/resilience.py`
- `control_plane/{live_gate,expiry_guard,reentry_guard,entry_window_guard}.py`
- `agents/binance_feed.py`, `agents/smart_trader_tracker.py`,
  `agents/top_trader_signal.py`, `agents/kalshi_arb.py`

Üç aday tam izlendi ve reddedildi:

1. **`autonomous_engine.py`'deki yorum satırı** — `evaluate()` içindeki yorum,
   "coordinator.py zaten `signal.size`'ı `suggested_size_pct` ile çarpıyor"
   diyor; bu doğru olsaydı `orchestrator.py`'nin sonradan
   `apply_risk_size_multiplier(bet_size, review_decision.suggested_size_pct)`
   çağırması (~880. satır) çifte uygulama olurdu. `coordinator.py` elle
   izlendi: 52. incelemenin düzeltmesiyle `suggested_size_pct`'i
   `sig.size`'a **gömmediği** doğrulandı (satır 309-324, kendi yorumunda da
   belirtilmiş). Yani indirim tam olarak bir kez, `orchestrator.py`'de
   uygulanıyor — yorum bayat/yanlış ama davranış doğru. Kod hatası değil.
2. **`agents/kalshi_arb.py::KalshiArbTracker.get_edge_adjustment()`** — aynı
   asset-prefix anahtarı altında birden fazla Kalshi kontratı önbelleğe
   alınmışsa, en yakın eşleşen yerine `matching[0]`'ı (dict ekleme sırası)
   seçiyor; ±0.02 boost nadiren yanlış kontrata göre hesaplanabilir. Etki
   sınırlı/küçük ve bu yol yalnızca Kalshi API'si gerçekten veri döndürdüğünde
   (API key yoksa çoğunlukla boş/401) canlı — bu ortamda ağ erişimi
   engellendiği için gerçek/istismar edilebilir olduğu doğrulanamadı.
3. **`ml_classifier.py` eğitim/servis dakika ayrışması** — eğitim, market
   sorusundan planlanan başlangıç dakikasını çıkarıyor (yalnızca 5-dk
   hizalı değerler), canlı çıkarım (`_extract_features_live`) ise gerçek
   duvar-saati dakikasını (sürekli 0-59) kullanıyor. Gerçek bir
   eğitim/servis uyumsuzluğu, ama 11 özellikten biri, döngüsel yumuşatmayla
   ve aynı dosyadaki çok daha büyük, zaten düzeltilmiş
   signal_price/edge eğitim/servis uyumsuzluklarından çok daha küçük
   ölçekte.

Çift sayım, yön/işaret (YES/NO, UP/DOWN, büyük/küçük harf enum
karşılaştırması), cycle'lar arası bayat durum veya uygulanmayan
boost sınıfında yeni bir hata bulunamadı.

## Sonuç
Bugün için yeni bir kod değişikliği gerekmedi. Üç aday
(autonomous_engine.py bayat yorum, kalshi_arb tie-break, ml_classifier
dakika ayrışması) derinlemesine incelendi ve hepsi reddedildi — davranış
doğru, etkisi doğrulanamadı/küçük ölçekli, veya zaten kapsamda olan daha
büyük bir sorunun küçük bir alt kümesi. Tam test suite çalışmadan önce ve
sonra değişmeden **844 passed, 2 skipped** kaldı (çalışma sırasında oluşan
`data/autonomous_state.json` zaman damgası yan etkisi `git checkout --`
ile geri alındı). CLAUDE.md'nin risk kuralları (max %20 pozisyon, günlük
-%15 stop, max 5 açık pozisyon, min $5,000 hacim, min 0.05 edge) kod
tarafında değiştirilmedi. Kod değişikliği içermeyen bu tur, sermayeyi
etkileyen tek gerçek bulgunun (PR #141) ayrı bir oturumda zaten ele
alındığını doğrular.

## Sıradaki tur için notlar
- `agents/whale_tracker.py:48`'deki `market` query param sorusu hâlâ
  doğrulanamadı (78./79. turlardan devrolan, ağ erişimi bu ortamda da
  engelli).
- `agents/kalshi_arb.py::get_edge_adjustment()`'ın `matching[0]` tie-break'i
  — gerçek Kalshi API erişimi olan bir oturum, aynı prefix altında birden
  fazla kontrat gerçekten oluşup oluşmadığını ve en yakın eşleşmenin (örn.
  strike/vade farkına göre) `matching[0]`'dan farklı olup olmadığını
  doğrulamalı.
- `ml_classifier.py` eğitim/servis dakika ayrışması (planlanan 5-dk hizalı
  vs. sürekli duvar-saati dakikası) — küçük ölçekli ama gerçek; ML sinyalinin
  ağırlığı ileride artırılırsa yeniden değerlendirilmeli.
- PR #141 merge edildikten sonra bir sonraki tur, bu PR'nin canlı yolda
  `available_capital()`/kapanış P&L hesaplarını beklendiği gibi düzelttiğini
  doğrulamalı.
