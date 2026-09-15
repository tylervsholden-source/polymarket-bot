# Günlük Strateji İncelemesi — 2026-09-15 (44. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Bağlam
Bu turun başında main'de #74 ve #75 olmak üzere iki bekleyen PR bulundu
(43. tur, iki ayrı oturumdan). İkisi de küçük, iyi test edilmiş, güvenlikle
ilgili düzeltmelerdi (TradeAnalyzer restart double-count, bond cycle guard
eksikliği). 26097447 commit'inde ("5-PR backlog") kurulan emsale uyarak,
her iki diff de okunup doğrulandı ve main'e merge edildi
(0b7a0c4c, c20d0af) — 44. turun kendi bulgusuna geçmeden önce.

## Bulgu — Maker (Phase A) aynı guard eksikliğini taşıyordu: günlük stop-loss / process lock / hesap-geneli pozisyon limiti atlanıyordu

43. tur, `_bond_cycle()`'ın (Phase B) günlük -%15 stop-loss, process lock ve
hesap-geneli max pozisyon kontrollerini atladığını bulup düzeltti. Ama
`agents/orchestrator.py`'de Phase A'nın (Market Making, `_maker_enabled` +
`MakerEngine.refresh_quotes()` → `client.place_passive_order()` ile gerçek
GTC emirleri veren yol) **aynı üç guard'dan hiçbirine sahip olmadığı**
gözden kaçmıştı — sadece `self._is_live_trading()` kontrol ediliyordu,
Phase B'nin düzeltme-öncesi haliyle birebir aynı şekil.

**Canlı etki:** `MAKER_ENABLED=true` ve `MAKER_CAPITAL_PCT > 0` ayarlandığında
(ikisi de gerçek, dokümante `.env` anahtarları — varsayılan olarak kapalı
ama scaffolding değil, tam bağlı bir özellik), günlük -%15 stop-loss
tetiklendiğinde botun geri kalan her gerçek emir yolu dururken maker
her döngüde iki taraflı gerçek emir vermeye devam ederdi. Aynı şekilde
bayat bir process lock veya aynı döngüde yönlü tarafın son pozisyon
slotunu doldurması da maker'ı durdurmuyordu.

**Doğrulama:** Yeni eklenen `tests/test_maker_cycle_daily_stop_and_lock_gate.py`
düzeltme-öncesi koda karşı çalıştırıldı — `daily_loss_exceeded` ve
`process_lock_is_mine=False` senaryolarında `refresh_quotes()` hâlâ
çağrılıyordu (2/4 test FAIL). Pozisyon-limiti senaryosu pre-fix'te de
geçiyordu çünkü `_cycle()`'ın başında (satır 564) `open_count >=
max_open_positions and self._is_live_trading()` durumunda zaten döngü
başında erken `return` var — ama bu üst düzey kontrol döngü *başındaki*
pozisyon sayısını kullanıyor; yönlü taraf bu döngü İÇİNDE yeni pozisyon
açarsa (aynı `_bond_cycle()` düzeltmesinin gerekçesiyle aynı), Phase A'ya
gelene kadar sayı değişmiş olabilir. Bu yüzden mid-cycle re-check olarak
korundu (bond'daki ikinci kontrolle aynı mantık).

## Düzeltme
`agents/orchestrator.py`, Phase A'nın dispatch koşuluna `_bond_cycle()` ile
birebir aynı üç guard eklendi: `daily_loss_exceeded()`, `_process_lock.is_mine()`,
`open_position_count() < max_open_positions`. Dar kapsamlı, sadece dispatch
koşuluna dokunuyor (minimal fix ilkesi).

## Test
`tests/test_maker_cycle_daily_stop_and_lock_gate.py` (yeni, 4 test) — 2 fail
düzeltme-öncesi `agents/orchestrator.py`'e karşı, 4/4 pass düzeltme sonrası.
Tam suite: `pytest tests/ -q` → **730 passed, 2 skipped, 0 failed** (43.
turdan sonraki 726'ya bu turun 4 yeni testi eklendi, regresyon yok).

## Sonraki tur için not — MakerEngine fill reconciliation hiç bağlı değil (daha büyük kapsamlı, bu turda düzeltilmedi)

`strategies/maker_engine.py`'deki `MakerEngine.on_fill()` ve
`check_paired_profit()` metodları — bir standing order'ın dolduğunu
kaydetmek ve eşleşmiş YES+NO karından PnL hesaplamak için var — hiçbir
yerden çağrılmıyor (`grep -rn "on_fill\|check_paired_profit"` sıfır sonuç,
tanımlandıkları dosya dışında). Sınıfın kendi docstring'i bunu itiraf
ediyor: *"fills are detected by position_manager externally"* — bu
entegrasyon hiç yazılmamış.

Bunun anlamı: `MAKER_ENABLED=true` olduğunda, bir pasif emir dolarsa
(`place_passive_order` gerçek USDC harcayıp gerçek hisse alır), bu fill
`position_manager.add_position()`'a hiç düşmez → `open_position_count()`,
`pool_locked("maker")` (dolayısıyla `pool_available("maker")`), ve günlük
PnL/stop-loss hesaplarının hiçbiri bu pozisyonu görmez. `pool_available("maker")`
gerçek fill sonrası bile havuzun tamamını "kullanılabilir" olarak
raporlamaya devam eder — bu turun düzelttiği guard'lar (özellikle
`daily_loss_exceeded`) girdisi bozuk olacağından etkisiz kalabilir.

Bu, bu turun düzelttiğinden daha büyük bir mimari boşluk: gerçek fill
tespiti (`client.get_order_status()` zaten var, `_cancel_all_standing()`
içine iptal etmeden önce durum kontrolü eklenebilir) ve
`position_manager`'a doğru wiring gerektiriyor — tek oturumluk minimal bir
fix'in kapsamını aşıyor ve CLOB'a karşı test edilemeden yazılırsa yeni
hatalar riski taşıyor. `MAKER_ENABLED` varsayılan `false` ve
`MAKER_CAPITAL_PCT` varsayılan `0.00` olduğundan şu an inert, ama
`crypto_directional/` gibi gerçekten ölü kod değil — bağlı, dokümante bir
özellik. Sonraki bir turda ele alınmalı: ya fill reconciliation'ı doğru
şekilde inşa et, ya da özellik tamamlanana kadar `MAKER_ENABLED`'ı
etkinleştirmenin production'da güvenli olmadığını CLAUDE.md/.env.example'a
açıkça not düş.
