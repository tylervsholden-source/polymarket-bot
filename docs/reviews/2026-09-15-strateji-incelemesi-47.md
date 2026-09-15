# Günlük Strateji İncelemesi — 2026-09-15 (47. çalışma)

## Kapsam
Test suite tamamen yeşil (736 passed, 2 skipped) ve açık PR/backlog yoktu,
bu yüzden bu tur canlı-yol emir/fill takibinde henüz derinlemesine
incelenmemiş bir modüle odaklandı: `strategies/maker_engine.py`
(`MakerEngine` — iki taraflı GTC quote market-making motoru). Motor
`MAKER_ENABLED`/`MAKER_CAPITAL_PCT` ile varsayılan olarak kapalı (`.env`
belgelerinde de yer almıyor), ama kod tamamen bağlı: `agents/
orchestrator.py`'deki Phase A maker döngüsü her cycle `refresh_quotes()`
çağırıyor ve doğru guard setine (daily stop-loss, process-lock,
account-wide position-cap, `_is_live_trading()`) sahip — 43./44.
incelemelerin bond/maker guard eksikliklerini kapattığı yer burası.

## Bulgu (47.) — `_cancel_all_standing()` doldurulmuş (matched) emirleri kontrolsüz siliyor; `on_fill()`/envanter takibi hiç tetiklenmiyor

### Hata
`MakerEngine.refresh_quotes()` her cycle önce tüm standing (bekleyen)
emirleri iptal ediyor:

```python
async def _cancel_all_standing(self, client) -> int:
    cancelled = 0
    for order_id in list(self._standing.keys()):
        if client.cancel_order(order_id):
            cancelled += 1
        del self._standing[order_id]
    return cancelled
```

`client.cancel_order()` (`core/polymarket_client.py:723`) `False` dönmesini
iki tamamen farklı durum için kullanıyor: gerçek bir iptal hatası VE "CLOB
zaten bu emri doldurdu (matched), iptal edilecek bir şey kalmadı" — bir
GTC emrinin asıl amacı. Eski kod ikisini de aynı şekilde ele alıyordu:
`cancel_order()` `False` dönse de dönmese de `del self._standing[order_id]`
koşulsuz çalışıyordu. Yani gerçek USDC ile dolmuş bir emir, hiçbir iz
bırakmadan `_standing`'den siliniyordu — `on_fill()` (envanteri
(`self._inventory`) güncelleyen TEK yer, ve dolayısıyla
`check_paired_profit()`'in tek veri kaynağı) hiçbir zaman çağrılmıyordu.
`on_fill()` ve `check_paired_profit()` repo genelinde grep edildiğinde
hiçbir çağrı noktası yok — tamamen ölü kod.

### Somut senaryo
`MAKER_ENABLED=true` ve `MAKER_CAPITAL_PCT>0` ayarlansa (bugün varsayılan
değil, ama motor bağlı ve çalışmaya hazır): bir cycle'da YES tarafına
$4.00'lık bir GTC quote yerleştiriliyor (`_quote_market()`), `_standing`'e
ekleniyor. Sonraki cycle'da (`refresh_quotes()` her cycle çağrılıyor) bu
emir piyasada zaten dolmuş (matched) durumda. `_cancel_all_standing()`
`client.cancel_order(order_id)` çağırıyor → CLOB "iptal edilecek bir şey
yok" diyerek `False` dönüyor → eski kod bunu direkt siliyor, `on_fill()`
çağrılmıyor. Sonuç: gerçek $4.00 harcanmış ama `MakerEngine._inventory`
hiçbir zaman güncellenmiyor, `check_paired_profit()` asla bu fill'i
göremiyor (iki taraf da dolsa bile "guaranteed profit" hiç tespit
edilemiyor), ve motorun kendi iç muhasebesi harcanan sermayeyi tamamen
kaybediyor.

Not: bu, `position_manager`'a (capital/position-cap/stop-loss guard'ları)
hâlâ hiç bağlı olmayan ayrı ve daha büyük bir mimari eksikliğin bir
parçası — `MakerEngine.on_fill()` şu an sadece kendi `_inventory`
sözlüğünü güncelliyor, `position_manager.add_position()` gibi paylaşılan
muhasebeye yazmıyor. Bunu düzgün kapatmak, aynı `market_id` altında YES ve
NO için eşzamanlı iki pozisyonu (mevcut `positions` sözlüğü tek anahtar
başına tek pozisyon varsayıyor) ve `update_positions()`'ın gerçek
`condition_id` ile piyasa çözümlemesini nasıl yapacağını gerektiren ayrı
bir tasarım kararı — 44. incelemenin bond/maker guard PR'ında bilerek
"kapsam dışı, ayrı bir inceleme gerekiyor" diye bırakıldığı nokta tam
burası, ve bugün de kapsam dışı bırakıldı (bkz. Sonuç). Bu turun düzeltmesi
yalnızca `MakerEngine`'in kendi kapalı-devre envanter takibini onarıyor.

### Düzeltme
`strategies/maker_engine.py::_cancel_all_standing()` — `cancel_order()`
`False` döndüğünde, silmeden önce `client.get_order_status()` ile gerçek
durum sorgulanıyor (aynı fill-tespit deseni `PositionManager.
_check_order_filled()`'da zaten kullanılıyor: `status in ("MATCHED",
"FILLED")` veya `size_matched > 0`). Gerçek bir fill tespit edilirse
`on_fill()` çağrılıyor (envanter güncelleniyor); değilse (gerçekten hâlâ
live/iptal edilmiş) sadece siliniyor.

### Test
`tests/test_maker_cancel_swallows_fill.py` (3 test, yeni dosya):
1. `test_cancel_all_standing_records_fill_instead_of_dropping_it` —
   `cancel_order()` `False`, `get_order_status()` `{"status": "MATCHED",
   "size_matched": "10.0"}` dönüyor; düzeltme öncesi kaynakla **FAILED**
   (`assert inv is not None` → `AssertionError: fill was discarded without
   updating inventory` — hata reprodüklendi), düzeltme sonrası **PASSED**.
2. `test_cancel_all_standing_drops_order_that_is_still_live` — gerçekten
   hâlâ live bir emrin (status LIVE, size_matched 0) envanteri
   etkilemediğini doğruluyor (fantom fill üretilmiyor).
3. `test_cancel_all_standing_true_cancel_does_not_touch_inventory` —
   gerçek bir iptalin (`cancel_order()` → `True`) `get_order_status()`'u
   hiç çağırmadığını ve envanteri değiştirmediğini doğruluyor.

Tam suite: `pytest tests/ -q` → **739 passed, 2 skipped** (736 taban + bu
turun 3 yeni testi, sıfır regresyon).

`data/autonomous_state.json` (test suite'in yan etkisi) commit öncesi eski
hâline döndürüldü.

## Sonuç
`MakerEngine._cancel_all_standing()` artık dolmuş emirleri sessizce
silmiyor; `on_fill()`/`_inventory`/`check_paired_profit()` zinciri gerçek
fill verisiyle çalışabilir hale geldi. Motor hâlâ varsayılan olarak kapalı
(`MAKER_ENABLED=false`, `MAKER_CAPITAL_PCT=0.00`) — bu düzeltme bugünkü
canlı riski değiştirmiyor, ileride açılırsa sessiz veri kaybını önlüyor.

Sıradaki tur için öneri: `MakerEngine`'in envanterini `position_manager`'a
(pool/strategy="maker", `open_position_count()`, `locked_capital()`, daily
stop-loss) bağlamak hâlâ açık — bunun için (a) aynı `market_id` altında
YES+NO'yu eşzamanlı tutabilecek bir pozisyon anahtarlama şeması (örn.
`positions` sözlüğüne `condition_id` alanı eklenip `update_positions()`'ın
piyasa çözümlemesinde dict-key yerine bu alanı kullanması) ve (b)
`refresh_quotes()`'un her cycle'da TÜM standing emirleri iptal edip yeniden
kurma davranışının bu fill-reconciliation ile nasıl uyumlu çalışacağının
tasarlanması gerekiyor — tek oturumda aceleye getirilecek bir değişiklik
değil, gerçek sermaye muhasebesi söz konusu.
