# Günlük Strateji İncelemesi — 2026-09-14 (34. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum özeti
- Oturum `origin/main` (`eb170aa`, 31. çalışmanın sonucu — PR #57) üzerinde
  açıldı. Taban test suite: `pytest tests/ -q` → **672 passed, 2 skipped**.
- Aynı taban commit'ten dallanmış **iki paralel PR açık ve unmerged**:
  - **PR #58** (`claude/brave-faraday-myvsz9`) —
    `core/polymarket_client.py::get_real_balance()`'ın hata yollarında
    gerçek $0 bakiyeden ayırt edilemeyen `0.0` döndürmesi.
  - **PR #59** (`claude/brave-faraday-jejt5w`) —
    `agents/orchestrator.py::_sync_real_balance()`'ın süresi geçmiş ama
    henüz resolve olmamış pozisyonları `locked` sermayeden hariç tutması.

  Her ikisinin de diff'i GitHub'dan çekilip satır satır incelendi; bu turun
  bulgusu bilinçli olarak o iki fonksiyonun dışında, üçüncü bir dosya
  bölgesinde seçildi. Çakışma yok: PR #58 `polymarket_client.py`'nin bakiye
  fonksiyonuna, PR #59 `orchestrator.py`'nin `locked` hesabına, bu PR
  `core/position_manager.py::update_positions()`'ın canlı NO değerleme
  fallback'ine dokunuyor.
- Önceki 33 turda dokunulan alanlar (bkz. `git log --oneline | grep -i
  "daily review"`) kapsam dışı bırakılarak, bugüne kadar hiç ayrıntılı
  incelenmemiş canlı-yol dosyaları tarandı: `control_plane/live_gate.py`,
  `control_plane/approval_queue.py`, `control_plane/reentry_guard.py`,
  `strategies/kelly_criterion.py`, `strategies/edge_model.py`,
  `strategies/monte_carlo.py`, `strategies/orderbook_analyzer.py`,
  `strategies/ml_classifier.py`, `strategies/walk_forward.py`,
  `agents/subagents/coordinator.py`, `agents/subagents/signal_agent_v2.py`,
  `agents/subagents/research_agent.py`, `agents/latency_arb.py`,
  `agents/binance_feed.py::get_market_regime()` ve
  `core/position_manager.py`'nin kalan resolution/değerleme mantığı.

## ⚠️ Bulgu: hiç kotasyonu olmayan NO pozisyonu 0.00'a değerlenip TAM KAYIP olarak kapanıyor

### Hata
`core/position_manager.py::PositionManager.update_positions()`:

```python
yes_ask = float(market.get("best_ask", 0) or 0) or (1.0 - float(market.get("best_bid", 0) or 0))
...
if outcome == "NO":
    no_tid = pos.get("token_id") or (market or {}).get("no_token_id")
    no_book = client.get_orderbook(no_tid) if no_tid else None
    if no_book and no_book["best_bid"] > 0:
        current_price = no_book["best_bid"]
    else:
        current_price = round(1.0 - yes_ask, 4) if yes_ask > 0 else pos["entry_price"]
else:
    current_price = float(market.get("best_bid", pos["entry_price"]) or pos["entry_price"])
```

`core/polymarket_client.py::get_market()` `best_ask`/`best_bid` alanlarını
**her zaman** float'a normalize ediyor; Gamma hiç kotasyon döndürmediğinde
ikisi de `0.0` oluyor:

```python
data["best_ask"] = float(data.get("bestAsk") or data.get("best_ask") or 0)
data["best_bid"] = float(data.get("bestBid") or data.get("best_bid") or 0)
```

İkisi de 0 olduğunda `or` zinciri `0 or (1.0 - 0)` = **1.0** üretiyor — yani
"YES 100 sent" anlamına gelen, hiçbir gerçek kotasyona dayanmayan uydurma bir
fiyat. NO dalı bunu `round(1.0 - 1.0, 4)` = **0.00**'a çeviriyor. Dalın kendi
"fiyat bilinmiyor" koruması (`else pos["entry_price"]` — YES dalının zaten
yaptığı şeyin aynısı) hiç çalışmıyor, çünkü `yes_ask > 0` koşulu sağlanıyor:
`yes_ask` 1.0.

### Somut senaryo
1. Bot `entry_price=0.45` ile $4'lık bir NO pozisyonu açıyor.
2. Market kapanışa yaklaşıyor. CLOB NO orderbook'u ya boş (bid tarafı
   temizlenmiş — 5dk'lık kripto marketlerinde kapanışa yakın rutin), ya da
   `get_orderbook()` `None` dönüyor (CLOB credential yok — `docs/api_guide.md`
   resmen "API key olmadan da çalışır" diyor — veya geçici bir CLOB hatası).
   Gamma da bu market için `bestAsk`/`bestBid` vermiyor (ikisi de 0).
3. `yes_ask = 1.0` → `current_price = 0.00`, `unrealized_pnl = -4.00`
   **diske yazılıyor**. Pozisyon hâlâ açıkken ve muhtemelen kazanıyorken
   dashboard'da tamamen değersiz görünüyor.
4. CLOB resolution 45 dakikayı aşıyor (bu dosyanın `WAITING_RESOLUTION` /
   `STALE_UNRESOLVED` / `FORCE_CLOSE_TIMEOUT` mantığı tam da bu durumu
   yönetmek için yazılmış, yani rutin bir senaryo). Timeout heuristic aynı
   kaydedilmiş değeri okuyor:

   ```python
   _last_price = pos.get("current_price")
   ...
   elif _last_price is not None and _last_price < 0.20:
       logger.warning(f"TIMEOUT_HEURISTIC_LOSS ... last_price={_last_price:.2f}<0.20 → treating as LOSS")
       self._close_position(market_id, 0.0)
   ```

   `0.00 < 0.20` → pozisyon **0.0 fiyattan kapatılıyor**: `pnl = -$4.00`,
   `result = "LOSS"`. Oysa hemen altındaki "fiyat belirsiz → NEUTRAL kapat"
   dalı (`pnl = 0`) tam olarak bu durum için var.

Bu hayalet kayıp doğrudan para muhasebesine giriyor:
`data["capital"] += pnl` ($100 → $96), `data["daily"]["pnl"] += pnl`
(CLAUDE.md'nin taviz verilmeyen günlük -%15 stop-loss'unun payı) ve
`result="LOSS"` kaydı; bu kayıt sonra `strategies/kelly_criterion.py::
update_streak()`'in streak çarpanını, `AutonomousDecisionEngine`'in
loss-streak savunmasını, `WalkForwardValidator`'ın test WR'ını ve ML
classifier'ın eğitim etiketlerini besliyor.

Karşılaştırma için: aynı fonksiyondaki YES dalı bu tuzağa düşmüyor —
`float(market.get("best_bid", pos["entry_price"]) or pos["entry_price"])`
kotasyon yoksa `entry_price`'a düşüyor. Asimetri tamamen `yes_ask`
ifadesindeki `or` zincirinden kaynaklanıyor.

`git blame` ile orijinal `feat` commit'ine (`9b5fd52`) kadar izlendi.
**Bu, 14. çalışmadaki (#33, `92a4589`) hatanın aynısı DEĞİL**: o tur NO
orderbook *lookup*'ını `pos["token_id"]` kullanacak şekilde düzeltmişti; bu
tur o lookup meşru olarak boş döndüğünde devreye giren fallback'i
düzeltiyor. Önceki 33 incelemenin hiçbiri (ve açık PR #58/#59'un da) bu
satıra dokunmamış.

### Düzeltme
`1 - best_bid` sentetik yedeği yalnızca **gerçek bir YES bid'i varken**
kullanılıyor; yoksa `yes_ask` 0'da bırakılıyor ve mevcut
`else pos["entry_price"]` "fiyat bilinmiyor" koruması — YES dalıyla
simetrik olarak — nihayet çalışıyor:

```python
_yes_ask_raw = float(market.get("best_ask", 0) or 0)
_yes_bid_raw = float(market.get("best_bid", 0) or 0)
yes_ask = _yes_ask_raw or (1.0 - _yes_bid_raw if _yes_bid_raw > 0 else 0.0)
```

Başka hiçbir davranış değiştirilmedi: `best_ask > 0` ise davranış aynı,
`best_ask = 0` ama `best_bid > 0` ise davranış aynı, gerçek NO orderbook'u
varsa (14. çalışmanın fix'i) hâlâ birincil kaynak. Sadece "hiçbir kotasyon
yok" durumu değişti.

`tests/test_no_valuation_no_quote_phantom_loss.py` eklendi (5 test):
1. Kök neden: hiç kotasyon yokken NO pozisyonunun `current_price`'ı
   `entry_price` mi (0.00 değil), `unrealized_pnl` 0 mı.
2. Asıl para etkisi (uçtan uca, iki döngü): döngü 1 kotasyonsuz değerleme,
   döngü 2 market kapalı + resolution alınamıyor + pozisyon 60dk eski →
   NEUTRAL (pnl 0) kapanmalı, LOSS (-$4) değil; `capital` $100'de,
   `daily.pnl` 0'da kalmalı.
3. Non-regresyon: gerçek bir YES bid'i (0.55) varsa sentetik NO fiyatı hâlâ
   0.55 hesaplanıyor.
4. Non-regresyon: canlı NO orderbook'u varsa (14. çalışma, #33) hâlâ o
   kullanılıyor (0.62).
5. Non-regresyon: gerçek bir Gamma `best_ask` (0.02) hâlâ sentetik NO
   fiyatını sürüyor (0.98).

## Doğrulama
- Fix öncesi (sadece `core/position_manager.py` geri alınmış, testler aynen
  duruyor): `pytest tests/test_no_valuation_no_quote_phantom_loss.py -q` →
  **2 failed, 3 passed**:
  ```
  E  AssertionError: NO position valued at 0.0 with NO price source available
     — `0 or (1.0 - 0)` fabricated yes_ask=1.0, so 1-yes_ask=0.00 marked an
     open position as totally worthless.
  E  assert 0.0 == 0.45 ± 4.5e-07

  E  AssertionError: position closed as LOSS (pnl=-4.0) — an unresolvable
     position whose price was never actually quoted must be force-closed
     NEUTRAL, not booked as a full loss off a fabricated current_price of 0.00.
  E  assert 'LOSS' == 'NEUTRAL'
  ```
  Log çıktısı senaryoyu birebir üretiyor:
  ```
  WARNING | core.position_manager:update_positions:527 -
    TIMEOUT_HEURISTIC_LOSS (60dk): Bitcoin Up or Down — last_price=0.00<0.20 → treating as LOSS
  INFO    | core.position_manager:_close_position:740 -
    Pozisyon kapatıldı: Bitcoin Up or Down | LOSS | PnL: $-4.00 | VERIFIED_WR: 0W/1L = 0.0% (1 trades)
  ```
- Fix sonrası: aynı dosya → **5/5 pass**.
- Tam suite (fix sonrası): `pytest tests/ -q` → **677 passed, 2 skipped**
  (672'den 677'ye — sadece bu turun 5 yeni testi, sıfır regresyon).
- `git diff core/position_manager.py` → tek ifadelik değişiklik (+ açıklayıcı
  yorum); NO orderbook lookup'ı, YES dalı, resolution mantığı, timeout
  heuristic'i dokunulmadı.
- Test çalıştırmalarının yan etkisi olan `data/autonomous_state.json`
  commit öncesi eski haline döndürüldü.

## İncelenip hata bulunamayan alanlar (bu turda)
- `control_plane/live_gate.py` (11-nokta gate), `control_plane/approval_queue.py`
  state machine, `control_plane/reentry_guard.py` cooldown kalıcılığı.
- `strategies/kelly_criterion.py` (%20 pozisyon tavanı, streak çarpanı),
  `strategies/edge_model.py` maliyet hesabı, `agents/orchestrator.py::
  compute_bet_size()` bandı.
- `strategies/monte_carlo.py`, `strategies/orderbook_analyzer.py` —
  `_estimate_slippage()`'ta birim karışıklığı var ama `slippage_5/10`
  hiçbir yerde tüketilmiyor; `agents/subagents/research_agent.py::
  _fetch_smart_trader()` `confidence`/`aligned`/`total` anahtarlarını yanlış
  okuyor ama bu alanların da finansal tüketicisi yok. İkisi de finansal
  etkisi olmadığı için bu turun konusu yapılmadı, not düşüldü.
- `agents/subagents/coordinator.py` REDUCE uygulaması, `signal_agent_v2.py`
  confluence/risk-flag hesabı, `agents/binance_feed.py::get_market_regime()`.

## Sonuç
34. çalışma, canlı P&L muhasebesinin merkezindeki `update_positions()`'ta,
hiçbir fiyat kaynağı bulunamadığında NO pozisyonlarını `current_price=0.00`'a
değerleyen bir `or` zinciri hatası buldu. Bu değer diske yazılıyor ve 45
dakikalık resolution timeout'unda pozisyonu NEUTRAL yerine **tam kayıp**
olarak kapatarak `capital`'i, günlük -%15 stop-loss'un PnL kovasını ve tüm
WIN/LOSS türevli mekanizmaları (Kelly streak, loss-streak savunması,
walk-forward WR, ML etiketleri) hayalet bir kayıpla zehirliyordu. Tek
ifadelik minimal bir düzeltmeyle — sentetik yedeği yalnızca gerçek bir YES
bid'i varken kullanmak, aksi halde zaten var olan `entry_price` korumasına
düşmek — kapatıldı, beş regresyon testiyle kilitlendi. PR #58 ve #59 bağımsız
olarak beklemeye devam ediyor; üç fix farklı fonksiyonlarda olduğu için
merge sırasında çakışma yok.
