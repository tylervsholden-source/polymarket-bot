# Günlük Strateji İncelemesi — 2026-09-14 (36. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum özeti — Bölüm 1: birikmiş PR kuyruğu temizlendi

Oturum açıldığında `main` hâlâ `eb170aa` (31. çalışma, PR #57) üzerindeydi ve
paralel oturumlardan kalan **4 açık, unmerged PR** vardı — hepsi 31. çalışmanın
tabanından dallanmış, hepsi kendi testleriyle doğrulanmış, hepsi `mergeable_state:
clean`:

| PR | Dosya | Bulgu |
|----|-------|-------|
| #58 (32. çalışma) | `core/polymarket_client.py::get_real_balance()` | Hata yollarında gerçek $0 bakiyeden ayırt edilemeyen `0.0` dönüyordu |
| #59 (33. çalışma) | `agents/orchestrator.py::_sync_real_balance()` | Süresi geçmiş ama resolve olmamış pozisyonlar `locked` sermayeden hariç tutuluyordu |
| #60 (34. çalışma) | `core/position_manager.py::update_positions()` | Kotasyonu olmayan NO pozisyonu 0.00'a değerlenip TAM KAYIP kapanıyordu |
| #61 (35. çalışma) | `agents/top_trader_signal.py::TopTraderTracker` | Trade eşleştirme yanlış JSON alanını okuyordu, cache hep boş kalıyordu |

Dördü de tek tek doğrulandı (diff satır satır okundu, testleri ayrı ayrı
çalıştırıldı), sonra hepsi birlikte `main` üzerine sırayla merge edilirken
tek bir gerçek çakışma çıktı: aynı isimli inceleme dosyası
(`docs/reviews/2026-09-14-strateji-incelemesi-5.md`) iki PR'da da vardı —
kod çakışması değil, dosya adı çakışmasıydı, `-5c.md` olarak yeniden adlandırılıp
çözüldü. Kod tarafında (`polymarket_client.py`, `orchestrator.py`,
`position_manager.py`, `top_trader_signal.py`) sıfır çakışma: dördü de farklı
fonksiyonlara dokunuyordu. Birleşik test suite'i her merge adımından sonra
tekrar çalıştırıldı, her seferinde **sıfır regresyon**:

- #58 sonrası: 677 passed, 2 skipped
- #58+#59 sonrası: 684 passed, 2 skipped
- #58+#59+#60 sonrası: 689 passed, 2 skipped
- #58+#59+#60+#61 sonrası: 689 passed, 2 skipped

Dördü de `squash` merge ile `main`'e alındı. `main` artık 35 daily-review
düzeltmesinin tamamını içeriyor.

## Bölüm 2: yeni bulgu (36.) — `_sync_real_balance()` günlük stop-loss paydasını bozuyor

### Kapsam
Önceki 35 turda dokunulan alanlar (`git log --oneline --grep="daily review" -i`)
hariç tutularak canlı-yol dosyaları tekrar tarandı. Bulgu, 31. çalışmanın
düzelttiği gün-geçişi sorununa komşu ama farklı bir kök nedene sahip: aynı
fonksiyonun (`_sync_real_balance()`) capital'i güncellerken `daily.pnl`'i
güncellememesi.

### Hata
`agents/orchestrator.py::Orchestrator._sync_real_balance()` her döngüde
(60-120sn) çalışıp CLOB bakiyesini tek gerçek kaynak kabul ederek
`data["capital"]`'i düzeltiyor:

```python
new_capital = balance + locked
old_capital = self.position_manager.data.get("capital", 0)
self.position_manager.data["capital"] = round(new_capital, 4)
self.position_manager._save()
```

Ama `PositionManager.daily_loss_exceeded()` (CLAUDE.md'nin non-negotiable
"-%15 günlük stop-loss" kuralı) gün başı sermayeyi `capital - daily.pnl`
olarak hesaplıyor. `_close_position()` ve `_close_position_neutral()`
`capital` ve `daily.pnl`'i her zaman birlikte değiştiriyor — bu değişmez
(invariant) kod tabanında tutarlı şekilde korunuyor. `_sync_real_balance()`
tek istisna: `capital`'i düzeltirken `daily.pnl`'e hiç dokunmuyor.

**Somut senaryo:** Gün $1000 sermaye, `daily.pnl=0` ile başlıyor.
PositionManager'ın kendi PnL takibi (bu fonksiyonun var olma nedeni olan,
kanıtlanmış güvenilmezliği: "752 yanlış resolution → $670 tracking hatası")
hiçbir değişiklik görmüyor, ama gerçek CLOB bakiyesi $849'a düşmüş — gerçek
bir %15,1 günlük kayıp. `_sync_real_balance()` `capital`'i $849'a düzeltiyor
ama `daily.pnl` 0'da kalıyor. Bir sonraki `daily_loss_exceeded(0.15)` çağrısı
`day_start_capital = 849 - 0 = 849` ve `loss_pct = 0` hesaplıyor — gerçek
%15,1 kayıp tamamen maskeleniyor, bot durması gereken günde trade yapmaya
devam ediyor.

### Düzeltme
`_close_position()`/`_close_position_neutral()`'ın zaten koruduğu değişmezi
`_sync_real_balance()`'a da uygulandı — `capital` düzeltmesi aynı miktarda
`daily.pnl`'e de yansıtılıyor, gün geçişi kontrolü de (31. çalışmanın
`_roll_daily_if_needed()` düzeltmesiyle) korunuyor:

```python
self.position_manager._roll_daily_if_needed()
self.position_manager.data["daily"]["pnl"] += (new_capital - old_capital)
```

### Test
`tests/test_sync_balance_daily_pnl_desync.py` (3 test):
1. **Kök senaryo** — $1000 → $849 CLOB düzeltmesi sonrası `daily_loss_exceeded(0.15)`
   `True` dönmeli. Düzeltme öncesi: `False` (bug reprodüksiyonu doğrulandı,
   `git stash` ile).
2. Simetri — yukarı yönlü düzeltme sahte kayıp üretmemeli.
3. Eşik altı düzeltme stop-loss'u tetiklememeli.

### Doğrulama
- Düzeltme öncesi kaynakla (`git stash`): `pytest tests/test_sync_balance_daily_pnl_desync.py -v`
  → **1 failed, 2 passed** — kök senaryo tam olarak reprodüklendi.
- Düzeltme sonrası: **3/3 passed**.
- Tam suite: `pytest tests/ -q` → **692 passed, 2 skipped** (689 taban + 3 yeni
  test), sıfır regresyon.
- `data/autonomous_state.json` (test suite'in yan etkisi) commit öncesi
  geri alındı.

## Sonuç
`main` artık 32 açık PR'ı da içeren 36 daily-review düzeltmesinin tamamına
sahip. Sıradaki tur için taranmamış alan olarak `control_plane/*`,
`strategies/monte_carlo.py`, `strategies/ml_classifier.py`,
`agents/subagents/coordinator.py` öneriliyor (35. ve 34. çalışmaların
bulgularının komşuluğunda, henüz derinlemesine incelenmedi).
