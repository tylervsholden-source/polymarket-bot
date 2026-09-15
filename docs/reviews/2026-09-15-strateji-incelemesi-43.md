# Günlük Strateji İncelemesi — 2026-09-15 (43. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Bağlam
42. tur (commit `7f2a596`), bond scan'in gerçek emirlerinin master
`live_trading` anahtarını atladığını bulup düzeltmişti (Phase B dispatch'ine
`and self._is_live_trading()` eklendi), ama incelemenin sonunda açıkça not
düştü: `_bond_cycle()` hâlâ `check_live_gate()`'i hiç çağırmıyor — sadece
dış `_is_live_trading()` guard'ına güveniyor; günlük -%15 stop-loss ve
process lock kontrolünü atlıyor. Bu tur o notu doğrudan takip etti.

**Yan not:** Bu turun ortasında, `main`'e `9b5fd52` ("full bot update") adlı
büyük bir harici commit'in kısa süreliğine düştüğü ve
`agents/orchestrator.py`'yi baştan yazarak 42. turun `_is_live_trading()`
düzeltmesini sildiği gözlemlendi (git fetch reflog'u ile doğrulandı) — ama
bu commit, bu inceleme sürerken `main`'den geri alındı (force-push ile
`e796b11`'e döndürüldü). Gerçek `main`'in şu anki hali (`e796b11`) bu
excursion'ı hiç görmemiş gibi: 42. turun düzeltmesi ve `agents/signal_agent.py`
sağlam. Bu yüzden düzeltme, gerçek `main` (`e796b11`) üzerine inşa edildi;
geçici commit'te gözlemlenen ekstra regresyonlar (deleted `_is_live_trading()`
guard, `signal_agent.py` söz dizimi hatası) `main`'de hiç var olmadı ve bu
PR'a dahil edilmedi. Yine de o excursion'ın tekrar olasılığına karşı, Phase B
dispatch koşulunun şeklini doğrudan kilitleyen yapısal bir regresyon testi
eklendi (bkz. Test bölümü) — böylece gelecekte benzer bir toptan yeniden
yazım, `_bond_cycle()`'ın içine dokunmasa bile fark edilir.

## Bulgu — Bond cycle günlük stop-loss, process lock ve hesap-geneli pozisyon limitini atlıyordu

`Orchestrator._bond_cycle()` (`agents/orchestrator.py`) gerçek emirleri
`self.client.place_passive_order(...)` ile doğrudan veriyor, ama
yönlü (directional) emir yolunun ve `_execute_approved_orders()`'ın her
ikisinin de uyguladığı üç zorunlu kontrolden hiçbirini yapmıyordu:

1. `position_manager.daily_loss_exceeded(self.daily_stop_loss)` — günlük
   -%15 stop-loss.
2. `self._process_lock.is_mine()` — process lock sahipliği.
3. `position_manager.open_position_count() >= self.max_open_positions` —
   CLAUDE.md'nin "Aynı anda max 5 açık pozisyon" kuralı (hesap geneli, tek
   strateji değil).

`_bond_cycle()` yalnızca kendi bond havuzunun sermayesini
(`pool_available("bond")`) ve `BondScanner.MAX_POSITIONS`'ı kontrol
ediyordu — bunlar CLAUDE.md'nin yukarıdaki iki kuralıyla ilgisiz,
strateji-içi sayaçlar.

**Canlı etki:** `live_trading=true` iken kayıplı bir gün
`daily_loss_exceeded()`'ı tetiklediğinde — botun geri kalan her gerçek emir
yolu anında durur — 5. döngüde çalışan bond taraması gerçek emir vermeye
devam ediyordu, botun az önce uyguladığı günlük stop-loss'u sessizce delip
geçiyordu. Aynı şekilde: ikinci bir process'in bayat bir lock tutması, ya da
aynı döngüde yönlü tarafın hesabın son pozisyon slotunu doldurması, bond
emirlerini durdurmuyordu.

## Düzeltme
`_bond_cycle()`'ın başına üç guard eklendi (daily stop-loss, process lock,
hesap-geneli max pozisyon) ve fırsat döngüsü içine — aynı cycle'da
yönlü taraf slot doldurursa veya stop-loss ortasında tetiklenirse döngüyü de
durdurmak için — dördüncü bir yeniden-kontrol eklendi. Dar kapsamlı, sadece
`_bond_cycle()`'a dokunuyor (minimal fix ilkesi).

## Test
`tests/test_bond_cycle_daily_stop_and_lock_gate.py` (yeni) — 5 test:
daily-stop-loss aşıldığında atla, process lock başkasına aitken atla,
hesap-geneli pozisyon limiti dolduğunda atla, tüm guard'lar geçtiğinde bond
trade'in hâlâ çalıştığını doğrulayan sanity check, ve Phase B dispatch
koşulunun `self._is_live_trading()` içerdiğini `inspect.getsource` ile
doğrudan doğrulayan yapısal bir test (bu turda gözlemlenen toptan
yeniden-yazım excursion'ından ders alınarak eklendi — `_cycle()`'ı uçtan uca
mocklamak yerine tek satırlık guard koşulunu doğrudan kilitliyor, böylece
`_cycle()`'ın geri kalanındaki değişikliklere karşı kırılgan olmuyor).

Düzeltme öncesi (gerçek `main` — `e796b11` — ile doğrulandı): 3/5 test FAIL —
`place_passive_order` çağrılıyordu (`daily_loss_exceeded=True`,
`process_lock_is_mine=False`, `open_position_count=5/max=5` durumlarının
hepsinde).

Düzeltme sonrası: `pytest tests/test_bond_cycle_daily_stop_and_lock_gate.py -q`
→ **5 passed**.

Tam suite: `pytest tests/ -q` → **723 passed, 2 skipped, 0 failed**
(regresyon yok).

## Sonraki tur için not
- `_bond_cycle()` artık üç kritik guard'ı doğrudan kontrol ediyor, ama hâlâ
  `check_live_gate()`'in tek merkezi noktasından geçmiyor —
  `_execute_approved_orders()` gibi ortak bir yola taşınması, ileride yeni
  bir guard eklendiğinde bond'un otomatik olarak kapsanmasını sağlardı. Bu
  turun kapsamı dışında tutuldu (minimal fix), ama izlenmeli.
- `main`'e önceki turların düzeltmelerini geri getiren büyük, harici
  commit'lerin (bu turdaki `9b5fd52` gibi) düşebildiği doğrulandı. Böyle bir
  şey tekrar gözlemlenirse: sadece en son bulunan kusura odaklanmak yerine,
  önce gerçek `main`'in GitHub üzerindeki güncel halini (yerel git ref'lerine
  değil, `git fetch` sonrası reflog'a ve/veya GitHub API'sine) doğrulayıp,
  önceki turların düzelttiği kritik güvenlik kontrollerinin (live-trading
  gate, daily stop-loss, process lock, pozisyon limitleri, PnL muhasebesi)
  hâlâ yerinde olup olmadığı sistematik olarak yeniden doğrulanmalı.
- `pytest.ini`'deki `testpaths` `crypto_directional/tests`'i de kapsıyor;
  o dizin bu ortamda eksik bağımlılıklar (`sklearn` vb.) yüzünden
  toplanamıyor. `crypto_directional/` mimari dokümana göre canlı yol
  dışında (scaffolding) olduğundan bu turda araştırılmadı, ama bağımlılık
  eksikliği ayrı bir CI/ortam sorunu olarak not edildi.
