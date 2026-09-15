# Günlük Strateji İncelemesi — 2026-09-15 (42. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Bağlam
41. tur (commit `aff7141`) ve konsolidasyon turu, `control_plane/entry_window_guard.py`,
`reentry_guard.py`, `strategies/monte_carlo.py` ve
`agents/subagents/research_agent.py`'yi zaten taramış ve temiz bulmuştu
(`monte_carlo.py`'deki `net_edge*2.0` hâlâ dead-code — tek çağrı noktası
`strategies/arbitrage_engine.py:408` dönüş değerini kullanmıyor). Bu tur o
üç alanı yeniden doğruladıktan sonra kapsamı genişletti.

## Bulgu — Bond scan gerçek emirleri master `live_trading` anahtarını atlıyordu

`agents/orchestrator.py`, `Orchestrator._cycle()`'ın sonunda:

```python
# Phase A: Market Making
if self._maker_enabled and self._maker_engine and self._is_live_trading():
    ...
# Phase B: Bond scan — every 5th cycle (~5 min)
if self._bond_enabled and self._bond_scanner and self._cycle_count % 5 == 0:
    await self._bond_cycle()
```

Phase A, `_cycle()`'daki doğrudan emir yolu ve `_execute_approved_orders()`
— döngüdeki gerçek para dokunan **her** yol — `self._is_live_trading()`'i
(env `LIVE_TRADING_ENABLED` + readiness verdict + `control.json.live_trading`)
kontrol ediyor; `_execute_approved_orders()` ayrıca `check_live_gate()`
üzerinden ikinci kez doğruluyor. Phase B (bond) bu kontrolün hiçbirini
yapmıyordu — `_bond_cycle()` de `check_live_gate()` çağırmıyor,
`self.client.place_passive_order(...)`'ı doğrudan çağırıyor.

**Canlı etki:** `BOND_ENABLED=true` ve gerçek CLOB kimlik bilgileri
bağlıyken, dashboard'dan `live_trading=false` yapmak (master pause/kill
switch) — ya da `LIVE_TRADING_ENABLED=false` ya da readiness verdict'in
bayatlaması/fail olması — bond stratejisinin her 5. döngüde gerçek emir
vermeye devam etmesini durdurmuyordu (`simulation_running=true` kaldığı
sürece). Bond emirleri ayrıca günlük -%15 stop-loss kontrolünü ve process
lock kontrolünü de atlıyordu, çünkü `_bond_cycle()` `check_live_gate`'i hiç
çağırmıyor. Bu, CLAUDE.md'deki "Günlük stop-loss: -%15 → bot o gün durur"
kuralının tam ihlali sınıfında bir güvenlik açığı: live/sim ayrımının
amacı kapatıldığında gerçek paranın hareket etmemesi.

## Düzeltme
Phase A ile aynı örüntü: guard koşuluna `and self._is_live_trading()`
eklendi (dar kapsamlı, tek satır + açıklayıcı yorum).

## Test
`tests/test_bond_cycle_live_trading_gate.py` (yeni) — minimal mock'lu
`Orchestrator._cycle()` çalıştırıp `live_trading=False` iken
`_bond_cycle()`'ın çağrılmadığını, `True` iken çağrıldığını doğruluyor.
Düzeltme öncesi: ilk test FAIL (hata reprodüksiyonu). Düzeltme sonrası: 2/2
PASS.

Tam suite: `pytest tests/ -q` → **718 passed, 2 skipped, 0 failed**.

## Sonraki tur için not
- `_bond_cycle()` hâlâ `check_live_gate()`'i hiç çağırmıyor — sadece dış
  `_is_live_trading()` guard'ına güveniyor. Diğer yollar (`_execute_approved_orders`)
  ayrıca `check_live_gate` ile ikinci bir doğrulama yapıyor (process lock,
  stop-loss dahil). Bond'un da `check_live_gate()`'e taşınması ileride ek
  savunma katmanı sağlar — bu turun kapsamı dışında tutuldu (minimal fix
  ilkesi), ama izlenmeli.
