# Günlük Strateji İncelemesi — 2026-09-16 (61. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum açıldığında `claude/brave-faraday-8j8twc` üzerinde açık PR yoktu (60.
çalışmanın konsolidasyon turu #92-#103'ü zaten birleştirmişti). Bu turda
yeni bir kod hatası aramak için, `git log --oneline -i --grep=<dosya>` ile
hiçbir önceki "fix:" commit'inde adı geçmeyen ama canlı işlem yoluna
(`arbitrage_engine.py`/`orchestrator.py` import zinciri) gerçekten bağlı
dosyalar taransın: `strategies/edge_model.py`, `spread_model.py`,
`stoikov.py`, `sum_monitor.py`, `orderbook_analyzer.py`, `quality_filter.py`,
`control_plane/entry_window_guard.py`, `agents/latency_arb.py`,
`strategies/bayesian.py`.

## Bulgu (61.) — `single_market_edge()` iki bacaklı arbitrajın maliyetini tek bacak üzerinden hesaplıyordu

### Kapsam
`strategies/edge_model.py::single_market_edge()` (satır 86-99, düzeltme
öncesi).

### Kök neden
Bu fonksiyon, tek bir market içindeki YES+NO<1 fiyat boşluğunu (gerçek
risksiz arbitraj) modelliyor. Bu boşluğu yakalamak için YES token'ı VE NO
token'ı aynı anda almak gerekir — biri YES defterinde, biri NO defterinde,
iki ayrı fill, her biri kendi spread+slippage maliyetini öder. Fonksiyon
`self.total_cost(yes_price)`'ı sadece **bir kez** çıkarıyordu:

```python
edge = 1.0 - combined - self.total_cost(yes_price)
```

Bu, gerçek round-trip maliyetini tam bir bacak kadar (`spread_cost +
slippage ≈ 0.008`) düşük gösteriyor, marjinal veya gerçekte zarar eden
"arbitraj" fırsatlarını karlı gibi gösteriyordu.

### Neden önemli
`strategies/arbitrage_engine.py`'de:
```python
single_edge = self.edge_model.single_market_edge(yes_price, no_price_ask)
edge = max(trade_edge, single_edge)
...
if edge < effective_min_edge:
    return None
size = self.kelly.position_size(edge=edge, ...)
```
`edge` doğrudan trade'in açılıp açılmayacağını belirliyor ve Kelly pozisyon
boyutunu ölçekliyor. Örnek: YES=0.50, NO_ask=0.49 (%1 birleşik fiyat
boşluğu). Düzeltme öncesi `edge=+0.002` (bedava para gibi görünüyor);
gerçek iki-bacak maliyetiyle (2×0.008=0.016 > 0.01 fark) bu işlem aslında
zarar (`edge=-0.006`).

### Düzeltme
```python
cost = self.total_cost(yes_price) + self.total_cost(no_price)
edge = 1.0 - combined - cost
```

### Test
`tests/test_edge_model_single_market_double_cost.py` (yeni, 1 test):
`single_market_edge(0.50, 0.49)`'un `deviation - 2*per_leg_cost`'a eşit ve
negatif olduğunu doğruluyor. Düzeltme öncesi: **1 failed** (`edge=0.002`).
Düzeltme sonrası: **1 passed**.

### Doğrulama
`python -m pytest tests/` → **787 passed, 2 skipped** (786 taban + 1 yeni
test), sıfır regresyon. PR #105, `claude/brave-faraday-8j8twc`'e squash
merge edildi.

## Diğer taranan dosyalar — bulgu yok
`spread_model.py`, `stoikov.py`, `entry_window_guard.py`, `bayesian.py`:
canlı çağrı yoluyla karşılaştırıldı, mantık tutarlı bulundu.
`quality_filter.py`: `scan_markets.py` dışında hiç çağrılmıyor — dead code,
canlı etkisi yok. `orderbook_analyzer._estimate_slippage()`'daki
`slippage_5/10` alanları bariz hatalı hesaplanıyor ama hiçbir yerde
tüketilmiyor (dead output). `latency_arb.py`'nin doğrudan emir verme yolu
(`_find_market_for_spike`/`_can_trade`) tanımlı ama hiç çağrılmıyor,
`get_spike_boost` çıktısı çağrı noktasında zaten devre dışı.
`sum_monitor.get_edge_adjustment()` de aynı şekilde devre dışı (16.
çalışmadan kalma bilinçli karar).

## Sıradaki tur için notlar
- `orderbook_analyzer.py` ve `latency_arb.py`'deki hatalı/kullanılmayan
  kod, ölü olduğu için bu turda dokunulmadı — ileride bu yollar aktif
  edilirse (`get_spike_boost`/`slippage_5/10` tüketilmeye başlarsa) önce
  düzeltilmeli.
- `operator_layer/aggregator.py`, `operator_layer/ledgers.py`,
  `agents/enhanced_signals.py`, `agents/context_fetcher.py`,
  `agents/copytrade.py`, `agents/btc_arb_agent.py` hâlâ satır satır
  incelenmedi.
