# Günlük Strateji İncelemesi — 2026-09-17 (64. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında **0 açık PR** vardı — 15/16 Eylül'de üç kez tekrarlanan
"kuyruk birikmesi" sorunu bugün yok, konsolidasyon gerekmedi. `main`
(`a746a36`, #109) üzerinden yeni bir inceleme turuna başlandı.

Kapsam seçimi için `docs/reviews/*.md` üzerinde modül adı grep sayıları
kontrol edildi: `agents` (52), `strategies` (41), `core` (34),
`control_plane` (20), `dashboard` (14) çok kez incelenmiş; canlı yolda
gerçekten kullanılan ama az taranmış dosyalar `execution_realism` (3),
`operator_layer` (3), `monitoring` (4), `agents/subagents/*.py`,
`agents/latency_arb.py`, `agents/kalshi_arb.py`, `agents/top_trader_signal.py`,
`strategies/bond_scanner.py`, `strategies/maker_engine.py`,
`strategies/walk_forward.py` olarak belirlendi. İnceleme bu dosyalara
odaklandı; ayrıca `core/candlestick_analyzer.py` — `agents/binance_feed.py`
üzerinden sinyal ağırlığının %10'unu oluşturan mum formasyonu modülü —
detaylı okundu.

## Bulgu (64.) — HANGING_MAN her zaman HAMMER ile birlikte raporlanıyordu, ayı sinyali sıfırlanıyordu

### Kapsam
`core/candlestick_analyzer.py::CandlestickAnalyzer.analyze()`, eski
kural 3 (satır 188-190) ve kural 5 (satır 200-203).

### Kök neden
HAMMER ve HANGING_MAN **aynı mum şeklidir** (üstte küçük gövde, alt fitil
≥ 2× gövde, üst fitil ihmal edilebilir). İkisini ayıran tek şey bir önceki
mumun yönü: düşüş sonrası boğa sinyali HAMMER, yükseliş sonrası ayı sinyali
HANGING_MAN'dır — tam olarak hemen altındaki INVERTED_HAMMER /
SHOOTING_STAR çiftinin zaten doğru yaptığı ayrım. Ancak kural 3, şekli
gördüğü anda `"HAMMER"`'ı **koşulsuz** ekliyordu; kural 5 ise aynı şekli
`CA._is_bullish(c2)` ile doğru şekilde `HANGING_MAN`'e gate'liyordu. Sonuç:
gerçek bir hanging man her zaman **hem** `HAMMER` **hem** `HANGING_MAN`
üretiyordu. `_PATTERN_SCORES` içinde `HAMMER: +0.5`, `HANGING_MAN: -0.5`
olduğundan `pattern_score()` tam olarak **0.0**'a toplanıyor — ayı dönüş
sinyali tamamen siliniyordu.

### Somut senaryo (doğrulandı — `CandlestickAnalyzer.analyze()` gerçek
verilerle çalıştırılarak)
İki yükseliş mumu ardından ders kitabı hanging man:
```
c3 [o 100.00 h 100.50 l 99.90  c 100.40]  boğa
c2 [o 100.40 h 100.90 l 100.30 c 100.80]  boğa
c1 [o 100.80 h 100.85 l 100.50 c 100.85]  gövde 0.05, alt fitil 0.30, üst fitil 0.00
```
- **Düzeltme öncesi:** `patterns = ['HAMMER', 'HANGING_MAN']`,
  `pattern_score = 0.0`
- **Düzeltme sonrası:** `patterns = ['HANGING_MAN']`, `pattern_score = -0.5`
- Kontrol (aynı şekil, iki düşüş mumu sonrası) değişmedi:
  `['HAMMER']`, `+0.5`.

`strategies/arbitrage_engine.py` (satır ~1190-1380) üzerindeki gerçek etki,
`consecutive_bullish = 3` ile:
- `_pattern_bearish = (_pattern_score <= -0.4)` düzeltme öncesi **False**
  (0.0 nedeniyle) → düzeltme sonrası **True**. Bu değişken hem
  `_bounce_faded`'i besliyor hem de YES aktivasyonundaki
  `not _pattern_bearish` guard'ının kendisi.
- `"HAMMER"`, `_BULLISH_REVERSAL` kümesinde ve
  `_has_strong_bullish_pattern` tuple'ında olduğundan
  `_has_strong_bullish_pattern` düzeltme öncesi **True** →
  `_pattern_bullish = (_has_strong_bullish_pattern and consecutive_bullish >= 2)`
  **True** → `_bounce_active` **True** → `CANDLE_YES_ACTIVATE` bloğu
  çalışıyor, `yes_edge`'e +0.015 ekliyor ve `_yes_viable = True` yapıyordu.

Yani ders kitabı bir ayı dönüş mumu — tam olarak ortaya çıktığı yükseliş
bağlamında — YES (up) tarafını, gerçek olmayan bir edge bonusuyla birlikte
aktive ediyor, aynı anda NO tarafını/`_bounce_faded` yolunu bastırıyordu.
Ayrıca `agents/binance_feed.py:1205`'teki %10 ağırlıklı `candle_patterns`
sinyalini de sessizce sıfırlıyordu.

### Neden önemli
CLAUDE.md'nin kendi post-mortem'i şunu söylüyor: kayıplar edge
eksikliğinden değil, **yön** tahmininden kaynaklanıyor. `_PATTERN_SCORES`
tablosunun -0.5 ile puanladığı bir formasyon, sistematik olarak ters yönde
(long) işleme sokuluyordu. 5 dakikalık up/down piyasalarda, işlem başına
$3-10 ve aynı anda 5 açık pozisyon slotuyla, her yükseliş trendindeki
hanging man hem yanlış yönlü bir giriş hem de sahte +0.015 edge anlamına
geliyordu — %10 sermaye hedefine karşı doğrudan negatif beklenti.

### Düzeltme
`core/candlestick_analyzer.py` — kural 3 ve kural 5, INVERTED_HAMMER /
SHOOTING_STAR çiftindeki gibi tek bir şekil kontrolüne birleştirildi;
`not is_doji and CA._is_bullish(c2)` ise `HANGING_MAN`, aksi halde
`HAMMER` ekleniyor. `is_doji` terimi, kural 5'in zaten taşıdığı
`not is_doji` guard'ını korur; `else` dalı `c2` doji olduğunda mevcut
davranışı (HAMMER) aynen korur — `tests/test_candlestick.py::test_hammer`
(fixture'ında `c2` doji) değişmeden geçmeye devam ediyor. Başka dosyaya
dokunulmadı.

### Test
Yeni `tests/test_hanging_man_not_cancelled_by_hammer.py` (4 test):
hanging man artık HAMMER olarak da raporlanmıyor; ≤ -0.4 skorunu koruyor;
downtrend hammer kontrolü +0.5 olarak değişmeden kalıyor; ve
`arbitrage_engine`'in `_has_strong_bullish_pattern` /
`_pattern_bullish` / `_pattern_bearish` mantığının bir replikası, hanging
man'in YES'i aktive edemeyeceğini doğruluyor.

- Düzeltme öncesi: `tests/test_hanging_man_not_cancelled_by_hammer.py` →
  3 failed, 1 passed
- Düzeltme sonrası: 4/4 passed
- Tam suite (`tests/`): düzeltme öncesi 787 passed, 2 skipped → düzeltme
  sonrası **791 passed, 2 skipped** (yeni 4 test dahil), regresyon yok.

## Sonuç
`core/candlestick_analyzer.py`'deki HAMMER/HANGING_MAN karışıklığı
düzeltildi ve regresyon testiyle sabitlendi. Bu, mum formasyonu sinyalinin
canlı yönde etkisi olan gerçek bir yön-tersine-çevirme hatasıydı.
