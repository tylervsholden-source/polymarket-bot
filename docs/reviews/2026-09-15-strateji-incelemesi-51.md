# Günlük Strateji İncelemesi — 2026-09-15 (51. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Bu turda yapılanlar

### 1. Yinelenen 50. tur PR'ları çözüldü (#83 vs #84)
Aynı anda çalışan iki ayrı 50. tur oturumu, 49. turun bıraktığı Monte Carlo
ödeme formülü bulgusunu birbirinden habersiz aynı anda düzeltip iki ayrı PR
açmış (#83, #84) — ikisi de `strategies/monte_carlo.py`/`arbitrage_engine.py`
üzerinde çakışıyordu. Her iki diff de okundu, her iki branch de lokal olarak
test edildi:

- **#83**: Ödeme formülünü düzeltti (`bet * b`, `b=(1/price)-1`) ama
  `win_prob = min(0.90, 0.50 + net_edge/2.0)` satırını DEĞİŞTİRMEDİ — yani
  win_prob hâlâ fiyattan bağımsız, ödeme fiyatla ölçekleniyor. Bu iki taraf
  arasında ekonomik tutarsızlık bırakıyor (düşük fiyat + aynı edge = aynı
  win_prob ama çok daha büyük ödeme). 2 whale_tracker testi "pre-existing
  flake" olarak bırakılmış (düzeltilmemiş).
- **#84**: `win_prob = clip(price + net_edge, 0.03, 0.97)` — `edge = true_prob
  - price - costs` tanımından türetilmiş, ödeme tarafıyla tutarlı. Ayrıca
  `MC_GATE_ENFORCE` env değişkeniyle gerçek engelleme moduna geçiş için bir
  anahtar ekliyor, VE flaky whale_tracker testini gerçekten düzeltiyor
  (sabit `"...T12:00:00Z"` timestamp yerine çalışma anına göreli).

#84 seçildi ve merge edildi (kendi sandbox'ımda 744 passed / 0 failed
doğrulandı), #83 "superseded" notuyla kapatıldı.

### 2. Yeni bulgu — GTC timeout-cancel sonrası kısmi doluş sessizce siliniyor
`core/polymarket_client.py::place_order()`, satır ~510-542: bir GTC emri
45sn içinde %95 doluş eşiğine ulaşmazsa iptal ediliyor. İptal sonrası
doğrulama adımı (`verify = self._clob.get_order(order_id)`) sadece
`v_status == "matched"` mı diye bakıyordu; `"cancelled"/"expired"/""` durumunda
`verify.get("size_matched")` HİÇ kontrol edilmiyordu.

Sorun: cancel yalnızca emrin doldurulmamış kalan kısmını iptal eder — cancel
öncesi eşleşen pay gerçek, geri alınamaz USDC harcamasıdır. Somut senaryo:
hedef 9.61 pay @ 0.52, kitap 45sn boyunca sadece 4.00 pay (%41.6, %95
eşiğinin altında) eşleştiriyor, timeout olup iptal ediliyor. Doğrulama
`{"status": "cancelled", "size_matched": "4.00"}` döndürüyor — eski kod bunu
"hiçbir şey dolmadı" sayıp `place_order()` `None` döndürüyordu.
`agents/orchestrator.py` `None` dönüşünde `position_manager.add_position()`
hiç çağrılmıyor — ama gerçekte ~$2.08 harcanıp 4 gerçek YES payı alınmış,
bu paylar hiçbir zaman izlenmiyor, sonuç takip edilmiyor, win/loss'a
girmiyor; sonraki `_sync_real_balance()` döngüsü bu kaybı belirli bir
piyasaya değil, açıklanamayan bakiye kaymasına yazıyor.

**Düzeltme**: iptal sonrası doğrulamada `size_matched > 0` kontrolü eklendi
— pozitifse döngü içindeki %95 kabul yoluyla aynı şekilde gerçek kısmi
doluş olarak kabul ediliyor (`filled=True`, `filled_size=size_matched`,
`status="matched"`), böylece pozisyon normal şekilde kaydediliyor.

**Test**: `tests/test_gtc_cancel_partial_fill_not_discarded.py` (yeni) —
9 poll turunda hep %41.6 doluş raporlayan, sonra iptal sonrası
`{"status": "cancelled", "size_matched": "4.00"}` dönen bir GTC senaryosu
simüle ediyor; düzeltme öncesi `order is None` (bug), sonrası
`order["amount"] == 4.00 * 0.52` (doğru) olduğunu doğruluyor.

### Doğrulama
`python3 -m pytest tests/` → **745 passed, 2 skipped, 0 failed** (yeni
test dahil; önceki 744'ten +1).

## Sonuç
- #83/#84 çakışması çözüldü, tek (daha doğru) düzeltme main'e alındı.
- Yeni bir gerçek para kaybı yolu (GTC iptal sonrası kısmi doluşun
  sessizce silinmesi) bulundu ve kapatıldı.

## Sıradaki tur için notlar
- `MC_GATE_SHADOW` loglarını birkaç canlı döngü boyunca izleyip
  `MC_GATE_ENFORCE=true` yapılıp yapılamayacağına karar ver.
- `data/trade_memory.json`'daki `CAPITAL_LOW` uyarısı hâlâ açık ($102.61,
  başlangıcın %21'i) — bu turun GTC kısmi-doluş düzeltmesi bilinmeyen
  bakiye kaymasının bir kısmını açıklayabilir, ama kök neden (sim-live WR
  farkı, CLAUDE.md'de not edilmiş) hâlâ araştırılmadı.
