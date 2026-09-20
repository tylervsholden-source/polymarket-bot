# Günlük Strateji İncelemesi — 2026-09-20 (105. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Bu oturum başladığında `origin/main` = `230c718` (104. turun 10 PR'ı merge
edilmişti, açık PR yoktu). Konteynerde çalışan bir bot instance'ı yok
(`data/status.json`/`control.json`/`positions.json` bu sandbox'ta yok, canlı
Polymarket/Anthropic API'lerine ağ erişimi yok) — %10 hedefine karşı gerçek
zamanlı sermaye ilerlemesi bu oturumdan doğrulanamıyor. Bu turun katkısı,
104. turun "henüz derinlemesine taranmamış adaylar" listesindeki dosyaların
taranması ve bulunan tek gerçek canlı-karar hatasının düzeltilmesi oldu.

## Kapsam
104. turun bıraktığı taranmamış aday listesinden 3 paralel oturumla tarandı:
- `strategies/{quality_filter,orderbook_analyzer,sum_monitor,bond_scanner,walk_forward,stoikov}.py`
- `monitoring/{readiness_checks,regime_review,drift_monitor,metrics,alerts}.py`
- `shadow_runner/{journal,reporting,types}.py`,
  `agents/{latency_arb,market_index_watcher,context_fetcher}.py`

Her dosya için hem içindeki mantık hem de `agents/orchestrator.py` /
`strategies/arbitrage_engine.py` / `agents/subagents/*` canlı karar yoluna
gerçekten bağlı olup olmadığı kontrol edildi.

## Bulunan ve düzeltilen gerçek hata

**`monitoring/regime_review.py` — tek pencere oluştuğunda `is_stable` yanlışlıkla `True`**

`compute_regime_review()`, rolling window trend analizini sadece
`len(snapshots) >= 2` iken çalıştırıyordu (satır 165, düzeltme öncesi).
`window_size=20, step=10` varsayılanlarıyla, `n` 20–29 arasında olduğunda
(yani `window_size <= n < window_size + step`) tam olarak **1** pencere
oluşuyor: trend karşılaştırma bloğu hiç çalışmıyor, `flags` tamamen `False`
kalıyor ve `is_stable = not(False or ... )` → `True` sonucu çıkıyor.

Bu, dosyanın kendi `n < window_size` durumundaki yerleşik kuralıyla
doğrudan çelişiyor (satır 133: `is_stable=False,  # unknown ≠ stable`) —
"trend tespit edilemiyor" durumunun "stabil" ile eşitlenmesi hatası, ama
sadece tam olarak bir pencere daha az veri olan komşu sınırda.

**Canlı etki:** `shadow_runner/readiness.py` → `assess_readiness()`,
`monitoring/readiness_checks.py`'deki `check_regime_stability()`'yi
çağırıyor; bu fonksiyon `is_stable=True` gördüğünde GREEN döndürüyor.
`daily_review.write_readiness_verdict()` bu sonucu `data/
readiness_verdict.json`'a yazıyor, `agents/orchestrator.py._readiness_clears_live()`
ise bunu canlı emir akışını açıp açmama kararında (yalnızca
`TINY_PILOT_CANDIDATE` durumunda emir akışına izin verilir) okuyor. Yani
20–29 kayıtlık — dosyanın kendi tanımına göre "trend tespiti için henüz
yetersiz" — bir pencerede rejim yanlışlıkla "stabil, hiç uyarı yok" olarak
raporlanabiliyor ve bu, diğer kontroller de geçerse gerçek sermayeyi açığa
çıkaran GO kararına katkıda bulunabiliyordu.

**Düzeltme:** Tek pencere durumu artık `n < window_size` durumuyla aynı
"unknown ≠ stable" kuralına tabi: `is_stable=False` dönüyor, açıklayıcı bir
not ekleniyor, ve `check_regime_stability()` bunu (flag sayısı 0 olduğu
için) doğru şekilde WARN olarak sınıflandırıyor — GREEN değil. 2+ pencere
oluşan durumlarda davranış değişmedi (regresyon testiyle doğrulandı).

Test: `tests/test_regime_review.py` (3 yeni test) — tek pencere durumunun
`is_stable=False` + WARN ürettiğini, 2 pencere + hiç flag yokken hâlâ
`is_stable=True` + GREEN ürettiğini doğruluyor.

## İncelenen ama düzeltme gerektirmeyen bulgular (ölü kod / bağlı değil)

Bunlar davranış hatası değil, mimari/temizlik notu — kod içinde iç tutarlı,
ama canlı karar yoluna hiç bağlı değil:

- `agents/latency_arb.py`: modül/sınıf docstring'i "spike tespit edince
  doğrudan emir verir" diyor ama `_find_market_for_spike()`/`_can_trade()`
  hiçbir yerden çağrılmıyor; tek canlı etkisi olan `get_spike_boost()` da
  `arbitrage_engine.py`'de önceki bir turdan beri bilinçli olarak
  devre dışı (`# (DISABLED)`). Belgelenen özellik fiilen hiçbir şey yapmıyor.
- `strategies/quality_filter.py`: sadece bağımsız `scan_markets.py`
  script'inden kullanılıyor, orchestrator'a hiç bağlı değil (orchestrator
  kendi yorumunda bunu zaten belirtiyor).
- `monitoring/drift_monitor.py`, `monitoring/metrics.py`,
  `monitoring/alerts.py`: sadece testlerden ve `_gen_artifacts.py`'den
  çağrılıyor; `orchestrator.py`/`daily_review.py` bunları hiç import etmiyor.
  Mantıkları (delta işaretleri, eşik yönleri) doğru ama canlı yolda değil.
- `strategies/stoikov.py`: `ArbitrageEngine.__init__` bir `StoikovExecutor`
  örnekliyor ama hiçbir metodunu çağırmıyor (giriş fiyatı ham ask/bid,
  "FOK: must be at ask to fill" yorumuyla bilinçli görünüyor).
  Ayrıca hiç çağrılmayan `adjusted_entry_price()` içinde
  (`r = max(r, aggressive_r)`, satır 148) envanter bazlı indirim, agresif
  fiyat daha yüksek olduğunda sessizce iptal oluyor — tam da indirimin
  koruması gereken senaryoda. Şu an teorik, çünkü fonksiyon hiç çağrılmıyor.
- `strategies/orderbook_analyzer.py`: `_estimate_slippage()` içinde
  `avg_price = total_cost/(order_size-remaining)` her zaman `1.0`'a
  indirgeniyor (`total_cost` zaten dolar cinsinden dolduruluyor, pay ve
  payda aynı değer oluyor) — gerçek bir hesap hatası, ama çıktısı
  (`slippage_5`/`slippage_10`) `get_signal_boost()` tarafından hiç
  okunmuyor, yani şu an sinyal/boyutlandırmayı bozmuyor.

Bunlar 104. turun notlarındaki "silinsin mi, bağlansın mı" sorusunun kapsamını
genişletiyor — kullanıcı kararı gerektiren mimari sorular, acil düzeltme değil.

## Doğrulama
```
python3 -m pytest tests/ calibration/tests execution_realism/tests \
  crypto_directional/tests signal_bridge/tests -q
# 1787 passed, 4 skipped  (baseline 1784 + 3 yeni test — 0 regresyon)
```

## Sermaye/performans notu
Bu oturumda çalışan bir bot instance'ı yok — %10 hedefine karşı gerçek
ilerleme doğrulanamıyor. Düzeltilen hata, gerçek sermaye canlıya alınmadan
önce readiness gate'inin yanlış bir "hazır" sinyali vermesini önlüyor —
dolayısıyla riski azaltıyor, ama hedefe doğrudan bir kazanç eklemiyor
(gerçek trade akışı bu ortamda çalışmıyor).

## Sıradaki tur için notlar (kullanıcı kararı bekleyen açık mimari sorular)
104. turdan devreden + bu turda genişleyen liste:
1. Onay kuyruğu/doğrudan emir yolu çelişkisi (`agents/orchestrator.py`
   ~1126 vs `docs/APPROVAL_WORKFLOW_SPEC.md`).
2. `agents/enhanced_signals.py` confluence/risk-flag'e hiç bağlı değil.
3. `agents/copytrade.py` tamamen ölü kod.
4. `agents/top_trader_signal.py`'nin `TOP_TRADERS` listesi hiç kullanılmıyor.
5. **(Yeni)** `agents/latency_arb.py`'nin "anında emir" özelliği tamamen
   bağlanmamış — ya bağlanmalı ya da docstring/README'den kaldırılmalı.
6. **(Yeni)** `monitoring/drift_monitor.py`/`metrics.py`/`alerts.py` ve
   `strategies/quality_filter.py` canlı yola hiç bağlı değil — silinsin mi,
   bağlansın mı kararı gerekiyor.
7. **(Yeni)** `strategies/stoikov.py`'de `ArbitrageEngine.self.stoikov`
   kullanılmayan bir alan; ayrıca hiç çağrılmayan `adjusted_entry_price()`
   ileride bağlanırsa önce satır 148'deki envanter-indirim iptali
   düzeltilmeli.

Henüz taranmamış adaylar (104. turdan kalan, bu turda ele alınmayan):
`shadow_runner/{journal,reporting,types}.py` (bu turda tarandı, temiz —
listeden çıkarıldı), `agents/{latency_arb,market_index_watcher,
context_fetcher}.py` (bu turda tarandı — `market_index_watcher` ve
`context_fetcher` temiz, listeden çıkarıldı).
