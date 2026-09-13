# Günlük Strateji İncelemesi — 2026-09-13 (20. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum özeti
- Bu oturum açıldığında `main`'de (`0d1c564`, 19. çalışmanın sonucu) bekleyen
  bir PR vardı: **#40**, "ResearchAgent regime detection read attributes that
  never exist" — paralel çalışan başka bir günlük inceleme oturumunun bulduğu
  gerçek bir hata (`BinanceFeed._regime` hiç var olmayan bir attribute,
  `getattr` her zaman `None` dönüyordu; fallback de `get_signal()`'in hiç
  döndürmediği `change_4h` anahtarını okuyordu — sonuç: research regime canlı
  yolda her zaman NEUTRAL/0.0 kalıyordu, `REGIME_OVEREXTENDED`/
  `COUNTER_REGIME_*` risk flag'leri ve AutonomousEngine'in regime>0.80 boyut
  freni hiç tetiklenmiyordu).
- Doğrulama: izole worktree'de PR #40 branch'i (`origin/main` ile birebir
  güncel) checkout edilip `pytest tests/` çalıştırıldı → **623 passed, 2
  skipped** (main'in 618'inden 5 yeni regresyon testi ile). Diff, `_regime`'in
  kod tabanında hiçbir yerde tanımlı olmadığını ve gerçek API'nin
  `get_market_regime()` olduğunu doğruluyordu. **PR #40 squash-merge edildi.**

## Bugün yapılan işlem: cycle-içi `capital`/`cycle_spent` sayacı gerçek emir maliyeti yerine istenen bet_size ile düşülüyordu

19 önceki inceleme dokümanının hiçbiri `core/polymarket_client.py::place_order()`'ın
CLOB minimum emir boyutu davranışını `agents/orchestrator.py`'nin cycle-içi
sermaye muhasebesiyle çapraz kontrol etmemişti — bunu bulmak için ayrı bir
araştırma ajanı (unreviewed dosyalara odaklı) kullandım.

### Hata
`core/polymarket_client.py:418-420`:
```python
size = math.floor(amount / price * 100) / 100
if size < 5.0:
    size = 5.0
```
CLOB'un 5-share minimum emir kuralı, küçük `amount` + yüksek fiyat (özellikle
`price > 0.90` civarı, price-bump sonrası) kombinasyonunda gerçek maliyeti
istenenin kat kat üzerine çıkarabiliyor — örn. `amount=$1 @ price=0.90` →
`size` 5'e taban buluyor → gerçek maliyet `round(5*0.91,4)=$4.55` (≈4.5x).
Fonksiyon bu gerçek maliyeti doğru şekilde `order["amount"]` olarak
döndürüyor (`core/polymarket_client.py:531-532`), ve
`position_manager.add_position()` bunu doğru cost-basis olarak kaydediyor —
yani **cross-cycle** muhasebe (`available_capital()`) hep sağlamdı.

Ama `agents/orchestrator.py`'deki iki canlı emir yerleştirme noktası bu
gerçek değeri hiç okumuyordu:
- `_cycle()` (doğrudan emir yolu, eski satır 899-900):
  `capital -= bet_size` / `cycle_spent += bet_size` — `order["amount"]`
  o noktada zaten elde mevcutken hiç kullanılmıyordu.
- `_execute_approved_orders()` (onay kuyruğu yolu, eski satır 1129):
  `capital -= amount` — aynı desen.

**Etki:**
1. `cycle_budget = min(capital*0.18, $20)` (satır ~656) bu cycle için
   açık bir risk tavanı; `cycle_spent >= cycle_budget` kontrolü (`CYCLE_CAP`,
   satır ~671) gerçek maliyetten daha düşük bir sayaçla karşılaştırıldığından
   bir cycle kendi bütçesini fiilen aşabiliyordu.
2. Daha önemlisi: aynı cycle'da birden fazla sinyal işlenirken, her sonraki
   sinyal için `compute_bet_size()`'ın `position_cap = capital *
   max_position_pct` hesabı (CLAUDE.md'nin **non-negotiable** "Max tek
   pozisyon: %20" kuralı) bu şişirilmiş `capital` üzerinden yapılıyordu —
   yani gerçekte kalmayan sermayeye göre sonraki emirler boyutlandırılıyordu.
3. Bu, dashboard'un düşük `min_bet` (1-5$) ayarlarını kullanan küçük
   hesaplarda en sık ve en büyük etkiyle tetikleniyor — CLAUDE.md'nin
   sermaye koruma kurallarının en kritik olduğu tam senaryo.

Sim modu (`_simulate()`, `core/polymarket_client.py:805`) bu tabanı hiç
uygulamıyor (`amount`'u aynen döndürüyor), bu yüzden bu ortamda (canlı API
key yok) hata sadece statik kod incelemesiyle tespit edilebildi — testlerde/
simülasyonda gözlemlenemez, sadece gerçek CLOB emirlerinde tetiklenir.

### Düzeltme
- `agents/orchestrator.py` (`_cycle`): `capital -= bet_size` /
  `cycle_spent += bet_size` → `real_cost = order.get("amount", bet_size)`
  sonrası `capital -= real_cost` / `cycle_spent += real_cost`.
- `agents/orchestrator.py` (`_execute_approved_orders`): `capital -= amount`
  → `capital -= order.get("amount", amount)`.
- Bond scanner yolu (`_bond_cycle`, satır ~1191, `bond_capital -= bet_size`)
  kasıtlı olarak dokunulmadı: farklı bir fonksiyon kullanıyor
  (`place_passive_order`, 5-share taban zorlaması yok) ve `BOND_ENABLED`
  varsayılan olarak `false` — önceki incelemelerin (16. çalışma) zaten tespit
  ettiği "varsayılan olarak devre dışı, ayrı bir konu" kategorisinde.
- `tests/test_capital_decrement_uses_real_order_cost.py` eklendi: (1) her iki
  canlı emir yolunun artık `order["amount"]`'ı kullandığını kaynak
  incelemesiyle kilitliyor ve eski hatalı formun geri gelmediğini doğruluyor,
  (2) CLOB'un 5-share tabanının gerçek maliyeti istenen tutarın 3 katından
  fazla şişirebildiğini bağımsız bir hesapla gösteriyor.

## Doğrulama
- İzole worktree'de PR #40: `pytest tests/` → 623 passed, 2 skipped (merge
  öncesi doğrulama).
- Merge sonrası + bugünkü düzeltme: `pytest tests/` → **626 passed, 2
  skipped** (623'ten 626'ya: 3 yeni test eklendi, mevcut testlerden hiçbiri
  bozulmadı).
- `python -c "import agents.orchestrator"` → hatasız.

## Sonuç
20. çalışma önce bekleyen PR #40'ı (research regime'in hiç canlı okunmaması)
doğrulayıp merge etti, sonra 19 önceki incelemenin hiç bakmadığı bir alanda
— CLOB emir yerleştirmenin gerçek maliyeti ile orchestrator'ın cycle-içi
sermaye sayaçları arasındaki tutarsızlık — yeni ve gerçek bir hata buldu ve
düzeltti. Bu, CLAUDE.md'nin %20 pozisyon tavanı ve cycle risk bütçesi
kurallarının gerçek canlı emirlerde (özellikle küçük/dashboard min_bet
ayarlarında) sessizce aşılabildiği bir senaryoyu kapatıyor.
