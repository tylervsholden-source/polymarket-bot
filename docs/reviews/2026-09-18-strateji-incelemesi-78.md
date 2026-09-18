# Günlük Strateji İncelemesi — 2026-09-18 (78. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = bu branch = `94f9468` (#135 sonrası, 77.
inceleme — loss-streak/Kelly-streak/walk-forward risk snapshot sıralama
düzeltmesi dahil). Açık PR yoktu. Baseline test: `pytest tests/ -q` →
841 passed, 2 skipped (crypto_directional/ hariç — ortamda sklearn eksik,
canlı yol ile ilgisiz, önceki turlarda da bilinen bir durum).

## Bu turda yapılanlar
1. Bağımsız, taze bir gözden geçirme ajanı ile canlı/shadow yol tekrar
   tarandı: `agents/orchestrator.py`, `core/position_manager.py`,
   `strategies/arbitrage_engine.py`, `strategies/kelly_criterion.py`,
   `strategies/edge_model.py`, `strategies/maker_engine.py`,
   `strategies/bond_scanner.py`, `strategies/mean_reversion.py`,
   `strategies/ml_classifier.py`, `agents/autonomous_engine.py`,
   `agents/trade_analyzer.py`, `agents/subagents/*`, `agents/resilience.py`,
   `agents/copytrade.py`, `agents/btc_arb_agent.py`,
   `core/polymarket_client.py`, `shadow_runner/*`, `monitoring/*`.
2. **Yeni bir hata bulundu:** `agents/orchestrator.py`,
   `Orchestrator._sync_open_orders_from_clob()`. Bu metod sadece process
   başlangıcında (`Orchestrator.run()`, `_sync_real_balance()`'tan hemen
   sonra) çalışır — amacı, önceki bot instance'ının zaten verdiği canlı
   emirleri geri yükleyip restart sonrası aynı markete tekrar girmeyi
   önlemektir. Recover edilen her pozisyon için `outcome` alanı koşulsuz
   `"YES"` olarak hardcode edilmişti (`# CLOB doesn't expose side easily`
   yorumuyla) — ama bu varsayım yanlış: CLOB'un `GET /orders` yanıtı her
   emrin kendi `outcome` alanını (Yes/No, up/down marketlerde Up/Down)
   taşıyor, sadece hiç okunmuyordu.
   - **Somut etki:** Bot canlı $20'lık bir NO emri verir; market
     çözülmeden önce process restart olur (deploy, crash-restart, manuel
     bounce — bu bot için rutin bir olay). Restart sonrası
     `_sync_open_orders_from_clob()` MATCHED NO emrini bulur ve
     `position_manager`'a ekler — ama `outcome: "YES"` olarak kaydeder.
     Market DOWN sonuçlanınca (gerçek NO pozisyonunun kazanan yönü),
     `PositionManager`'ın resolution formülü
     (`close_price = 0.0 if outcome=="YES" else 1.0`, NO resolution'da)
     yanlış etiketlenmiş `outcome` yüzünden `close_price=0.0` hesaplıyor —
     gerçekten kazanan $20'lık NO pozisyonu tam LOSS (pnl=-$20) olarak
     kapanıyor. Tersi de mümkün (kaybeden bir NO pozisyonu WIN
     kaydedilir). Her iki durumda da `position_manager.data["capital"]`
     ve CLAUDE.md'deki günlük -%15 stop-loss paydası ters yönde bozuluyor.
     `asset_id` de aynı sebeple `token_id` olarak taşınmıyordu —
     `PositionManager.update_positions()` NO pozisyonların kendi
     orderbook'unu bulmak için önce bunu tercih ediyor.
   - Bu, önceki 77 incelemenin kapsamadığı ayrı bir hata sınıfı
     (`docs/reviews/*.md` ve `tests/`'te `_sync_open_orders_from_clob` /
     `CLOB_SYNC` için hiçbir eşleşme yoktu).
3. **Düzeltme:** `order.get("outcome")` artık okunuyor;
   `YES/UP → "YES"`, `NO/DOWN → "NO"` normalize ediliyor, alan eksik/tanınmayan
   olduğunda eski varsayılan davranış (`"YES"`) korunuyor — bu durumda
   regresyon yok. `order.get("asset_id", "")` de `token_id` olarak
   taşınıyor.
4. **Test:** Yeni dosya `tests/test_clob_sync_outcome_mismatch.py` (2 test):
   - `test_sync_recovers_real_no_order_as_no_not_yes`: recover edilen
     MATCHED NO emrinin `outcome == "NO"` kaldığını doğrular.
   - `test_sync_recovered_no_win_must_not_be_booked_as_a_loss`: uçtan uca —
     NO emri recover edilir, `PositionManager`'ın kendi DOWN-resolution
     formülü uygulanır ve `_close_position()` çağrılır; sonucun hayalet
     `LOSS` değil `WIN` (pozitif pnl) olduğu doğrulanır.
   - Doğrulama: her iki test de düzeltme öncesi kaynağa karşı **fail**
     ediyor (`AssertionError: ... booked as LOSS, pnl=$-20.00`), düzeltme
     sonrası **pass** ediyor. Bağımsız olarak da doğrulandı
     (`git stash` ile eski kodla tekrar çalıştırıldı → aynı hata).
5. **Tam suite:** `python3 -m pytest tests/ -q` → **843 passed, 2 skipped**
   (841'den +2 yeni test, regresyon yok).

## Sonuç
- `agents/orchestrator.py::_sync_open_orders_from_clob()` içindeki
  restart-recovery hatası düzeltildi; artık recover edilen pozisyonlar
  gerçek CLOB `outcome`'una göre YES/NO olarak doğru etiketleniyor,
  `token_id` de doğru taşınıyor.
- Etki sadece process restart senaryosunda tetiklendiği için önceki
  turlarda fark edilmemiş olabilir, ama canlı sermaye etkisi doğrudan ve
  ciddi (yanlış yönde tam pnl ters çevirme).
- Açık PR kalmadı, bu turun değişikliği PR olarak açılacak.
