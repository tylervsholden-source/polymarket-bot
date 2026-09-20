# Günlük Strateji İncelemesi — 2026-09-20 (102. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = `a1ba25b` (101. tur konsolidasyonu merge
edilmiş) idi, branch zaten bu commit'teydi. Açık PR yoktu. Konteynerde
çalışan bir bot instance'ı yok (`data/status.json`/`control.json`/
`positions.json` bu sandbox'ta yok, ağ erişimi yok) — %10 hedefine karşı
gerçek zamanlı sermaye ilerlemesi bu oturumdan doğrulanamıyor; katkı
kod/strateji doğruluğu seviyesinde.

## Baseline doğrulama
`pip install -r requirements.txt` + `python3 -m pytest tests/
calibration/tests crypto_directional/tests execution_realism/tests
signal_bridge/tests -q` → **1762 passed, 4 skipped** (regresyon yok).

## Bu turda yapılanlar

### 1. Kapsam belirleme
101. turun raporu bir sonraki tur için `crypto_directional/` ve
`agents/{binance_feed,ml_classifier,walk_forward}.py`'yi önermişti.
`crypto_directional/` için önce hızlı bir bağlantı kontrolü yapıldı:
`calibration/types.py`'deki tek referans sadece bir docstring yorumu —
modül `orchestrator.py`/`arbitrage_engine.py`/`position_manager.py`
tarafından hiç import edilmiyor, yani canlı karara sıfır etkisi olan ölü
kod (100. turda `signal_bridge/` için tespit edilen durumla aynı sınıf).
Bu yüzden atlandı; bütçe canlı yola bağlı iki alana ayrıldı.

### 2. Tur 1 — `binance_feed.py` / `ml_classifier.py` / `walk_forward.py` derin taraması
Subagent ile satır satır tarandı, her çıktı alanı `arbitrage_engine.py`,
`bayesian.py`, `kelly_criterion.py`, `orchestrator.py`,
`position_manager.py`'deki tüketicilerine kadar izlendi. **Yeni hata
bulunamadı** — adaylar ya önceki turlarda zaten düzeltilip teste
bağlanmış (ML_BOOST, 4h window bucket, entry_price/edge train-serve skew,
bb_squeeze/breakout, cross_exchange_boost sıfırlama) ya da kasıtlı
tasarım (bayesian.py'nin v2 momentum-first redesign'ında devre dışı
bırakılan rsi/stoch/sr/fib parametreleri, kelly'nin NEUTRAL pass-through
davranışı — `tests/test_neutral_trades_not_counted_as_losses.py` ile
referans doğru olarak kilitli) olarak çıktı.

### 3. Tur 2 — `core/candlestick_analyzer.py` derin taraması ve bulunan hata
İlk turun önerisiyle ikinci bir subagent bu dosyaya yönlendirildi
(`binance_feed.py`'nin `_process_klines()`/`get_candle_analysis()`'i
üzerinden çağrılıyor, `pattern_score` çıktısı `arbitrage_engine.py`'deki
`_has_strong_bullish_pattern`/`_pattern_bullish`/`_pattern_bearish`
kapılarını doğrudan besliyor). Dosyanın 21. ve 86. turlarda zaten
incelenmiş/düzeltilmiş geçmişi var (THREE_WHITE_SOLDIERS/
THREE_BLACK_CROWS `range3` hatası, `multi_tf_score`'un hiç
bağlanmadığının teyidi) — bunlar tekrar doğrulandı, yeniden dokunulmadı.

**Bulunan hata**: `CandlestickAnalyzer.analyze()` içindeki HAMMER vs
HANGING_MAN ayrımı (`core/candlestick_analyzer.py`, ~199-213. satırlar):

```python
if body1 > 0 and lw1 >= body1 * 2 and uw1 <= body1 * 0.5:
    if not is_doji and CA._is_bullish(c2):
        patterns.append("HANGING_MAN")
    else:
        patterns.append("HAMMER")
```

`is_doji`, c1'in (hammer/hanging-man mumunun kendisi) gövdesinin kendi
range'inin ≤%10'u olup olmadığını tanımlıyor — c2'yi değil. Docstring'in
kendisi ayrımın *yalnızca* c2'nin yönüne bağlı olması gerektiğini
söylüyor (hemen altındaki INVERTED_HAMMER/SHOOTING_STAR bloğu bunu doğru
yapıyor). Ama bu satırdaki gereksiz `not is_doji` koşulu: hammer/hanging-
man şeklinin gövdesi tanımı gereği küçük olduğundan, çok sık aynı zamanda
DOJI/DRAGONFLY_DOJI eşiğini de geçiyor — bu durumda `not is_doji` False
oluyor ve `else` dalı koşulsuz olarak tetiklenip **HAMMER** (boğa, +0.5)
yazıyordu, c2 yükselişte olsa bile — doğrusu **HANGING_MAN** (ayı, -0.5).

**Canlı etki**: `HAMMER`, `arbitrage_engine.py`'nin
`_has_strong_bullish_pattern` tuple'ında ve `_BULLISH_REVERSAL`
kümesinde. Gerçekte ayı sinyali veren bir hanging-man mumu HAMMER olarak
yanlış sınıflandırıldığında `_pattern_bullish`/`_bounce_active`'i set
edip `CANDLE_YES_ACTIVATE`'i tetikleyebiliyordu — ayı dönüş sinyaline
rağmen YES (yükseliş) tarafını almak. Ayrıca `pattern_score()` net
pozitif dönüyordu (DRAGONFLY_DOJI +0.3, HAMMER +0.5 = +0.8), yani
`_pattern_bearish` (score ≤ -0.4) hiç tetiklenemiyordu.

**Düzeltme**: gereksiz `not is_doji` koşulu kaldırıldı — ayrım artık
yalnızca c2'nin yönüne bakıyor, tıpkı hemen altındaki
INVERTED_HAMMER/SHOOTING_STAR bloğu gibi.

**Test**: `tests/test_hanging_man_doji_shape_not_forced_bullish.py`
(4 test):
- `test_doji_shaped_hanging_man_is_not_reported_as_hammer`
- `test_doji_shaped_hanging_man_does_not_score_bullish`
- `test_doji_shaped_hanging_man_does_not_look_like_a_strong_bullish_pattern`
  (arbitrage_engine'in `_has_strong_bullish_pattern`/`_pattern_bullish`
  kapılarını doğrudan taklit ediyor)
- `test_downtrend_doji_shaped_candle_still_reports_hammer` (kontrol —
  aynı geometri ama ayı c2'den sonra hâlâ HAMMER olmalı)

**Doğrulama** (bağımsız olarak tekrarlandı):
- Düzeltme öncesi (`git stash` ile): 3/4 yeni test FAIL
  (`patterns=['DRAGONFLY_DOJI', 'HAMMER']`, `score=0.8`,
  `has_strong_bullish=True`).
- Düzeltme sonrası: 4/4 PASS.
- Tam suite: **1762 → 1766 passed, 4 skipped** (+4 yeni test, 0
  regresyon — beklenen aritmetik).

## Sermaye/performans notu
Bu oturumda çalışan bir bot instance'ı yok, %10 hedefine karşı gerçek
ilerleme bu oturumdan doğrulanamıyor. Bu turun katkısı: mum formasyonu
tabanlı YES-aktivasyon sinyalinin, gerçekte ayı bir hanging-man mumunu
her zaman yanlışlıkla boğa HAMMER olarak okuyup yanlış yöne pozisyon
açılmasına yol açabilecek sık rastlanan bir durumu (doji eşiğiyle
örtüşen hammer/hanging-man gövdeleri) düzeltmek.

## Sıradaki tur için notlar
- `core/candlestick_analyzer.py`'nin canlı-bağlı yarısı (`analyze`/
  `pattern_score`) artık üç kez (21, 86, 102. turlar) derin taranmış ve
  temiz çıkıyor — dördüncü kez dönmek yerine daha az taranmış alanlara
  geçilmesi öneriliyor: `agents/ws_feed.py` (`RealtimeFeed`,
  `RT_LAG_BLOCK_YES/NO` kapısını besliyor, şimdiye kadar yalnızca
  dolaylı okundu), `agents/trade_analyzer.py`'nin `EXPIRED` sonuç dalı
  (WIN/LOSS/NEUTRAL dışında üçüncü bir kategori, bazı tüketiciler bunu
  yalnızca örtük olarak atlıyor), `strategies/maker_engine.py`'nin
  `check_paired_profit` dışındaki kısmı, `dashboard`/`web` durum-servis
  kodu, veya `scripts/`.
- `crypto_directional/` hâlâ tamamen bağlanmamış (ölü kod) — 88, 100 ve
  şimdi 102. turlarda aynı sonuçla tekrar teyit edildi, tekrar
  bildirilmeyecek.
