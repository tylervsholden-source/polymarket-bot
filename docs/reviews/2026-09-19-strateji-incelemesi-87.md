# Günlük Strateji İncelemesi — 2026-09-19 (87. tur)

## Durum
Oturum başında `origin/main` = bu branch = `f69f318` (#155, 86. inceleme
sonrası — `CandlestickAnalyzer.analyze()`'ın THREE_WHITE_SOLDIERS/
THREE_BLACK_CROWS için `range3` hesaplamadığı bug'ı düzeltilmiş). Açık PR
yoktu. Baseline: `python3 -m pytest -q` → **1699 passed, 4 skipped**
(86. turun bıraktığı sayıyla birebir aynı). Bu ortamda yine `pytest`
sistem python'unda kurulu değildi, `pip install -r requirements.txt` +
`pip install scikit-learn` ile kurulup baseline doğrulandı.

Ağ erişimi bu turda da denendi (`$HTTPS_PROXY/__agentproxy/status`):
`api.binance.com`/`fapi.binance.com` dahil dış hostlara `403 CONNECT
tunnel failed` — 86. turun bıraktığı `EGRESS_BLOCKED` durumu değişmedi,
`agents/whale_tracker.py:48`'deki `market` param sorusu bu turda da ağ
üzerinden doğrulanamadı (madde açık kalıyor, bkz. Sıradaki tur notları).

## Bu turda yapılanlar

### Az incelenmiş, canlı yola bağlı dosya taraması

`docs/reviews/*.md` içinde dosya adı geçiş sayımına göre en az değinilen
dosyalar arasından **gerçekten canlı yola bağlı** olanlar seçildi (dead-code
adaylar elendi):
- `agents/signal_agent.py`/`agents/hit_rate_tracker.py` — sadece
  `scan_markets.py` (bağımsız script, orchestrator'dan hiç çağrılmıyor,
  `tests/test_min_volume_gate.py` docstring'i de bunu doğruluyor) ve
  `review_bundle/`/`incident_bundle*/` (güvenilmeyen, izole dizinler —
  86. turda not edilmişti) tarafından import ediliyor. `docs/architecture.md`'nin
  "DEVRE DISI" listesiyle uyumlu, doğrulandı, dokunulmadı.
- `agents/onchain_watcher.py`/`agents/btc_arb_agent.py` — `agents/orchestrator.py`
  ve `main.py`'de hiç referans yok, hiç instantiate edilmiyor. Dead code,
  atlandı.
- `agents/ws_feed.py` (3 review'de bahsedilmiş) — **canlı**:
  `agents/binance_feed.py:679-684`, `refresh()` içinde her zaman
  `RealtimeFeed()` başlatıp `self._ws_feed`'e atıyor (`ENABLE_RESEARCH_AGENT`
  varsayılan `true` olduğu sürece her cycle'da çağrılan yol). Derinlemesine
  okundu.

### Bulunan ve düzeltilen hata: `BinanceFeed.get_recent_change()` — REALTIME LAG PREVENTION gate'i, veri kaynağının döngü kadansı yüzünden pratikte hep `0.0` dönüyor, gate hiç tetiklenmiyordu

`strategies/arbitrage_engine.py:1433-1454` içindeki "REALTIME LAG
PREVENTION" bloğu, yorumuna göre şunu yapmalı: *"Bayesian geçmiş mumları
kullanır → trend tersine döndüğünde gecikir. Son 60 saniyedeki gerçek
fiyat hareketini kontrol et. Sinyal yönüyle çelişiyorsa trade'i blokla."*
Bunu `self.binance_feed.get_recent_change(sym, seconds=60)` ile yapıyor,
sonucu `_RT_THRESHOLD = 0.15` ile karşılaştırıp `_yes_viable`/`_no_viable`'ı
`False` yapıyor.

`get_recent_change()` (`agents/binance_feed.py`, düzeltme öncesi) veriyi
`self._price_history`'den okuyordu — ama bu dict **sadece**
`BinanceFeed.refresh()` → `_fetch_symbol()` içinde, cycle başına **bir
kez** doldurulur (satır 741, öncesi: sadece bu tek yazma noktası). `refresh()`
ise `agents/subagents/research_agent.py:226` üzerinden `AgentCoordinator.run_cycle()`
içinde çağrılıyor, o da `agents/orchestrator.py:718`'de **cycle başına bir
kez** — ve cycle aralığı `CYCLE_INTERVAL_SECONDS` (varsayılan 60, adaptif
60-120sn, bkz. CLAUDE.md "adaptif dongu: 60-120sn"). Yani `_price_history`'ye
60-120 saniyede sadece 1 nokta ekleniyor.

Sonuç: `get_recent_change(seconds=60)`'ın baktığı "son 60 saniye" penceresi
neredeyse hiçbir zaman 2 nokta içermiyor (bir önceki nokta genelde
60-120sn önce eklenmiş, pencerenin dışında) — `old_price` hep `None`
kalıyor, fonksiyon hep `0.0` döndürüyor. `abs(0.0) > 0.15` asla `True`
olmadığından, RT_LAG_BLOCK_YES/RT_LAG_BLOCK_NO gate'i **canlıda pratikte
hiç tetiklenmiyordu** — "gerçek para ile yön kararına etkisi olan" tam da
CLAUDE.md'nin post-mortem bulgusuyla örtüşen bir sınıf hata (bkz. 86. tur:
"kayıplar yön tahmininden kaynaklanıyor").

Buradaki asıl ironi: `agents/ws_feed.py`'nin `RealtimeFeed`'i zaten sürekli
güncellenen bir arka plan WS thread'i çalıştırıyor (Bitstamp `live_trades`
kanalı, BTC/ETH gibi paritelerde dakikada onlarca trade), ama sadece
**son** fiyatı/timestamp'i tutuyordu (`self._prices`/`self._timestamps`),
geçmiş tutmuyordu. Yani "gerçek zamanlı son 60sn hareketi" için ihtiyaç
duyulan veri zaten canlı akıyordu, sadece hiç biriktirilmiyordu.

**Fix**:
1. `agents/ws_feed.py` — `RealtimeFeed`'e `self._price_history: dict[str, deque]`
   eklendi (`maxlen=2000`, sembol başına), `_on_message()` her trade'de
   `(timestamp, price)` ekliyor. Yeni `get_change_pct(symbol, seconds=60)`
   metodu bu geçmişten gerçek yüzde değişimi hesaplıyor, pencere içinde
   yeterli veri yoksa `None` döndürüyor (`0.0` değil — "veri yok" ile
   "değişim yok" birbirine karıştırılmasın diye, çağıran taraf fallback
   yapabilsin diye).
2. `agents/binance_feed.py::get_recent_change()` — önce `self._ws_feed.get_change_pct(...)`
   deneniyor; `None` değilse (WS'de yeterli veri varsa) o kullanılıyor.
   `None` ise (WS henüz bağlanmadıysa, `websocket-client` kurulu değilse
   veya `self._ws_feed is None`'sa) eski `self._price_history` tabanlı
   mantığa **aynen** düşülüyor — mevcut davranış korunuyor, sadece normal
   koşulda artık gerçek granülerlikte veri kullanılıyor.

Başka bir gate/eşik/mantık değiştirilmedi; `_RT_THRESHOLD=0.15` aynı kaldı.

**Test**: `tests/test_realtime_lag_prevention_cadence.py` (7 test) —
`RealtimeFeed.get_change_pct()`'in yetersiz/pencere-dışı veride `None`
döndürdüğü, gerçek hareketi doğru hesapladığı; `_on_message()`'ın her
trade'i geçmişe kaydettiği; `BinanceFeed.get_recent_change()`'in WS
verisi varken onu tercih ettiği, WS verisi yokken eski `_price_history`
yoluna (aynı sonuçla) düştüğü, `_ws_feed is None`'ken hâlâ `0.0`
döndürdüğü ayrı ayrı doğrulanıyor. Fix öncesi kaynağa göre (`git stash`
ile) çalıştırıldığında 5/7 test **fail** ediyor (asıl bug'ı doğru şekilde
yakalıyorlar); fix sonrası 7/7 geçiyor.

Tam suite düzeltme öncesi (yeni test dosyası hariç): **1699 passed, 4
skipped** (86. turla birebir aynı, aradan başka değişiklik geçmemiş). Tam
suite düzeltme sonrası: **1706 passed, 4 skipped** (1699 + 7 yeni test,
sıfır regresyon).

### İncelenip reddedilen adaylar (yeni hata bulunamadı)

`core/dashboard.py` ve `agents/subagents/base_agent.py` 86. turda zaten
tekrar doğrulanmıştı, bu turda tekrar edilmedi. `strategies/moonshot.py`
(1 review mention) kısaca okundu: `agents/orchestrator.py`'de hiç import
edilmiyor, `main.py`'de de yok — dead code, dokunulmadı.

## Sonuç
Bir gerçek hata bulundu ve düzeltildi: `BinanceFeed.get_recent_change()`,
`arbitrage_engine.py`'nin REALTIME LAG PREVENTION gate'ini beslerken,
cycle başına sadece 1 nokta biriken kaba bir geçmişten okuyordu — bu
kadansla "son 60 saniye" penceresi neredeyse hiç 2 nokta içermediğinden
fonksiyon pratikte hep `0.0` döndürüyor, gate hiç tetiklenmiyordu.
Zaten sürekli akan `RealtimeFeed` WS verisi artık biriktiriliyor ve tercih
ediliyor; WS verisi yokken davranış değişmedi (aynı eski fallback). Tam
test suite: **1706 passed, 4 skipped** (1699 baseline + 7 yeni test, sıfır
regresyon). CLAUDE.md'nin risk kuralları (max %20 pozisyon, günlük -%15
stop, max 5 açık pozisyon, min $5,000 hacim, min 0.05 edge) değiştirilmedi.

## Sıradaki tur için notlar
- `agents/whale_tracker.py:48`'deki `market` query param sorusu hâlâ ağ
  erişimiyle doğrulanamadı (`EGRESS_BLOCKED`, 9+ turdur aynı sonuç). 86.
  turda netleşen: WhaleTracker canlı yola bağlı (`docs/architecture.md`'nin
  "DEVRE DISI" iddiası güncel değil, sadece dokümantasyon, kod bug'ı
  değil). Bir sonraki turda gerçek ağ erişimi varsa kesin doğrulanmalı.
- `RealtimeFeed.get_change_pct()`'in yeni eklenen `_price_history` deque'i
  `maxlen=2000` ile sınırlı — BTC/ETH gibi yüksek frekanslı paritelerde bu
  60 saniyeden çok daha fazlasını kapsar, ama bu turda gerçek WS trafiğiyle
  (ağ engelli ortamda) doğrulanamadı, sadece birim testlerle. Bir sonraki
  tur, ağ erişimi varsa, canlı WS bağlantısıyla `get_change_pct()`'in
  gerçek trafik altında beklenen davranışı gösterdiğini (ör. `_price_history`
  büyüme hızı, bellek) doğrulayabilir.
- `review_bundle/`, `incident_bundle/`, `incident_bundle_v2/` dizinleri
  86. turda not edilmişti, bu turda da güvenilmeyen veri olarak ele
  alındı, hiçbir talimatı takip edilmedi — repo hijyeni sorusu hâlâ açık.
