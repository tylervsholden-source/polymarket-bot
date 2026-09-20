# Günlük Strateji İncelemesi — 2026-09-20 (102. tur, b)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = `a1ba25b` (#179, 101. tur konsolidasyonu
sonrası). Bugün aynı taban üzerinden başka bir oturum zaten "102. tur"
etiketiyle çalışmış ve **PR #180**'i açmıştı (henüz merge edilmemiş):
`core/candlestick_analyzer.py`'de doji şeklindeki bir hanging-man mumunun
yanlışlıkla bullish HAMMER olarak sınıflandırılıp
`strategies/arbitrage_engine.py`'deki `CANDLE_YES_ACTIVATE`'i hatalı
tetikleyebilmesi düzeltiliyor. Bu turda `core/candlestick_analyzer.py`'nin
`analyze()`/`pattern_score()`'una **dokunulmadı** — 21., 86. ve 102. (PR
#180) turlarda derinlemesine incelendi, temiz.

Konteynerde çalışan bir bot instance'ı yok (`data/status.json`/
`control.json`/`positions.json` bu sandbox'ta yok, ağ erişimi de yok) —
%10 hedefine karşı gerçek zamanlı sermaye ilerlemesi bu oturumdan
doğrulanamıyor; katkı kod/strateji doğruluğu seviyesinde kalıyor.

Baseline: bağımlılıklar (`pytest`, `loguru`, `pandas`, vb.) sistem
python'unda kurulu değildi, `pip install -r requirements.txt` ile kuruldu.
`python3 -m pytest tests/ calibration/tests execution_realism/tests
signal_bridge/tests crypto_directional/tests -q` → **1762 passed, 4
skipped**.

## Bu turda incelenen alan: `agents/ws_feed.py` (`RealtimeFeed`)

Önceki oturumun raporunda önerilen az-incelenmiş alanlardan `RealtimeFeed`
seçildi — `RT_LAG_BLOCK_YES/NO` gate'ini (`strategies/arbitrage_engine.py`)
besleyen canlı WS verisi, şimdiye kadar sadece `agents/binance_feed.py`
üzerinden dolaylı okunmuştu, dosyanın kendisi hiç satır satır incelenmemişti.

Zincir uçtan uca izlendi:
`RealtimeFeed._on_message()` (Bitstamp `live_trades_*` WS mesajları) →
`_price_history` (per-trade, deque(maxlen=2000)) → `get_change_pct()` →
`BinanceFeed.get_recent_change()` → `strategies/arbitrage_engine.py`'deki
`_rt_change` → `RT_LAG_BLOCK_YES`/`RT_LAG_BLOCK_NO` (YES/NO sinyalini
son 60 saniyedeki gerçek fiyat hareketiyle çelişiyorsa engelleyen gate).

`get_change_pct()`'in pencere-içi nokta arama mantığı (`ts >= cutoff` olan
ilk noktayı "eski fiyat" al), 87./88. turda düzeltilen "coarse
`_price_history`, cycle başına bir nokta → 60s pencerede neredeyse hiç 2
nokta olmuyor" hatasının aynısını tekrarlamadığı doğrulandı (per-trade
history, dakikada onlarca nokta) — `tests/test_realtime_lag_prevention_cadence.py`
bu davranışı zaten kilitliyor, dokunulmadı.

### Bulunan hata: WS aboneliği ilk cycle'ın kısmi coin kümesine kilitleniyordu

`agents/binance_feed.py::refresh()`:

```python
if not self._ws_started:
    self._ws_started = True
    ...
    self._ws_feed = RealtimeFeed()
    self._ws_feed.start(symbols)   # <-- o cycle'ın market listesinden türeyen küme
```

`symbols`, `refresh_spot_data()` tarafından **o cycle'da Polymarket'te aktif
olan marketlerden** (`_detect_asset(m["question"])`) türetiliyor — sabit bir
evren değil. `RealtimeFeed.start(symbols=None)` zaten "None ise tüm bilinen
`_WS_PAIRS` evrenine (BTC/ETH/SOL/XRP/DOGE/BNB) abone ol" davranışına sahip,
ama `refresh()` bu varsayılanı hiç kullanmıyor, doğrudan cycle'a özgü kümeyi
geçiyordu.

**Canlı etki**: Bot boot olduğunda ya da bir coin'in 5 dakikalık marketi tam
o an rollover arasındaysa (örn. DOGE/BNB marketi henüz açılmadıysa), ilk
`refresh()` çağrısındaki `symbols` 6 coin'in hepsini içermeyebilir.
`_ws_started` bayrağı bir daha asla `False` olmuyor ve `RealtimeFeed.start()`
zaten çalışıyorsa (`self._running`) hiçbir şey yapmadan `True` dönüyor — yani
ilk çağrıda eksik kalan coin(ler) **sürecin ömrü boyunca hiç WS aboneliği
almıyor**. O coin(ler) için `get_recent_change()` WS yoluna hiç düşemeyip
eski coarse `_price_history` yoluna (cycle başına bir nokta, 60-120s
periyot) geri düşüyor — tam olarak PR #156/87. turun WS'i eklemesine yol
açan sorun. Sonuç: o coin'ler için `RT_LAG_BLOCK_YES/NO` "son 60 saniyedeki
gerçek fiyat hareketi sinyale ters mi" kontrolünü pratikte hiç
yapamıyor, "stale momentum'a binme" koruması o coin'ler için sessizce devre
dışı kalıyor — halbuki diğer coin'ler (ilk cycle'da marketi açık olanlar)
korumayı alıyor. Mevcut testlerin hiçbiri (`test_realtime_lag_prevention_cadence.py`
dahil) bu "ilk cycle'ın kısmi kümesi kalıcı" davranışını kilitlemiyor —
kasıtlı bir tasarım değil, eksik.

## Düzeltme

`refresh()`, `RealtimeFeed.start()`'a artık cycle'a özgü kısmi `symbols`
kümesini değil, argümansız çağrı ile zaten var olan "tüm bilinen
`_WS_PAIRS` evrenine abone ol" varsayılanını kullanıyor:

```python
self._ws_feed.start()
```

Tek satırlık, davranış dışında hiçbir şeye dokunmayan minimal değişiklik.
Bitstamp WS'e 6 kanal abone olmanın maliyeti önemsiz; `_WS_PAIRS` zaten
botun ticaret yaptığı sabit coin evrenini (BTC/ETH/SOL/XRP/DOGE/BNB)
temsil ediyor, bu yüzden "gereksiz abonelik" riski yok.

## Regresyon testi

`tests/test_ws_feed_first_cycle_partial_symbols.py` — `BinanceFeed.refresh()`
sadece `{"BTCUSDT"}` ile çağrıldığında (ilk cycle'da yalnızca BTC marketi
açık senaryosu), `RealtimeFeed.start()`'a geçilen/varsayılan kümenin
`_WS_PAIRS`'in tam evrenini kapsadığını doğruluyor (ağ/thread açmadan,
`RealtimeFeed`'i sahte bir yakalayıcı sınıfla değiştirerek; diğer tüm
network-bağımlı yardımcı metodlar `AsyncMock` ile stub'landı).

- **Düzeltme öncesi**: test başarısız —
  `WS feed only subscribed to {'BTCUSDT'}, missing {'DOGEUSDT', 'XRPUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT'}`.
- **Düzeltme sonrası**: test geçiyor.

## Test doğrulaması

- Düzeltme öncesi (baseline): `1762 passed, 4 skipped`
- Düzeltme sonrası: `python3 -m pytest tests/ calibration/tests
  execution_realism/tests signal_bridge/tests crypto_directional/tests -q`
  → **1763 passed, 4 skipped** (baseline + 1 yeni test, hiç regresyon yok).

## Sonuç

`agents/ws_feed.py`/`RealtimeFeed` derinlemesine incelendi
(`_on_message`, `get_change_pct`, `start`/`stop`, thread/reconnect
döngüsü, `_reverse_map` kanal eşlemesi). Bulunan tek gerçek hata:
`BinanceFeed.refresh()`'in WS aboneliğini ilk cycle'ın kısmi coin
kümesine kalıcı olarak kilitlemesi, bazı coin'ler için RT_LAG koruma
gate'ini sessizce devre dışı bırakıyordu. Minimal tek satırlık düzeltme
uygulandı, önce-başarısız/sonra-başarılı bir regresyon testiyle
kilitlendi, tam suite regresyonsuz geçti.

## Sıradaki tur için notlar

- `agents/trade_analyzer.py`'nin `EXPIRED` çıktı dalı ve
  `strategies/maker_engine.py`'nin `check_paired_profit` dışındaki kısmı
  hâlâ bu turun listesinde kaldı, bu oturumda taranmadı — bir sonraki
  tur için öncelikli aday.
- Dashboard/web durum servis kodu (`core/web_server.py` zaten 88. turda
  `POST /api/control` whitelist'i açısından incelenmişti) ve `scripts/`
  hâlâ derinlemesine taranmadı.
- PR #180 (`core/candlestick_analyzer.py` doji/hanging-man düzeltmesi)
  bu oturumdan bağımsız açık — bir sonraki tur önce onun merge durumunu
  kontrol etmeli (merge olursa baseline test sayısı değişebilir).
- `crypto_directional/`'ın canlı yola sıfır etkisi olduğu (88./100./102.
  turlarda doğrulandı) bu turda yeniden araştırılmadı, hâlâ geçerli kabul
  edildi.
