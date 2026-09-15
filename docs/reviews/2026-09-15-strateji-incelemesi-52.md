# Günlük Strateji İncelemesi — 2026-09-15 (52. tur)

## Hedef
Canlı Polymarket kripto up/down trading botunda gerçek para kaybına, sessiz
state bozulmasına veya bozuk bir risk korumasına yol açan TEK somut,
gerçek bir doğruluk hatası bulup (fail→pass regresyon testiyle kanıtlayıp)
minimal şekilde düzeltmek.

## Yöntem
CLAUDE.md, docs/architecture.md, docs/strategy.md okundu. Son birkaç
incelemenin formatı/kapsamı için `git show 5eee979` (51. tur), `git show
9ec7acf` (50. tur), `docs/reviews/` altındaki son dosyalar ve
`git log --oneline -60` incelendi — 51 turda zaten düzeltilmiş konular
(GTC partial-fill, Monte Carlo payout formülü, NEUTRAL/LOSS karışıklığı,
%20 pozisyon tavanı bypass'ları, maker/bond cycle guard eksiklikleri,
STREAK_FILTER'ın SKIP'inin ezilmesi, vs.) tekrar aranmadı.

Ana odak: `agents/orchestrator.py`, `agents/autonomous_engine.py`,
`agents/trade_analyzer.py`, `agents/resilience.py`,
`agents/subagents/*.py`, `core/polymarket_client.py`,
`core/position_manager.py`, `strategies/kelly_criterion.py`,
`strategies/arbitrage_engine.py` (Bayesian/Edge/Stoikov/Monte Carlo
bileşenleri), maker/bond scanner döngüleri. Özellikle: PnL/capital
muhasebesi, pozisyon boyutu taban/tavanlarının bypass edilmesi, risk
korumalarının tüm döngülerde (main/maker/bond) tutarlı uygulanıp
uygulanmadığı, NEUTRAL/WIN/LOSS sınıflandırması, `None`/exception
dönüşlerinin bir pozisyonu/emri sessizce düşürüp düşürmediği.

## Bulgu — REVIEWER'ın REDUCE kararı, sermaye tabanı tarafından sessizce eziliyordu

### Sorunun yeri
`agents/subagents/coordinator.py::AgentCoordinator.run_cycle()` (REDUCE
bloğu, ~satır 309-317) ve `agents/orchestrator.py`'nin ana döngüsü
(`compute_bet_size()` çağrısı, ~satır 800-810).

### Mekanizma
Coordinator, REDUCE verdiktli her sinyal için `signal.size`'ı doğrudan
küçültüyordu:

```python
sig.size = round(sig.size * dec.suggested_size_pct, 2)
```

Bu küçültülmüş değer, orchestrator'ın ana döngüsünde doğrudan
`compute_bet_size(signal_size=signal.size, ...)`'e giriyor.
`compute_bet_size()`'ın `effective_min` tabanı, *Kelly'nin ürettiği ham*
`signal_size`'ı sermaye ölçekli bir bantta ("küçük hesap boşa çıkmasın")
tutmak için var — capital<$20 için `effective_min = min(min_bet,
capital*0.40)`, ayrıca `capital*max_position_pct`'a tavanlanıyor. Bu taban
fonksiyonu, kendisine verilen değerin "Kelly'nin edge'i zayıf olduğu için
küçük" mü yoksa "reviewer az önce risk nedeniyle bilerek küçülttü" mü
olduğunu ayırt edemiyor — ikisini de aynı şekilde tabana kadar yukarı
şişiriyor.

Sonuç: REDUCE ile küçültülmüş `signal.size`, `effective_min`'in altına
düşerse, `compute_bet_size()` onu sessizce tekrar tabana kadar yukarı
çekiyor — bazen Kelly'nin orijinal (indirim öncesi) önerisinden bile daha
büyük bir pozisyona.

### Somut senaryo
`capital=$15` (survival-mode bandı), Kelly `signal.size=$2.00` öneriyor
(zaten `%20` pozisyon tavanı olan `$3.00`'ün altında, makul bir boyut).
Reviewer ciddi risk flag'leri görüp `REDUCE` veriyor,
`suggested_size_pct=0.3` (yalnızca **$0.60** riske girilsin istiyor).

- **Düzeltme öncesi**: Coordinator `signal.size`'ı `$0.60`'a küçültüyor.
  `compute_bet_size(capital=15, signal_size=0.60, min_bet=3.0, max_bet=8.0,
  max_position_pct=0.20)` → `effective_min=$3.00`'e tabanlanıyor →
  `bet_size=$3.00`. Reviewer'ın istediğinin **5 katı**, hatta Kelly'nin
  kendi orijinal önerisinden (`$2.00`) bile büyük bir gerçek pozisyon
  açılıyor.
- Bu, aynı hata sınıfının (bir risk korumasının sessizce ezilmesi) daha
  önce `AutonomousDecisionEngine.evaluate()`'in `size_multiplier`'ı için
  düzeltilmiş haline (22. tur, `apply_risk_size_multiplier()`) çok benziyor
  — ama tamamen farklı, o zaman dokunulmamış bir yoldan: post-floor
  re-clamp değil, **pre-floor mutation** (coordinator, floor'dan önce
  `signal.size`'ı bozuyor).

### Düzeltme
1. `agents/subagents/coordinator.py`: REDUCE bloğu artık `sig.size`'ı
   **mutasyona uğratmıyor** — sadece bilgilendirme logu basıyor.
   `signal.size`, Kelly'nin ham değeri olarak `compute_bet_size()`'a kadar
   korunuyor.
2. `agents/orchestrator.py`: `compute_bet_size()`'dan hemen sonra (diğer
   risk-bazlı çarpanlardan — AUTONOMOUS/WALK_FORWARD/ADAPTIVE — önce),
   `review_decision.verdict == ReviewVerdict.REDUCE` ise
   `bet_size = apply_risk_size_multiplier(bet_size, review_decision.suggested_size_pct)`
   uygulanıyor — mevcut, zaten denetlenmiş, **re-clamp yapmayan** yardımcı
   fonksiyon tekrar kullanılıyor (REVIEWER_VETO / walk-forward / adaptive-bet
   çarpanlarıyla aynı desen).

Bu sayede REDUCE artık her zaman `compute_bet_size()`'ın tabanından SONRA,
tabanı asla geri şişirmeyecek şekilde uygulanıyor.

### Test
`tests/test_reduce_verdict_swallowed_by_capital_floor.py` (yeni):

1. `test_coordinator_does_not_pre_shrink_signal_size_for_reduce` — gerçek
   `AgentCoordinator.run_cycle()`'ı (signal_agent/reviewer_agent stub'larıyla)
   çalıştırıp, dönen `approved_signals`'daki `sig.size`'ın Kelly'nin ham
   `$2.00`'sinde kaldığını doğruluyor. **Düzeltme öncesi FAIL** (`0.6 ==
   2.0` — coordinator'ın erken mutasyonu yakalanıyor), **düzeltme sonrası
   PASS**.
2. `test_pre_floor_reduce_application_defeats_reviewer_intent` — hatanın
   mekanizmasını `compute_bet_size()` üzerinden doğrudan gösteriyor
   (eski çağrı sırası → `$3.00`, Kelly'nin orijinalinden bile büyük).
3. `test_post_floor_reduce_application_preserves_reviewer_intent` —
   düzeltmenin doğru çağrı sırasıyla (`compute_bet_size()` → sonra
   `apply_risk_size_multiplier()`) reviewer'ın `%30`'unu tam olarak
   koruduğunu doğruluyor (`$3.00 × 0.3 = $0.90`).

`python3 -m pytest tests/test_reduce_verdict_swallowed_by_capital_floor.py -v`
→ önce (fix'siz, `git stash` ile doğrulandı) **1 failed, 2 passed**;
düzeltmeyle **3 passed**.

### Doğrulama
`python3 -m pytest tests/` → **748 passed, 2 skipped, 0 failed** (yeni 3
test dahil; önceki 745'ten +3).

## Sonuç
- REVIEWER'ın `REDUCE` kararı artık `compute_bet_size()`'ın sermaye
  tabanı tarafından ezilemiyor — özellikle düşük sermayeli (`<$20`,
  survival-mode) hesaplarda, risk bazlı bir küçültme kararı gerçek parayla
  tam boyutunda (hatta daha büyük) bir pozisyona dönüşmüyor.
- CLAUDE.md'nin `%20` pozisyon tavanı ve diğer sabit risk kuralları
  değiştirilmedi — yalnızca REDUCE'un uygulanma *sırası* düzeltildi.

## Sıradaki tur için notlar
- `data/trade_memory.json`'daki `CAPITAL_LOW` uyarısı ve sim-live WR farkı
  hâlâ araştırılmayı bekliyor (önceki turlardan devralınan not).
- `MC_GATE_SHADOW` loglarını izlemeye devam et; `MC_GATE_ENFORCE=true`'ya
  geçiş kararı hâlâ bekliyor.
