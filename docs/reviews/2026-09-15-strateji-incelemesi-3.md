# Günlük Strateji İncelemesi — 2026-09-15 (41. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Kapsam
Önceki tur (konsolidasyon, bkz. `2026-09-15-strateji-incelemesi-consolidation.md`)
`main`'e 5 PR'lık bir birikimi merge ettikten sonra, henüz derinlemesine
incelenmemiş üç alanı bir sonraki tura bıraktı: `control_plane/entry_window_guard.py`
+ `reentry_guard.py`'nin gerçek-zamanlı davranışı, `strategies/monte_carlo.py`'deki
`net_edge*2.0` çift-sayımının canlı etkisi, ve `agents/subagents/research_agent.py`'nin
whale/smart-money birleştirme mantığı. Açık PR kuyruğu önce kontrol edildi — boş
(backlog birikmiyor). Üç alan tek tek okunarak tarandı.

## Bulgu — `WhaleTracker._analyze()` yalnızca `side`'a bakıyor, `outcome`'u hiç okumuyor

### Hata
`agents/whale_tracker.py::_analyze()` her trade için sadece `side` (BUY/SELL)
alanına bakıp yön belirliyordu:

```python
if side == "BUY":
    large_buys += 1
elif side == "SELL":
    large_sells += 1
```

`data-api.polymarket.com/trades` (bu sınıfın çektiği endpoint) her trade'de
`side`'ı **hangi outcome token'ının** alınıp satıldığını, `outcome`'u ise
**hangi tarafın** (bu botun işlediği crypto up/down marketlerinde "Up"/"Down")
işlem gördüğünü ayrı ayrı taşıyor (`artifacts/recent_trades.json`'daki gerçek
kayıtlarla doğrulandı: `{"side": "BUY", "outcome": "Down", ...}` gibi). Yani
"Down" tarafında $20K'lık bir BUY — bearish bir bahis — eski kodda `large_buys`
ve `smart_money_buys`'a yazılıyor, `direction="BULLISH"` üretiyordu: **whale
yönü NO/Down tarafında yoğunlaştığında tersine dönüyordu.**

Bu, `agents/smart_trader_tracker.py`'nin `outcome == "YES"` kontrolüyle zaten
doğru yaptığı ve `agents/top_trader_signal.py`'de 35. çalışmada aynı
data-api şeması için düzeltilmiş (bkz. `tests/test_top_trader_signal_field_mismatch.py`)
side-vs-outcome hatasının bir üçüncü tekrarı.

**Canlı etkisi:** `agents/orchestrator.py` gerçek `WhaleTracker`'ı
`AgentCoordinator`'a veriyor → `ResearchAgent` → `ResearchResult.get_market_context()`
→ `agents/subagents/signal_agent_v2.py`, burada `whale_direction`/
`smart_money_signal` doğrudan `confluence_score`'u (ağırlık 2) hareket
ettiriyor ve `WHALE_OPPOSITION` risk flag'ini set/unset ediyor — ikisi de
reviewer agent'a ve `AutonomousDecisionEngine`'in risk skoru/boyutlandırmasına
gidiyor. Tersine dönmüş bir whale sinyali, yanlış yönde güveni/boyutu
artırabilir ya da doğru yönlü bir trade'i yanlışlıkla "whale opposition"
olarak işaretleyip küçültebilir/engelleyebilir — gerçek sermaye ile.

### Düzeltme
`_analyze()`'e `top_trader_signal.py`'deki ile aynı side+outcome birleştirme
mantığı eklendi: `BUY`+`YES/UP` veya `SELL`+`NO/DOWN` → bullish trade;
`SELL`+`YES/UP` veya `BUY`+`NO/DOWN` → bearish trade. `large_buys/large_sells`
ve `smart_money_buys/smart_money_sells` artık bu düzeltilmiş sınıflandırmayı
kullanıyor.

### Test
`tests/test_whale_tracker_outcome_side_mismatch.py` (yeni) — BUY-of-Down'ın
BEARISH sayıldığını, SELL-of-Up'ın BEARISH sayıldığını, ve normal BUY-of-Up'ın
hâlâ BULLISH sayıldığını doğruluyor (düzeltme öncesi ilk iki test FAIL
veriyordu — BUY-of-Down `direction="BULLISH"`/`whale_alignment="BUY"`
üretiyordu).

## Diğer iki alan — bulgu yok
- `control_plane/entry_window_guard.py` / `reentry_guard.py`: zaman dilimi
  yönetimi `zoneinfo` ile DST-farkında, pencere matematiği ve cooldown
  persistence/expiry mantığı doğru. Tek gevşek uç:
  `is_recheck_after_approval` canlı çağrı yolundan (`orchestrator.py` →
  `check_live_gate`) hiçbir zaman `True` geçilmiyor, yani
  `APPROVAL_DELAY_PUSHED_OUT_OF_WINDOW` etiketi fiilen ölü — kozmetik (yanlış
  ret etiketi), gating hatası değil, çünkü `TOO_EARLY/TOO_LATE` her durumda
  doğru tetikleniyor.
- `strategies/monte_carlo.py`: `net_edge*2.0` çift-sayımının canlı etkisi yok
  olduğu doğrulandı. `ArbitrageEngine._maybe_run_monte_carlo()`'nun dönüş
  değeri tek çağrı yerinde (`strategies/arbitrage_engine.py:408`)
  kullanılmıyor; `_mc_result`/`.viable`'ı okuyan başka bir yer yok. Sadece
  `Viable=✅/❌` log satırını etkiliyor.

## Doğrulama
Tam test suite (ortam bu oturumda `pip install -r requirements.txt` ile
kuruldu): **716 passed, 2 skipped, 0 failed** (yeni 3 test dahil; önceki tur
713 passed, 2 skipped idi — fark tam olarak eklenen test sayısı).

## Sonuç
Whale/smart-money yön sinyali artık `outcome` tarafını doğru okuyor; bu,
canlı confluence skorunu ve `WHALE_OPPOSITION` risk bayrağını doğrudan
etkileyen üçüncü bağımsız side-vs-outcome düzeltmesi (bkz. 35. çalışma:
TopTraderTracker). Diğer iki alan temiz çıktı; bir sonraki tur için yeni bir
öneri yok — geniş odaklı bir tarama (önceki 40 turun dokunmadığı dosyalar)
önerilir.
