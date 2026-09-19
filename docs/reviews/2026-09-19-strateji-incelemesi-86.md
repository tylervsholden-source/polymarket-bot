# Günlük Strateji İncelemesi — 2026-09-19 (86. tur)

## Durum
Oturum başında `origin/main` = bu branch = `a2662e8` (#149, 85. inceleme
sonrası — `Orchestrator._analyze_new_closed_trades()`'in `_current_closed_trades()`
yerine `position_manager.data["closed"]` okuduğu bug'ı düzeltilmiş). Açık PR
yoktu. Baseline: `python3 -m pytest -q` → **1695 passed, 4 skipped**
(85. turun bıraktığı sayıyla birebir aynı — aradan başka değişiklik geçmemiş).
Bu ortamda `pytest`/`sklearn` sistem python'unda kurulu değildi,
`pip install -r requirements.txt` + `pip install scikit-learn` ile kurulup
baseline doğrulandı.

## Bu turda yapılanlar

### 1) 85. turun açık maddeleri takip edildi

**a) `agents/whale_tracker.py:48` — `market` query param sorusu.**
`data-api.polymarket.com`'a bu oturumda da `curl` ile erişim denendi:
proxy durumu (`$HTTPS_PROXY/__agentproxy/status`) `connect_rejected` /
`403 CONNECT tunnel failed` gösterdi — 14. turdan bu yana (80., 82., 83.,
84., 85.) tekrar eden aynı `EGRESS_BLOCKED` sonucu, bu turda da kesin ağ
doğrulaması yapılamadı.

Ama madde talimatında sorulan ikinci soru — "WhaleTracker canlı yola
bağlı mı, yoksa gerçekten ölü kod mu?" — bu turda **kesin** olarak
cevaplandı: `docs/architecture.md`'nin "DEVRE DISI" listesi WhaleTracker'ı
SmartTraderTracker ile "değiştirildi" diye işaretliyor, ama bu **güncel
değil**. Zincir doğrulandı:
`agents/orchestrator.py:192` → `AgentCoordinator(whale_tracker_cls=WhaleTracker, ...)`
→ `agents/subagents/coordinator.py` → `agents/subagents/research_agent.py::_fetch_whale_data()`
→ `WhaleTracker().get_activity(condition_id)` her cycle'da (`ENABLE_RESEARCH_AGENT`
varsayılan `"true"`) candidate marketler için paralel olarak çağrılıyor,
sonuç `signal_agent_v2.py` üzerinden confluence_score/`WHALE_OPPOSITION`
risk flag'ine gidiyor (bkz. `tests/test_whale_tracker_outcome_side_mismatch.py`
docstring'i, 14. turda `outcome`/`side` karışıklığı zaten bu gerçek yol
üzerinden düzeltilmişti). Yani WhaleTracker **canlı**, `docs/architecture.md`
bu noktada yanlış/eski — ama bu bir dokümantasyon tutarsızlığı, kod bug'ı
değil, bu yüzden dokunulmadı (talimat "sadece gerçek kod bug'ı düzelt"
diyor).

`market` param'ının kendisi için: repodaki 3 bağımsız `data-api.polymarket.com/trades`
tüketicisi (`agents/top_trader_signal.py` — yorum: "conditionId (camelCase)
— never market or condition_id"; `agents/smart_trader_tracker.py:205` —
`pos.get("conditionId") or pos.get("market") or ...`; `artifacts/recent_trades.json`
— gerçek yakalanmış 50 trade örneği, tamamı `conditionId` alanı taşıyor,
`market` alanı yok, 47 farklı conditionId) tutarlı biçimde yanıt şemasının
`market` DEĞİL `conditionId` kullandığını doğruluyor. Ama bu, whale_tracker'ın
kullandığı **istek** filtre parametresiyle (`params={"market": condition_id}`)
aynı şey değil — 80. turun tespit ettiği gibi hiçbir kardeş çağrı noktası
sunucu tarafında market'e göre filtreleme yapmıyor (hepsi `user`/`limit`
kullanıp filtrelemeyi client-side yapıyor), yani doğrudan bir emsal hâlâ yok.
Gerçek ağ erişimi olmadan kesin karar verilemiyor — madde yine açık
bırakıldı, ama artık "WhaleTracker'ın dead code olma ihtimali" ekarte
edildiği için bir sonraki turun bu soruya odaklanması daha değerli.

**b) `agents/orchestrator.py:654/1534/2344`'teki doğrudan
`position_manager.data.get("closed", ...)` noktaları bağımsız yeniden
doğrulandı** (satır numaraları 85. turdan bu yana küçük kaymalar gösterdi,
içerik aynı):
- `:654` ve `:1534` — ikisi de `_sw.update(..., closed=...)` çağrısının
  içinde, sadece `StatusWriter`/dashboard'a yazılıyor, hiçbir karar
  zincirine geri beslenmiyor. Doğrulandı (85. turla aynı sonuç).
- `:2344` — `closed_order_ids = {c.get("order_id", "") ... }` sonra
  `proxy_wallet in closed_order_ids` kontrolü yapıyor (bir `order_id`
  kümesine bir cüzdan adresi arıyor — kendi başına şüpheli bir eşleşme).
  Ama bu turda kod satır satır yukarı izlendi: bu tüm blok (`agents/
  orchestrator.py` — Polymarket positions-sync + ghost-pozisyon temizliği)
  fonksiyonun başında **`# Polymarket data API pozisyon sync'i DEVRE DIŞI.`**
  yorumunun hemen ardından gelen koşulsuz `return` ifadesinden SONRA yer
  alıyor — yani `try:` bloğunun tamamı (bu satır dahil) sözdizimsel olarak
  **hiçbir zaman çalışmayan, tamamen ulaşılamaz kod**. 85. turun "sadece
  canlı modda anlamlı" değerlendirmesinden daha güçlü bir sonuç: bu kod
  hiçbir modda hiç çalışmıyor. Üçü de düzeltme gerektirmiyor.

### 2) Az incelenmiş dosyalarda taze tarama

Talimatın önerdiği listeden `docs/reviews/*8[3-5]*.md` ve tüm `docs/reviews/*.md`
grep sayımına göre en az değinilen dosyalar seçildi:
`agents/subagents/base_agent.py`, `agents/subagents/orderflow_agent.py`,
`core/candlestick_analyzer.py`, `core/dashboard.py`, `strategies/mean_reversion.py`.

### Bulunan ve düzeltilen hata: `CandlestickAnalyzer.analyze()` — THREE_WHITE_SOLDIERS/THREE_BLACK_CROWS, 3 mumun ikisinin gövde-gücünü KENDİ aralığı yerine son mumun (`range1`) aralığına göre ölçüyordu

`core/candlestick_analyzer.py::analyze()`, fonksiyon başında sadece
`range1, range2 = CA._range(c1), CA._range(c2)` hesaplıyordu — `range3`
(üç mum öncesinin range'i) fonksiyonda **hiç tanımlanmamıştı**. Buna
rağmen THREE_WHITE_SOLDIERS/THREE_BLACK_CROWS kontrolü şöyleydi:

```python
body3 > range1 * 0.3 and body2 > range1 * 0.3 and body1 > range1 * 0.3
```

Yani `body2` (ikinci mumun gövdesi) ve `body3` (üçüncü/en eski mumun
gövdesi) kendi aralıklarına göre değil, en SON (güncel) mumun aralığına
(`range1`) göre ölçülüyordu. Dosyadaki her diğer formasyon (DOJI,
SPINNING_TOP, MARUBOZU, HAMMER, ENGULFING, HARAMI...) tutarlı biçimde
"kendi mumunun kendi range'i" kuralını kullanıyor — bu iki formasyon
tek istisnaydı, ve `range3`'ün hesaplanmamış olması bunun kasıtlı bir
tasarım değil, atlanmış bir değişken olduğunu gösteriyor.

**Somut senaryo (gerçek kodla doğrulandı)**: c3 (range=6.0, body=0.5,
gövde/aralık=%8.3 — neredeyse doji, kararsız), c2 (range=8.0, body=1.5,
%18.75 — yine zayıf) ve güçlü ama küçük aralıklı son mum c1 (range=0.4,
body=0.2, %50). Gerçek "üç asker" formasyonu değil — iki eski mum
neredeyse kararsız. Eski kodla: `body3(0.5) > range1(0.4)*0.3=0.12` →
True, `body2(1.5) > 0.12` → True → **`THREE_WHITE_SOLDIERS`** yanlışlıkla
tetiklendi, `pattern_score = +0.9` — `_PATTERN_SCORES` tablosundaki TEK EN
YÜKSEK skor. Ayna senaryo (bearish) `THREE_BLACK_CROWS` (`-0.9`) için de
doğrulandı.

**Neden önemli — canlı yol**: `agents/binance_feed.py:1205-1207`,
`CandlestickAnalyzer.pattern_score()`'u sinyal kompozitine **%10 ağırlıkla**
katıyor (`agents/binance_feed.py:1128` yorumu). Ayrıca
`strategies/arbitrage_engine.py:1207-1272`, `THREE_WHITE_SOLDIERS`'ı
`_BULLISH_REVERSAL`/`_has_strong_bullish_pattern`'e,
`THREE_BLACK_CROWS`'u `_BEARISH_REVERSAL`/`_pattern_bearish`'e doğrudan
koyuyor — bunlar YES/NO trade aktivasyon gate'lerini besliyor
(`_pattern_bullish`/`_pattern_bearish`, `CANDLE_YES_ACTIVATE` bloğu). Yani
iki zayıf/kararsız mumdan oluşan yanlış pozitif, sistemin candlestick
modülünün üretebileceği EN GÜÇLÜ yönlü sinyali (±0.9) üretip hem %10
ağırlıklı kompozite hem de doğrudan YES/NO aktivasyon gate'lerine
besleniyordu — CLAUDE.md'nin post-mortem bulgusuyla ("kayıplar yön
tahmininden kaynaklanıyor, edge'den değil") doğrudan örtüşen bir sınıf
hata.

**Fix** (`core/candlestick_analyzer.py`):
```python
# önce:
range1, range2 = CA._range(c1), CA._range(c2)
...
body3 > range1 * 0.3 and body2 > range1 * 0.3 and body1 > range1 * 0.3
# sonra:
range1, range2, range3 = CA._range(c1), CA._range(c2), CA._range(c3)
...
body3 > range3 * 0.3 and body2 > range2 * 0.3 and body1 > range1 * 0.3
```
İki formasyonda da (WHITE_SOLDIERS ve BLACK_CROWS) aynı düzeltme
uygulandı. Başka bir formasyon/mantık değiştirilmedi; gerçek "üç güçlü
mum" senaryosu (her üçü de kendi range'ine göre güçlü gövdeli) test ile
doğrulanıp davranışının aynen korunduğu kontrol edildi.

**Test**: `tests/test_three_soldiers_crows_own_range.py` (4 test) —
zayıf/kararsız gövdeli c2/c3 + küçük range'li c1 senaryosunda düzeltme
öncesi `THREE_WHITE_SOLDIERS`/`THREE_BLACK_CROWS`'un yanlışlıkla
tetiklendiği (`AssertionError: assert 'THREE_WHITE_SOLDIERS' not in
[...]`), düzeltme sonrası tetiklenmediği; ayrıca gerçek/güçlü üç-mum
formasyonunun (her mum kendi range'ine göre güçlü) düzeltmeden sonra da
hâlâ doğru tespit edildiği (`pattern_score == ±0.9`) ayrı iki kontrol
testiyle doğrulanıyor.

Düzeltme öncesi (yeni test dosyası, sadece kaynak stash'lenip): **2
failed** (WHITE_SOLDIERS ve BLACK_CROWS yanlış-pozitif testleri), 2
passed (kontrol testleri, zaten pre-fix'te de doğru formasyonu buluyordu).
Tam suite düzeltme öncesi (test dosyası dahil değilken, sadece mevcut
kod): **1695 passed, 4 skipped**. Düzeltme sonrası tam suite: **1699
passed, 4 skipped** (1695 + 4 yeni test, sıfır regresyon).

### İncelenip reddedilen adaylar (yeni hata bulunamadı)

`agents/subagents/base_agent.py` — `execute()`'ın timeout/hata sarmalama
mantığı, `AgentResult`/`AgentStatus` durum geçişleri satır satır okundu,
sorunsuz. `agents/subagents/orderflow_agent.py` — 11 indikatörün
(OBI/CVD/walls/VWAP/EMA cross/HA streak/velocity) hesap formülleri ve
`compute_bias_score()`'un ağırlıklı toplaması bağımsız yeniden doğrulandı;
`trade_velocity`'nin yön oyu olarak SAYILMADIĞI (81. turda düzeltilmiş
bug) hâlâ doğru korunuyor, yeni kusur yok. `core/dashboard.py` — Rich
terminal UI, `main.py`/`agents/orchestrator.py`/`agents/btc_arb_agent.py`/
`agents/onchain_watcher.py`'den `dashboard.update(...)` ile beslenen tek
yönlü bir görüntüleme sink'i (karar zincirine geri beslenmiyor,
`core/status_writer.py`'nin 85. turda incelenen benzeri), bölme
noktalarında (`pnl_pct`) sıfır koruması var, ek kusur bulunamadı.
`strategies/mean_reversion.py` — `docs/architecture.md`'nin "Mean
reversion: DEVRE DISI" notu doğrulandı: `MeanReversionStrategy` hiçbir
yerden (`agents/orchestrator.py`, `main.py`, `agents/subagents/*`) import
edilmiyor, gerçekten ölü kod — Kelly fraksiyonu/edge formülü (78. turda
zaten bir kez incelenmiş) bu turda tekrar okundu, canlı yola bağlı
olmadığı için düzeltme önceliği yok.

## Sonuç
Bir gerçek hata bulundu ve düzeltildi:
`CandlestickAnalyzer.analyze()`'daki THREE_WHITE_SOLDIERS/THREE_BLACK_CROWS
formasyon tespiti, üç mumdan ikisinin gövde-gücünü kendi aralıkları
yerine en son mumun aralığına göre ölçüyordu (`range3` hiç
hesaplanmamıştı) — bu, iki zayıf/kararsız gövdeli mumun, son mumun
aralığı küçük olduğunda yanlışlıkla `_PATTERN_SCORES` tablosundaki en
yüksek büyüklükteki sinyali (±0.9) üretmesine yol açıyordu. Bu sinyal
`agents/binance_feed.py`'de %10 ağırlıkla kompozite giriyor ve
`strategies/arbitrage_engine.py`'de doğrudan YES/NO aktivasyon
gate'lerini besliyor — gerçek para ile yön kararına etkisi olan bir hata.
Tam test suite: **1699 passed, 4 skipped** (1695 baseline + 4 yeni test,
sıfır regresyon). CLAUDE.md'nin risk kuralları (max %20 pozisyon, günlük
-%15 stop, max 5 açık pozisyon, min $5,000 hacim, min 0.05 edge) kod
tarafında değiştirilmedi.

## Sıradaki tur için notlar
- `agents/whale_tracker.py:48`'deki `market` query param sorusu hâlâ ağ
  erişimiyle kesin doğrulanamadı (`EGRESS_BLOCKED`, 8+ turdur aynı
  sonuç). Bu turda netleşen: WhaleTracker `docs/architecture.md`'nin
  iddiasının aksine **canlı yola bağlı, ölü kod değil** (`Orchestrator`
  → `AgentCoordinator` → `ResearchAgent._fetch_whale_data()` →
  `WhaleTracker.get_activity()`, `ENABLE_RESEARCH_AGENT` varsayılan
  `true`) — bu yüzden `market` param'ının doğruluğu hâlâ gerçek etkisi
  olan açık bir soru; gerçek ağ erişimi olan bir oturum kesin
  doğrulamalı. Ayrıca `docs/architecture.md`'nin "DEVRE DISI" listesinin
  WhaleTracker satırı güncel değil — bir sonraki tur isterse bunu
  (kod değil, sadece dokümantasyon) düzeltebilir.
- `agents/orchestrator.py:654/1534/2344`'teki üç doğrudan
  `position_manager.data.get("closed", ...)` noktası bu turda üçüncü kez
  bağımsız doğrulandı, üçü de zararsız (`:2344` ayrıca sözdizimsel olarak
  ulaşılamaz kod olduğu netleşti) — bu madde artık kapatılabilir, tekrar
  tekrar doğrulanmasına gerek yok.
- Bu oturumda `review_bundle/`, `incident_bundle/`, `incident_bundle_v2/`
  adlı üst düzey dizinler fark edildi (`agents/whale_tracker.py` ve
  `strategies/mean_reversion.py`'nin kopyalarını içeriyorlar,
  `CLAUDE.md`/`docs/architecture.md` kopyaları dahil). Bu turda sadece
  `artifacts/recent_trades.json` (gerçek data-api şema örneği) veri
  kaynağı olarak kullanıldı; bu dizinlerin içeriği güvenilmeyen veri
  olarak ele alındı, hiçbir talimatı takip edilmedi. Bir sonraki tur bu
  dizinlerin ne amaçla var olduğunu (test fixture mı, yanlışlıkla commit
  mi) sorgulayabilir — canlı koda dokunmuyorlar ama repo hijyeni
  açısından not edilmeye değer.
