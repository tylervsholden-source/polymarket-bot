# Günlük Strateji İncelemesi — 2026-09-15 (49. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Bu turda yapılanlar — 3 bekleyen PR (#79, #80, #81) main'e alındı

Turun başında `main` hâlâ `27ff84b` (46./44. turdan sonraki merge) üzerindeydi
ve aynı gün içinde açılmış **3 ayrı, unmerged PR** bekliyordu — 44./consolidation
turlarının işaret ettiği "düzeltmeler PR kuyruğunda birikip main'e hiç
ulaşmıyor" riski tekrar oluşmuştu:

- **#79 (47. tur)** — `strategies/maker_engine.py::_cancel_all_standing()`
  `client.cancel_order()` `False` döndüğünde (gerçek hata VEYA "zaten
  matched, iptal edilecek bir şey yok" — ikisi ayırt edilmiyordu) emri
  kontrolsüz siliyordu; `on_fill()`/envanter zinciri hiç tetiklenmiyordu.
  Düzeltme: silmeden önce `client.get_order_status()` ile gerçek durum
  kontrolü (`PositionManager._check_order_filled()` ile aynı desen).
  `MAKER_ENABLED` varsayılan `false` olduğundan bugünkü canlı riski
  değiştirmiyor, ileride açılırsa sessiz veri kaybını önlüyor.
- **#80 (48. tur)** — `strategies/bond_scanner.py::_evaluate_market()` NO
  tarafı sentetik fiyatı `1.0 - yes_price` (paysız) kullanıyordu;
  `arbitrage_engine.py` aynı senaryo için zaten `+0.02` pay eklemişti
  (kendi FIX-3'ü: "was 1.0 - yes_price, too optimistic"). Aynı pay
  `bond_scanner.py`'ye de uygulandı. **Canlı etki:** `yes_price=0.03`
  gibi gerçekçi bir "NO neredeyse kesin" piyasasında, paysız tahmin
  (`0.97`) `BondScanner`'ın kendi kabul bandının (`0.93-0.97`) tam
  sınırında kalıp gerçek bir GTC alım emrine (`_bond_cycle()` →
  `client.place_passive_order`) yol açıyordu; padlı tahmin (`0.99`)
  `MAX_PRICE`'ı aşıp doğru şekilde reddediyor.
- **#81 (47. tur)** — `agents/binance_feed.py::get_signal()`, kendi
  hesapladığı `bb_squeeze`/`bb_breakout` alanlarını (46. turun
  `bullish_exhaustion` bulgusuyla birebir aynı şekil) dönen sözlüğe hiç
  koymuyordu. `arbitrage_engine.py` → `bayesian.py` zincirinde bu ikisi
  gerçek probability sinyalinin %5 ağırlıklı bir terimini besliyor;
  alanlar eksik olduğundan her zaman nötr fallback'e düşüyordu. Düzeltme:
  iki alan da diğerleri gibi forward edildi.

Üç PR de farklı dosyaya dokunuyordu (`maker_engine.py`, `bond_scanner.py`,
`binance_feed.py`), diff'leri (kod + testler + önce/sonra kanıtı) tek tek
okunup doğrulandı, hiçbir kod çakışması çıkmadan sırayla `main`'e alındı
(`27b3cad`, `fc9a9cb`, `e95b7ac`). Birleştirilmiş `main` üzerinde tam suite:
**744 passed, 2 skipped, 0 failed** (739 taban + #80'in 2 yeni testi — #79
ve #81 zaten 739'a dahildi).

## Araştırma bulgusu — Monte Carlo "viable" kapısı hiç bağlı değil, VE mevcut ödeme formülüyle bağlanırsa botu tamamen durdurur

`strategies/arbitrage_engine.py::_maybe_run_monte_carlo()` her ~600 saniyede
bir `MonteCarloSimulator.simulate()` çalıştırıp `bool` bir `viable` sonucu
döndürüyor (CLAUDE.md'nin "6-model" listesinde MC de sayılıyor: "Bayesian+
Edge+Spread+Stoikov+Kelly+MC"). Ama tek çağrı noktası olan `analyze()`
(satır 407-408) dönüş değerini hiç okumuyor:

```python
if signals:
    self._maybe_run_monte_carlo(signals[0].edge, capital)   # dönüş değeri atılıyor
return signals
```

Yani "Viable=✅/❌" her cycle loglanıyor ama gerçekte hiçbir sinyali
engellemiyor — fonksiyon imzası (`-> bool`), ismi (`viable`) ve log formatı
bunun bir güvenlik kapısı olarak tasarlandığını açıkça gösteriyor, ama hiç
bağlanmamış. Bu, tam olarak son birkaç turda tekrar tekrar bulunan
"hesaplanıyor ama tüketilmiyor" hata ailesiyle aynı şekil (bkz. #79, #81,
44. turun `on_fill()` notu, `_extract_global_indices()`).

**Bunu basitçe "bağla" diye düzeltmeden önce** `strategies/monte_carlo.py`'nin
kazanç formülünü gerçek parametrelerle test ettim:

```
edge=0.05  → viable=False (mean_return=-95.4%, win_rate=51.3%)
edge=0.08  → viable=False (mean_return=-95.4%, win_rate=52.1%)
edge=0.10  → viable=False (mean_return=-95.4%, win_rate=53.3%)
edge=0.15  → viable=False (mean_return=-95.1%, win_rate=55.8%)
edge=0.20  → viable=False (mean_return=-90.9%, win_rate=58.4%)
edge=0.30  → viable=False (mean_return=+66.8%, ama p10=-84.1% < -40% eşiği)
```

**Sonuç: MIN_EDGE_THRESHOLD (0.08-0.12) ile MAX_POSITION_PCT (0.20)
aralığındaki HER gerçekçi canlı edge için `viable=False` çıkıyor.** Kök
neden: `simulate()`'in kazanç formülü —

```python
w += bet * net_edge * 2.0   # KAZANÇ
w -= bet                    # KAYIP (tam bet)
```

— gerçek prediction-market ödeme yapısını (kazanınca `bet * (1/price - 1)`,
yani fiyat düşükse çok büyük, fiyat 0.5'e yakınsa ~1x getiri) yansıtmıyor;
bunun yerine kazancı `edge`'in kendisiyle (`×2`) sınırlıyor. 0.05-0.20 edge
aralığında bu, kazancı bet'in sadece %10-40'ına sabitlerken kaybı bet'in
%100'üne bırakıyor — güçlü şekilde negatif beklenen değerli, gerçekçi
olmayan bir simülasyon. Bu spesifik formül hatası 48. turda da not edilmişti
("net_edge*2.0 çift-sayımı") ama o tur bunu "sadece log/viable alanına
gidiyor, canlı etkisi yok" diyerek kapsam dışı bırakmıştı — bu değerlendirme
hâlâ doğru, AMA eksik bir uyarıyla: sonucu böyle bırakmak, "dönüş değerini
kontrol et" şeklindeki apaçık/naif bir sonraki-tur düzeltmesini bir tuzağa
çeviriyor.

**Bu turda kod değişikliği yapılmadı** (bilinçli karar): `_maybe_run_monte_carlo()`'nun
dönüşünü `analyze()`'e bağlamak DIRECTIONAL havuzun (bugün sermayenin
%100'ü) TÜM canlı sinyallerini her cycle'da engelleme riski taşıyor — yukarıdaki
veri, mevcut ödeme formülüyle bu kapı açılırsa botun pratikte hiç trade
yapamayacağını gösteriyor. Hem formülü düzeltmek hem de kapıyı bağlamak
(ve doğru viable eşiklerini canlı davranışa karşı doğrulamak) tek oturumda
aceleye getirilecek, geri dönüşü sermaye kaybı değil ama "bot sessizce
hiç trade yapmaz hale gelir" riski taşıyan bir değişiklik — CLAUDE.md'nin
"3+ adımlı karmaşık görevlerde önce planla" ilkesine göre kendi başına bir
tur gerektiriyor.

### Sıradaki tur için not (yüksek öncelik)
`strategies/monte_carlo.py::simulate()`'i ele almak isteyen bir sonraki tur
için sıra şu olmalı:
1. Önce ödeme formülünü düzelt — gerçek Kelly/edge-model ile tutarlı hale
   getirmek için `simulate()`'e bir `price` parametresi eklenip kazanç
   `bet * (1/price - 1)` (gerçek `b` — bkz. `kelly_criterion.py`'nin kendi
   `b = (1/price)-1` formülü) ile hesaplanabilir; `_maybe_run_monte_carlo()`
   çağrısına `signals[0].entry_price` geçirilmeli.
2. Düzeltilmiş formülü yukarıdaki gibi gerçekçi edge/price kombinasyonlarıyla
   tekrar test et — `viable` artık tipik kazanan sinyallerde `True` çıkmalı.
3. Ancak (1) ve (2) doğrulandıktan SONRA `analyze()`'in dönüş değerini
   kullanmasını sağla, VE bunu canlı sinyalleri tamamen susturmadan önce
   birkaç cycle'lık log-only "gölge mod"da (sadece uyarı, hâlâ trade'e izin
   ver) çalıştırıp gerçek sinyallerle ne sıklıkta false-pozitif engelleme
   yaptığını gözlemlemek daha güvenli olur.
Bu üç adımı tek bir oturumda atlayıp doğrudan "dönüş değerini kontrol et"
şeklinde bağlamak, botu sessizce tamamen durdurabilir.

## Sonuç
`main` artık `#79`-`#81` dahil tüm doğrulanmış düzeltmelere sahip
(`e95b7ac`). Bu tur ayrıca gerçek sermaye riski taşıyan bir "naif düzeltme
tuzağını" (Monte Carlo viable-kapısı) keşfedip belgeleyerek, bir sonraki
turun bunu yanlışlıkla botu durdurmadan doğru sırayla ele almasını sağladı.
