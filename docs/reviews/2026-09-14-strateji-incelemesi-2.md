# Günlük Strateji İncelemesi — 2026-09-14 (23. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum özeti
- Bu oturum açıldığında `origin/main` 22. çalışmanın sonucu olan PR #43'ü
  (`8dd49ab`) zaten içeriyordu, bekleyen PR yoktu. Yerel branch `origin/main`
  ile birebir aynıydı, ek bir işlem gerekmedi.
- 22 önceki inceleme `agents/orchestrator.py`, `agents/subagents/
  research_agent.py`, `agents/smart_trader_tracker.py`,
  `agents/subagents/reviewer_agent.py` ve Kelly/ArbitrageEngine'in bazı
  köşelerini kapsamlıca denetlemişti. Bu çalışma bilinçli olarak daha az
  denetlenmiş modüllere odaklandı: `core/position_manager.py`,
  `agents/autonomous_engine.py`'nin geri kalanı, `strategies/
  kelly_criterion.py`, `agents/trade_analyzer.py`, `agents/resilience.py`.

## Bugün yapılan işlem: AutonomousDecisionEngine, NEUTRAL (dolmamış emir) kapanışlarını LOSS sayıyordu

### Hata
`agents/autonomous_engine.py::_update_performance()`, win/loss sayımını ve
`evaluate()`/`get_adaptive_params()`'ın kullandığı ardışık kayıp/kazanç
streak'ini doğrudan `pnl` işaretinden hesaplıyordu:

```python
wins = [t for t in closed_trades if t.get("pnl", 0) > 0]
losses = [t for t in closed_trades if t.get("pnl", 0) <= 0]
...
elif pnl <= 0:
    if perf.consecutive_wins == 0:
        perf.consecutive_losses += 1
    else:
        break
```

`core/position_manager.py` (satır ~646, ~679, ~689), GTC emri market
kapanana kadar hiç dolmadığında pozisyonu `pnl=0.0`, `result="NEUTRAL"` ile
kapatıp USDC'yi iade ediyor — bu rutin, beklenen bir durum (likidite/execution
sonucu), gerçek bir kayıp değil. `pnl <= 0` koşulu `pnl == 0` için de doğru
olduğundan, her NEUTRAL kapanış bir LOSS gibi sayılıyordu: `win_rate`'i
düşürüyor, ardışık kayıp streak'ini uzatıyor ve gerçek bir win streak'ini
maskeleyebiliyordu — botun sinyal kalitesiyle hiç ilgisi olmadan.

Bu doğrudan canlı karara bağlı: `evaluate()` içinde
`STREAK_LOSS_THRESHOLD=2` boyut çarpanını küçültüyor, `streak>=4 and
edge<0.08` ise `action=SKIP` yapıyor (STREAK_FILTER) — yani 4 emrin arka
arkaya dolmaması (gerçek kayıp yok), iyi bir sinyali tamamen atlatabiliyordu.

Aynı kod tabanında `strategies/kelly_criterion.py::update_streak()` zaten
`result` alanına göre yalnızca `"WIN"`/`"LOSS"` eşleşmelerini sayıyor —
`"NEUTRAL"` (veya başka herhangi bir değer) örtük olarak atlanıyor (ne
streak'i kırıyor ne uzatıyor). Bu, `autonomous_engine.py`'deki `pnl`-işaret
mantığının bilinçli bir tasarım değil, gözden kaçmış bir hata olduğunu
gösteriyor: iki yerde aynı veri (`closed_trades`) iki farklı şekilde
işleniyor.

**Etki:** Sim-canlı execution farkının (bkz. `docs/architecture.md`: "Backtest
Sinirlamalari") en görünür belirtilerinden biri düşük likidite saatlerinde
emirlerin dolmaması. Bu hata tam da o senaryoda botun kendi risk motorunu
yanlış yönde tetikleyip iyi sinyalleri gereksiz yere küçültüyor/atlıyordu.

### Düzeltme
- `agents/autonomous_engine.py::_update_performance()`: win/loss sayımı ve
  ardışık streak hesaplaması `pnl` işareti yerine `result` alanına
  (`"WIN"`/`"LOSS"`, `"NEUTRAL"` atlanır) göre yapılacak şekilde değiştirildi
  — `kelly_criterion.update_streak()` ile tutarlı hale getirildi. `win_rate`
  de artık `total_trades` yerine karara bağlanmış (WIN+LOSS) işlem sayısına
  bölünüyor, NEUTRAL'lerle sulandırılmıyor.
- Başka davranış değiştirilmedi (edge ortalaması, sermaye/drawdown mantığı
  aynı kaldı).
- `tests/test_neutral_trades_not_counted_as_losses.py` eklendi (3 test):
  (1) 4 ardışık NEUTRAL kapanışın gerçek bir win streak'ini kırmadığını/LOSS
  streak'i başlatmadığını, (2) `win_rate`'in NEUTRAL'lerle sulandırılmadığını,
  (3) uçtan uca `evaluate()` ile düşük-edge (0.06) ama gerçekte iyi bir
  sinyalin, 4 dolmamış emir yüzünden STREAK_FILTER tarafından yanlışlıkla
  `SKIP` edilmediğini doğruluyor.

## Doğrulama
- Fix öncesi (mevcut `main` revizyonu): `pytest tests/test_neutral_trades_not_
  counted_as_losses.py -v` → **3 failed** (`consecutive_losses=4` (beklenen 0),
  `win_rate` sulandırılmış, `action=SKIP` (beklenen SKIP değil)).
- Fix sonrası: aynı komut → **3 passed**.
- Tam suite (fix sonrası): `pytest tests/ -q` → **634 passed, 1 failed,
  2 skipped**. Tek hata yine `tests/test_reduce_verdict_size_not_double_
  applied.py` — 22. çalışmada belgelenen, UTC 00:xx-01:xx `LOW_LIQUIDITY_
  HOURS` zaman-bazlı gate'e bağlı bilinen flaky test (bu oturum da UTC
  01:1x'te çalıştı, aynı hata bağımsız olarak tekrar doğrulandı). Kapsam
  dışı, canlı paraya etkisi yok.
- `git diff agents/autonomous_engine.py` → tek fonksiyon içinde, tek amaçlı
  minimal değişiklik.
- Test çalıştırmalarının yan etkisi olan `data/autonomous_state.json`
  commit öncesi eski haline döndürüldü.

## Sonuç
23. çalışma, önceki 22 incelemenin bakmadığı bir alanda —
`AutonomousDecisionEngine`'in performans/streak muhasebesi — yeni ve canlı
yola bağlı gerçek bir hata buldu: rutin, beklenen NEUTRAL (dolmamış emir)
kapanışları LOSS olarak sayılıp botun kendi risk-küçültme/atlama mantığını
gereksiz yere tetikliyordu. Minimal bir düzeltmeyle (kod tabanındaki mevcut
`result`-bazlı emsalle tutarlı hale getirilerek) kapatıldı ve üç regresyon
testiyle kilitlendi.
