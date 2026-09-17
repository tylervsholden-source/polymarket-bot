# Günlük Strateji İncelemesi — 2026-09-17 (72. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = bu branch = `c12cc87` (#127 sonrası, 71.
inceleme + LatencyArbEngine fix dahil). Açık PR yoktu. Baseline test:
`pytest tests/ -q` → 827 passed, 2 skipped.

## Bu turda yapılanlar
1. Bağımsız, taze bir gözden geçirme ajanı ile canlı yol tekrar tarandı:
   `agents/orchestrator.py`, `strategies/arbitrage_engine.py`,
   `core/position_manager.py`, `agents/autonomous_engine.py`,
   `agents/subagents/*`, `agents/resilience.py`, `agents/trade_analyzer.py`,
   maker/bond/latency-arb motorları, `core/polymarket_client.py`.
2. **Yeni bir hata bulundu:** `strategies/maker_engine.py`,
   `MakerEngine._cancel_all_standing()`. 47. inceleme, `cancel_order()`
   `False` döndüğünde (emir zaten CLOB tarafından eşleşmiş) gerçek durumu
   `get_order_status()` ile kontrol edip dolan miktarı `on_fill()` ile
   kaydetmeyi düzeltmişti. Ama `True` dönen dal (cancel başarılı) hiç
   dokunulmamış kalmıştı — ve `cancel_order() == True` aslında bir GTC
   emrinin **kısmi dolduktan sonra** kalan miktarının iptal edilmesinin
   normal/başarılı sonucudur, "hiç dolmadı" anlamına gelmez. Eski kod bu
   dalda emri iz bırakmadan siliyordu: `on_fill()` (tek `self._inventory`
   güncelleyici, `get_committed_capital()` ve `check_paired_profit()`'i
   besliyor) hiç çağrılmıyordu.
   - Somut etki: 25 adet MAKER_YES @ $0.40 emrinden 10 adedi ($4 gerçek
     USDC) bir sonraki `refresh_quotes()` döngüsünde kalan 15 adet iptal
     edilmeden önce dolar. Cancel başarılı olur (`True`) → eski kod emri
     sessizce siler → `get_committed_capital()` bu $4'ü hiç görmez →
     `orchestrator.py`'deki `pool_available("maker") - get_committed_capital()`
     bu parayı hâlâ boş sanıp bir sonraki döngüde tekrar taahhüt eder
     (tekrarlayan double-spend riski) → `check_paired_profit()` bu
     pozisyonu asla realize edemez.
   - Mevcut bir test (`test_cancel_all_standing_true_cancel_does_not_touch_inventory`)
     bu hatalı davranışı doğru diye assert ediyordu
     (`get_order_status.assert_not_called()`).
   - Not: `MAKER_ENABLED=false` varsayılan olduğu için bugün canlı risk
     yok, ama desteklenen, belgelenmiş bir özellik ve incelemenin
     kapsamında.
3. **Düzeltme:** Her iki dal (cancel başarılı/başarısız) artık emri
   silmeden önce `client.get_order_status()` ile gerçek durumu kontrol
   ediyor; `size_matched > 0` her durumda `on_fill()` ile kaydediliyor.
4. **Test:** `tests/test_maker_cancel_swallows_fill.py` güncellendi —
   eski "true cancel hiç dolmadı" testi gerçek no-fill senaryosuna
   yeniden adlandırıldı, hatayı kanıtlayan yeni bir test eklendi
   (`test_cancel_all_standing_true_cancel_of_partial_fill_records_fill`:
   25 adetten 10'u dolu, cancel başarılı, inventory artık doğru şekilde
   `yes_shares=10.0, yes_cost=$4.00` gösteriyor).
5. Doğrulama: `python3 -m pytest tests/ -q` → **828 passed, 2 skipped**
   (827'den +1 yeni test, regresyon yok). Diff bağımsız olarak da
   incelendi: `on_fill()` zaten `self._standing.pop(order_id, None)`
   yaptığı için silme mantığında çifte silme/kaybolan emir riski yok.

## Sonuç
- `strategies/maker_engine.py` içindeki 47. incelemenin kapsamadığı
  ikiz dal düzeltildi; maker motoru artık her iki cancel sonucunda da
  gerçek dolumu kaydediyor.
- Etkisi bugün `MAKER_ENABLED=false` olduğu için inert, ama gelecekte
  maker motoru açılırsa sessiz sermaye kaybını önlüyor.
- Açık PR kalmadı, bu turun değişikliği PR olarak açılacak.
