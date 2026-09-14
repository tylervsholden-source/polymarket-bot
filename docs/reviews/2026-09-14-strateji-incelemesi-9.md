# Günlük Strateji İncelemesi — 2026-09-14 (37. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Kapsam
Önceki 36 turda dokunulan alanlar (`git log --oneline --grep="daily review" -i`)
hariç tutularak canlı-yol dosyaları üç paralel odakla tekrar tarandı:
(1) emir verme/sermaye muhasebesi (`orchestrator.py`, `position_manager.py`,
`polymarket_client.py`), (2) otonom karar/subagent pipeline'ı
(`autonomous_engine.py`, `resilience.py`, `agents/subagents/*`), (3) sinyal/
strateji matematiği (`arbitrage_engine.py` ve 6-model bileşenleri,
`top_trader_signal.py`, `smart_trader_tracker.py`). Üç ayrı bulgu raporlandı;
biri iki farklı tarama tarafından bağımsız olarak da doğrulandı. Her ikisi de
kodu satır satır okuyarak, somut sayılarla senaryo kurarak ve düzeltme
öncesi/sonrası testle doğrulandı.

## Bulgu 1 (37a) — `_execute_approved_orders()` `token_id`'yi pozisyona hiç yazmıyor, 33./34. çalışmaların düzelttiği NO-değerleme hatasını ikinci bir yoldan geri getiriyor

### Hata
`agents/orchestrator.py::_execute_approved_orders()` (dashboard onay
kuyruğundan gelen emirler — her döngüde çalışır) emri verirken `token_id`'yi
`place_order()`'a geçiyor ama dönen `order` dict'ine hiç yazmıyordu:

```python
order = await self.client.place_order(
    market_id=market_id, outcome=direction, amount=amount,
    price=price, token_id=token_id, question=question,
)
if order:
    self._order_timestamps.append(time.time())
    order["outcome"] = direction
    self.position_manager.add_position(market_id, order, question)
```

Aynı dosyadaki **doğrudan emir** yolu (otomatik döngü içi execution, satır
~921) bu eksikliği zaten telafi ediyordu:

```python
order["outcome"] = signal.direction
order["token_id"] = token_id or ""
self.position_manager.add_position(market_id, order, ...)
```

`PolymarketClient.place_order()` (`core/polymarket_client.py`, hem canlı
dönüş satır 557-564 hem `_simulate()` satır 831-838) döndürdüğü dict'te
`token_id` alanı hiç bulundurmuyor — sadece `order_id, market_id, outcome,
amount, price, status`. `PositionManager.add_position()` ise
`order.get("token_id", "")`'yi olduğu gibi kaydediyor. Onay-kuyruğu yoluyla
açılan her pozisyon `token_id=""` ile kaydediliyordu.

**Somut senaryo:** Dashboard'dan bir NO emri onaylanıp bu yoldan execute
ediliyor. `update_positions()` (`core/position_manager.py:566`):
```python
no_tid = pos.get("token_id") or (market or {}).get("no_token_id")
```
`pos.get("token_id")` boş string (falsy), `client.get_market()` tekil-market
Gamma çağrısı `no_token_id`'yi hiç doldurmuyor
(`tests/test_no_valuation_live_orderbook.py` bu Gamma kısıtını zaten
kilitliyor). Yani `no_tid=None`, gerçek NO orderbook hiç sorgulanmıyor,
kod `current_price = round(1.0 - yes_ask, 4)` — bayat Gamma-türevi fiyata
düşüyor. Bu da 45-dakikalık `TIMEOUT_HEURISTIC`'i besliyor: gerçekte kazanan
bir pozisyon, stale değere göre LOSS/NEUTRAL olarak zorla kapanabiliyor —
tam olarak 34. çalışmanın (`5a9b829`) ve 33. çalışmanın (`92a4589`)
düzelttiği hatalar, ikinci, test edilmemiş bir kod yolundan geri geliyor.

### Düzeltme
`agents/orchestrator.py::_execute_approved_orders()` içine, doğrudan emir
yolundaki eşdeğer satır eklendi:
```python
order["outcome"] = direction
order["token_id"] = token_id or ""
self.position_manager.add_position(market_id, order, question)
```

### Test
`tests/test_approved_order_token_id_wiring.py` (1 test) —
`place_order()`'ın gerçek dönüş şeklini (token_id'siz dict) mock'layıp
`_execute_approved_orders()`'ı çalıştırıyor, kaydedilen pozisyonun
`token_id`'sinin onaylı emrin `token_id`'siyle eşleştiğini doğruluyor.
Düzeltme öncesi kaynakla (`git stash`): **KeyError: 'token_id' — FAILED**
(hata tam olarak reprodüklendi). Düzeltme sonrası: **PASSED**.

## Bulgu 2 (37b) — `Coordinator._re_enrich_signals()` skorları güncelliyor ama listeyi hiç yeniden sıralamıyor, `MAX_DIRECTIONAL`/döngü bütçesi yanlış sinyali seçebiliyor

### Hata
CLAUDE.md'nin PHASE 1/2/3 pipeline'ında, `SignalAgentV2.run()`
(`agents/subagents/signal_agent_v2.py:166`) sinyalleri `confluence_score`'a
göre sıralıyor — ama bu, ResearchAgent ile **paralel** çalıştığı için henüz
hiçbir whale/smart-money/regime/orderflow verisi yokken, sadece edge'e göre
bir sıralama. PHASE 2'nin `_re_enrich_signals()`'ı
(`agents/subagents/coordinator.py`) her sinyalin gerçek research alanlarını
yazıp `confluence_score`'u yeniden hesaplıyor — ama listeyi tekrar
sıralamadan döndürüyordu. `ReviewerAgent.get_approved_signals()` bu listeyi
sırasıyla geziyor; `agents/orchestrator.py`'daki `MAX_DIRECTIONAL = 2` ve
döngü risk bütçesi kapakları da `coord_result.approved_signals`'ı sırasıyla
gezip kapasite dolunca `break` ediyor. Yani rekabet eden sinyaller arasında
hangisi işleme girecek, gerçek confluence'a göre değil, research'ten önceki
bayat edge-sırasına göre belirleniyordu.

**Somut senaryo (ağırlıklar: edge 3, whale 2, smart 2, regime 1.5, hacim 1,
orderflow 2.5 — toplam 12):**
- Sinyal A (BTC YES, edge 0.18): research öncesi sadece edge terimi →
  confluence≈0.61. Research sonrası: whale BEARISH (YES'e karşı), smart
  −0.4 (karşı), regime DOWN (karşı), orderflow karşı → confluence≈0.51.
- Sinyal B (ETH NO, edge 0.09): research öncesi confluence≈0.56. Research
  sonrası: whale BEARISH (NO ile uyumlu), smart −0.4 (uyumlu), regime DOWN
  (uyumlu), orderflow uyumlu → confluence≈0.74.

Research-öncesi sırada A önde (0.61 > 0.56) ve liste hiç yeniden
sıralanmadığı için A önde kalıyor — oysa gerçek, tam bilgili sıralama tam
tersi (B 0.74 ≫ A 0.51). `MAX_DIRECTIONAL`/bütçe kapasitesi dolduğunda,
her sinyalin gerçekten onayladığı B değil, hiçbir research verisi yokken
şans eseri önde kalan A işleme giriyor — PHASE 1/2/3'ün var olma amacının
(rekabet halinde en çok teyitli sinyali tercih etmek) tam tersi.

### Düzeltme
`_re_enrich_signals()`'ın sonuna, tüm sinyaller yeniden hesaplandıktan
sonra tek bir yeniden-sıralama eklendi:
```python
signal_result.signals.sort(key=lambda s: s.confluence_score, reverse=True)
```

### Test
`tests/test_coordinator_reenrich_resort.py` (1 test) — yukarıdaki A/B
senaryosunu birebir kuruyor (pre-confluence A=0.61>B=0.56, post-confluence
B=0.74>A=0.51), `_re_enrich_signals()`'ı çağırıp dönen listenin
`["B", "A"]` olduğunu doğruluyor. Düzeltme öncesi kaynakla: **AssertionError
— `['A', 'B'] == ['B', 'A']` FAILED** (hata reprodüklendi). Düzeltme
sonrası: **PASSED**.

## Bağımsız çapraz doğrulama
Üç paralel taramadan ikisi, birbirinden habersiz şekilde aynı 37a bulgusuna
(`_execute_approved_orders()` token_id eksikliği) ulaştı — biri emir/
muhasebe yolunu, diğeri sinyal/strateji matematiğini tararken. Bu, bulgunun
gerçekliği için ek bir doğrulama sinyali.

## Doğrulama
- Her iki test de düzeltme öncesi kaynakla ayrı ayrı **FAILED** (hata
  reprodüksiyonu doğrulandı), düzeltme sonrası ayrı ayrı **PASSED**.
- Tam suite: `pytest tests/ -q` → **694 passed, 2 skipped** (692 taban + 2
  yeni test), sıfır regresyon.
- `data/autonomous_state.json` (test suite'in yan etkisi) commit öncesi
  geri alındı.

## Sonuç
`main` artık 37 daily-review düzeltmesinin tamamına sahip olacak. Sıradaki
tur için taranmamış/derin incelenmemiş alan olarak `control_plane/*`,
`strategies/monte_carlo.py`, `strategies/ml_classifier.py`,
`agents/enhanced_signals.py`, `agents/subagents/orderflow_agent.py` öneriliyor.
