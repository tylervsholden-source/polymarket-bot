# Günlük Strateji İncelemesi — 2026-09-15 (38. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Kapsam
37. çalışmanın sonuç bölümünde önerilen, önceki 37 turda hiç dokunulmamış
alanlar dört paralel odakla tarandı: (1) `control_plane/*` (process lock,
approval queue, expiry/entry-window/reentry guard'ları), (2)
`strategies/monte_carlo.py` + `strategies/ml_classifier.py`, (3)
`agents/enhanced_signals.py`, (4) `agents/subagents/orderflow_agent.py`.
Dört bulgu raporlandı; ikisi (control_plane taskkill iddiası ve
enhanced_signals'ın devre dışı boost'ları) doğrulama sırasında elendi, ikisi
kodu satır satır okuyarak, somut sayılarla senaryo kurarak ve düzeltme
öncesi/sonrası testle doğrulandı.

## Elenen bulgular (doğrulamada gerçek çıkmadı)

- **`control_plane/process_lock.py` "taskkill Linux'ta çalışmaz" iddiası** —
  ilk bakışta ikna edici (Windows'a özgü `taskkill /F /PID` çağrısı, "os.kill
  Windows'ta güvensiz" yorumlarıyla), ancak `start_bot.bat`,
  `reset_and_start.bat` ve `.claude/commands/deploy.md` botun gerçek prod
  ortamının Windows (`C:\Users\lcladm\.antigravity\Polymarket`,
  `.venv\Scripts\python.exe`, `tasklist`/`taskkill` tabanlı deploy pipeline)
  olduğunu doğruluyor. Yani `taskkill` doğru komut — bu bir bug değil.
- **`agents/enhanced_signals.py`'nin `multi_exchange_imbalance`/options
  sinyallerinin hiçbir yere ulaşmaması** — gerçek ama zaten
  `strategies/arbitrage_engine.py:883-937`'de "kill all external boosts"
  yorumlarıyla bilinçli olarak devre dışı bırakılmış; yeni/canlı bir hata
  değil.

## Bulgu 1 (38a) — `TradeClassifier._extract_features()` "edge" özelliğini train'de farklı, live'da farklı hesaplıyor

### Hata
`strategies/ml_classifier.py::_extract_features()` (eğitim seti — kapanmış
pozisyonlardan):
```python
edge = abs(entry_price - 0.5)
```
`_extract_features_live()` (canlı `predict()` — her döngüde
`arbitrage_engine.py`'den çağrılıyor, `ml_score < -0.5` iken Kelly bet
size'ı ML_CAUTION ile yarıya indiriyor):
```python
edge = float(params.get("edge", 0.0))
```

Her ikisi de aynı feature slot'una (`_feature_names()` index 3, `"edge"`)
yazılıyor ve aynı `GradientBoostingClassifier` ile eğitilip skorlanıyor —
ama iki tamamen farklı büyüklük: train'de "fiyatın 0.5'ten uzaklığı"
(piyasa ne kadar favori görüyordu), live'da gerçek Bayesian-fiyat
mispricing edge'i (`strategies/arbitrage_engine.py`'de
`yes_edge = adjusted_bayesian - yes_price - cost`).

28. çalışmadan (18de444) beri `core/position_manager.py::add_position()`
gerçek `edge` değerini pozisyona yazıyor ve bu değer `closed` kaydına
olduğu gibi taşınıyor — yani gerçek değer zaten `positions.json`'da
duruyor, `_extract_features()` bunu hiç okumadan, entry_price'tan yeniden
hesaplıyordu.

**Somut senaryo:** entry_price=0.85, gerçek sinyal edge'i 0.07 olan bir
trade, train'de `edge=0.35` (`abs(0.85-0.5)`) olarak öğreniliyor. Aynı
fiyat/edge kombinasyonuyla açılan bir live trade `predict()`'e
`edge=0.07` olarak gidiyor. Model "0.35 civarı edge → şu WR" öğrenip
"0.07 edge" ile skorlanıyor — `ml_score` her canlı trade için anlamsız
hale geliyor, ML_CAUTION gerçek edge ile ilgisiz şekilde tetikleniyor/
tetiklenmiyor.

### Düzeltme
`_extract_features()`, pozisyonda gerçek `edge` alanı varsa onu kullanacak,
yoksa (28. çalışma öncesi eski trade'ler için) eski proxy'ye düşecek
şekilde değiştirildi:
```python
raw_edge = trade.get("edge")
edge = float(raw_edge) if raw_edge is not None else abs(entry_price - 0.5)
```

### Test
`tests/test_ml_classifier_edge_train_serve_skew.py` (3 test) — gerçek
edge'in kullanıldığını, edge alanı olmayan eski trade'lerde proxy'ye
düştüğünü, ve train/live'ın aynı girdide aynı feature'ı ürettiğini
doğruluyor. Düzeltme öncesi kaynakla (`git stash`): 2/3 test
**AssertionError — FAILED** (hata reprodüklendi: `0.05 == 0.12` gibi).
Düzeltme sonrası: **3/3 PASSED**.

## Bulgu 2 (38b) — `compute_bias_score()`, yönü olmayan "trade velocity"yi yönlü sinyal gibi oyluyor

### Hata
`agents/subagents/orderflow_agent.py::calc_trade_velocity()` sadece işlem
**sayısındaki** değişimi ölçüyor — `calc_cvd()`'nin aksine `is_buy` bilgisi
hiç kullanılmıyor. Ama `compute_bias_score()` bunu "pozitif = bullish"
işaretiyle diğer yönlü göstergelerle aynı ağırlıklı toplama (`BIAS_WEIGHTS`,
toplam 43 üzerinden velocity=3) katıyordu:
```python
scores["velocity"] = max(-100, min(100, velocity * 30))
```
Panik satışı da işlem sayısını arttırır — yön bilgisi olmayan bu
büyüklüğün "bullish" işaretiyle oylanması, tam olarak piyasanın en sert
hareket ettiği anda gerçek OBI/CVD BEARISH okumasını NEUTRAL'e
seyreltebiliyor/çevirebiliyor.

**Somut senaryo:** OBI=-0.7, CVD=-0.8 (kitapta ve tape'te ağır tek yönlü
satış) — diğer göstergeler nötr. Velocity'siz gerçek skor:
`(-70*8 + -80*7)/40 = -28.0` → BEARISH. Panik sırasında işlem sayısı da
sıçrarsa (velocity=4.0 → skor +100, eski TOTAL_WEIGHT=43):
`(-70*8 + -80*7 + 100*3)/43 = -19.1` → **NEUTRAL**. Bu,
`OrderFlowData.is_bearish`/`agrees_with()` üzerinden
`signal_agent_v2._compute_confluence()`'ın orderflow oyunu (37. çalışmanın
belirttiği 2.5/12 ağırlık) ve `_detect_risk_flags()`'ın
`ORDERFLOW_OPPOSITION` bayrağını doğrudan etkiliyor.

### Düzeltme
`velocity`, `BIAS_WEIGHTS`'ten çıkarıldı (artık yönlü oy vermiyor);
`trade_velocity` alanı teşhis amaçlı `OrderFlowData` üzerinde hâlâ
raporlanıyor:
```python
BIAS_WEIGHTS = {
    "ema": 10, "obi": 8, "cvd": 7,
    "vwap": 5, "ha_streak": 6, "walls": 4,
}
```

### Test
`tests/test_orderflow_velocity_not_directional.py` (3 test) — yukarıdaki
senaryoyu birebir kuruyor, velocity'nin `BIAS_WEIGHTS`'te olmadığını,
panik-satış senaryosunun velocity spike'ına rağmen BEARISH kaldığını, ve
bias'ın velocity büyüklüğünden bağımsız olduğunu doğruluyor. Düzeltme
öncesi kaynakla: 3/3 test **FAILED** (`label=NEUTRAL` reprodüklendi, bias
-26.0 vs -19.1 farkı doğrulandı). Düzeltme sonrası: **3/3 PASSED**.

## Doğrulama
- Her iki düzeltmenin testleri de düzeltme öncesi kaynakla ayrı ayrı
  **FAILED** (hata reprodüksiyonu doğrulandı), düzeltme sonrası ayrı ayrı
  **PASSED**.
- Tam suite: `pytest tests/ -q` → **703 passed, 1 failed, 2 skipped**.
  Başarısız olan tek test (`test_reduce_verdict_size_not_double_applied.py`)
  bu incelemeyle ilgisiz, önceden var olan bir hata: `LOW_LIQUIDITY_HOURS`
  kontrolü gerçek wall-clock UTC saatini okuyor ve testin varsayımıyla
  (saat aralığı dışı) çelişiyor — `git stash` ile bugünkü değişiklikler
  geri alınıp aynı test tekrar çalıştırıldığında da **aynı şekilde FAILED**
  (satır satır aynı `assert 0.7 == 1.0` hatası), yani bu düzeltmelerden
  kaynaklanmıyor. 6a2ba74'ün düzelttiği `BAD_HOUR_BLOCK` flakiness'ine
  benzer, farklı bir gate'te tekrarlanan bir desen — sıradaki tur için not
  edildi.
- `data/autonomous_state.json` (test suite'in yan etkisi) commit öncesi
  geri alındı.

## Sonuç
`main` artık 38 daily-review düzeltmesinin tamamına sahip olacak. Sıradaki
tur için öneriler:
- `tests/test_reduce_verdict_size_not_double_applied.py`'nin wall-clock
  bağımlılığını gider (6a2ba74'teki gibi saat mock'lanmalı).
- Taranmamış/derin incelenmemiş alanlar: `control_plane/entry_window_guard.py`
  ve `reentry_guard.py`'nin gerçek zamanlı davranışı (bu turda sadece
  process_lock derinlemesine incelendi), `strategies/monte_carlo.py`'deki
  `net_edge*2.0` çift-sayım (şu an sadece log/`viable` alanına gidiyor,
  canlı karara etkisi yok — ama ileride bağlanırsa yanlış EV raporlar),
  `agents/subagents/research_agent.py`'nin whale/smart-money birleştirme
  mantığı.
