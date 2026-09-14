# Günlük Strateji İncelemesi — 2026-09-14 (35. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum özeti
- Oturum `origin/main` (`eb170aa`, 31. çalışmanın sonucu — PR #57) üzerinde
  açıldı. Taban test suite: `pytest tests/ -q` → **672 passed, 2 skipped**.
- Aynı taban commit'ten dallanmış **üç paralel PR açık ve unmerged**:
  - **PR #58** (32. çalışma) — `core/polymarket_client.py::get_real_balance()`
    hata sentineli.
  - **PR #59** (33. çalışma) — `agents/orchestrator.py::_sync_real_balance()`
    süresi geçmiş pozisyonları locked'dan hariç tutuyordu.
  - **PR #60** (34. çalışma) — `core/position_manager.py::update_positions()`
    NO değerleme fallback'i kotasyon yokken 0.00 üretiyordu.

  Üçünün de diff'i incelendi; bu turun bulgusu bilinçli olarak dördüncü,
  bağımsız bir dosyada seçildi — çakışma yok.
- Bulgu, önceki 34 incelemenin kapsamadığı ama `agents/orchestrator.py`'nin
  gerçekten canlı döngüye kablolu olan bir modülünde: `agents/
  top_trader_signal.py` (`TopTraderTracker`). `docs/architecture.md`
  bu modülü listelemiyor ama orchestrator onu gerçekten import edip
  `ArbitrageEngine`'e enjekte ediyor (`agents/orchestrator.py:20,133,139,347,379`)
  ve `strategies/arbitrage_engine.py:636-648`'te `bayesian_prob`'a doğrudan
  boost olarak ekliyor.

## Bulgu: Top Trader Copy Signal, yanlış JSON alan adı yüzünden hiçbir zaman tek bir trade'i bile eşleştiremiyordu

### Hata
`agents/top_trader_signal.py::TopTraderTracker._process_trades()`:

```python
for t in trades:
    cid = t.get("market", t.get("condition_id", ""))
    if not cid:
        continue

    side = t.get("side", "").upper()
    size = float(t.get("size", 0))

    if side in ("BUY", "YES", "1"):
        market_trades[cid]["yes_vol"] += size
    elif side in ("SELL", "NO", "0"):
        market_trades[cid]["no_vol"] += size
```

`refresh()` bu veriyi `https://data-api.polymarket.com/trades`'ten çekiyor.
Bu endpoint market id'sini **`"conditionId"`** (camelCase) olarak veriyor —
asla `"market"` veya `"condition_id"` olarak değil. Aynı endpoint'i kullanan
`agents/copytrade.py:152` (`t.get("conditionId", "")`) bunu doğru okuyarak
gerçek şemayı doğruluyor. Sonuç: `cid` her zaman `""`, `if not cid: continue`
her trade'de tetikleniyor, `market_trades` (dolayısıyla `self._cache`)
**hiçbir zaman tek bir girdi bile almıyor**. `get_signal()`/`get_boost()`
bu yüzden her zaman `NEUTRAL`/`0.0` default'unu dönüyor.

Bu, `strategies/arbitrage_engine.py:638-648`'te gerçek paraya giriyor:

```python
_tt_boost = self.top_trader.get_boost(market.get("condition_id", ""))
if _tt_boost and abs(_tt_boost) > 0.005:
    bayesian_prob = max(0.05, min(0.95, bayesian_prob + _tt_boost))
```

`bayesian_prob` doğrudan `edge = prob - price`, yön seçimi ve Kelly
boyutlandırmasını besliyor. Top trader'lar bir marketde tek yönlü ağırlıklı
olsa bile bu ±0.03'lük katkı hiçbir zaman gelmiyordu — sınırda (min-edge
eşiğine yakın) bir sinyalin geçip geçmemesini belirleyebilecek bir katkı.

### İkinci, birleşik hata (aynı döngüde)
`side` (BUY/SELL — pozisyon açma/kapama) tek başına outcome (YES/NO) gibi
okunuyordu. Gerçek outcome ayrı bir `"outcome"` alanında (`"Up"`/`"Down"`
gibi) ve tamamen görmezden geliniyordu. `copytrade.py` aynı endpoint için
`side` VE `outcome`'u birlikte kullanarak bunu doğru yapıyor. Sadece `cid`
düzeltilip bu ikinci hata düzeltilmeseydi, tracker devreye girip **sistematik
olarak ters işaretli** bir boost üretmeye başlayacaktı (ör. büyük bir NO/Down
token BUY'ı yanlışlıkla bullish `yes_vol` sayılacaktı) — bu, bugünkü sessiz
no-op'tan daha kötü bir davranış olurdu.

`git blame` orijinal `feat` commit'ine (`9b5fd52`) kadar izlendi; önceki 34
günlük incelemenin hiçbiri (ve açık PR #58/#59/#60'ın da) bu dosyaya
dokunmamış.

### Düzeltme
`copytrade.py`'nin aynı endpoint için zaten doğru olan deseni birebir
uygulandı: `cid` artık `"conditionId"` okuyor; `side` artık `outcome` ile
birlikte değerlendiriliyor (BUY+YES/UP veya SELL+NO/DOWN → bullish;
SELL+YES/UP veya BUY+NO/DOWN → bearish).

`tests/test_top_trader_signal_field_mismatch.py` eklendi (5 test):
1. Kök neden: gerçek `conditionId` alanlı trade'ler artık cache'e giriyor mu.
2. Non-regresyon: eski (yanlış) `market`/`condition_id` anahtarları hâlâ
   gerçek şema değil — kazara "destekleniyor" gibi görünmüyor.
3. `side` tek başına yön belirlemiyor — SELL+YES bearish sayılıyor mu.
4. BUY+DOWN bearish sayılıyor mu (eski hatanın üreteceği ters işaret).
5. Up/Down outcome etiketleri (kripto marketlerin gerçek formatı) destekleniyor mu.

## Doğrulama
- Fix öncesi (`git stash` ile sadece kaynak dosya fix'i geri alınmış):
  `pytest tests/test_top_trader_signal_field_mismatch.py -v` →
  **5 failed** — `AssertionError: assert 'NEUTRAL' == 'BULLISH'` (cache hiç
  dolmuyor), tam olarak açıklanan senaryoyu üretiyor.
- Fix sonrası: aynı dosya → **5/5 pass**.
- Tam suite (fix sonrası): `pytest tests/ -q` → **677 passed, 2 skipped**
  (672'den 677'ye — sadece bu turun 5 yeni testi, sıfır regresyon).
- `git diff agents/top_trader_signal.py` → tek döngü bloğu değişikliği
  (cid anahtarı + side/outcome birleşimi) + açıklayıcı yorum; `get_signal()`,
  `get_boost()`, refresh mantığı, konsensüs eşikleri dokunulmadı.
- Test çalıştırmalarının yan etkisi olan `data/autonomous_state.json`
  commit öncesi eski haline döndürüldü.

## İncelenip hata bulunamayan alanlar (bu turda)
- `agents/kalshi_arb.py` (doğru alan kullanımı, gerçek tüketici üzerinden
  doğrulandı), `agents/whale_tracker.py` (alan kullanımı gerçek data-api
  şemasıyla eşleşiyor), `strategies/walk_forward.py` (NEUTRAL-as-LOSS fix'i
  hâlâ yerinde), `control_plane/expiry_guard.py`, `control_plane/
  entry_window_guard.py`, `control_plane/live_gate.py` kablolaması,
  `agents/subagents/base_agent.py`, `agents/subagents/orderflow_agent.py`
  ve `coordinator.py`/`research_agent.py` üzerinden kablolanması,
  `agents/trade_analyzer.py` (öneriler/pattern'ler sadece loglanıyor, gerçek
  tüketicisi yok), `core/position_manager.py` (PR #60'ın kapsadığı NO
  değerleme dışındaki tüm yollar), `core/approval_queue.py` (saf re-export
  shim'i).
- Düşük öncelikli, finansal tüketicisi olmadığı için raporlanmayan:
  `agents/subagents/research_agent.py::_extract_global_indices()`,
  `MarketIndexWatcher` üzerinde var olmayan `.get()` metodunu çağırıp
  `AttributeError` fırlatıyor (yutuluyor) — ama `global_indices`/
  `btc_dominance`'ın hiçbir downstream tüketicisi yok.
- `strategies/maker_engine.py` / `strategies/bond_scanner.py` derinlemesine
  incelenmedi: `MAKER_ENABLED`/`BOND_ENABLED` env flag'leri default
  `"false"` ve bu repoda `.env` override'ı yok, yani şu an canlı döngüden
  hiç erişilebilir değiller.

## Sonuç
35. çalışma, `agents/orchestrator.py`'nin gerçekten kablolu olduğu ama
`docs/architecture.md`'de belgelenmemiş bir modülde (`TopTraderTracker`)
kritik bir hata buldu: yanlış JSON alan adı yüzünden Top Trader Copy Signal
hiçbir zaman tek bir trade'i bile market'e eşleştiremiyordu, bu da sinyalin
canlı `bayesian_prob` hesabına asla gerçek bir katkı yapmamasına yol
açıyordu — belgelenen bir sinyal kaynağının sessizce tamamen devre dışı
kalması. Aynı endpoint'i doğru kullanan `copytrade.py`'nin deseni birebir
uygulanarak (alan adı + side/outcome birleşimi) minimal bir değişiklikle
düzeltildi, beş regresyon testiyle kilitlendi. PR #58/#59/#60 bağımsız
olarak beklemeye devam ediyor; dört fix farklı dosyalarda olduğu için
merge sırasında çakışma olmaz.
