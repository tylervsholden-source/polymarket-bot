# Live Gate Specification

## Amac
Her canli emir oncesi 10-nokta guvenlik kontrolu.
Tek bir kontrol bile basarisiz olursa emir engellenir.

## 10 Kontrol Noktasi

| # | Ad | Kontrol | Basarisizlik |
|---|-----|---------|-------------|
| 1 | process_lock | Lock bu process'e ait mi? | Baska instance calisiyor |
| 2 | live_trading | control.json live_trading=true? | Canli islem kapali |
| 3 | readiness | readiness_verdict.json taze & TINY_PILOT_CANDIDATE? | Verdict eski/gecersiz |
| 4 | daily_stop | Gunluk kayip limiti asilmadi mi? | Stop-loss tetiklendi |
| 5 | position_count | Acik pozisyon < max? | Max pozisyon limiti |
| 6 | rate_limit | Son 1 saatte < max emir? | Saatlik emir limiti |
| 7 | reentry_guard | Market cooldown'da degil mi? | Market 24h cooldown |
| 8 | expiry_guard | Market expired/too near/too far degil mi? | Expiry red |
| 9 | approval | Emir onaylandi mi? | Onay bekliyor |
| 10 | capital | Yeterli sermaye var mi? | Yetersiz sermaye |

## API

```python
from control_plane.live_gate import check_live_gate

result = check_live_gate(
    process_lock=lock,           # ProcessLock instance
    control_file="data/control.json",
    readiness_file="data/readiness_verdict.json",
    readiness_max_age_hours=26.0,
    daily_loss_exceeded=False,
    open_position_count=2,
    max_open_positions=5,
    order_timestamps=[...],      # epoch timestamps
    max_orders_per_hour=3,
    market_id="0x...",
    reentry_guard=guard,         # ReentryGuard instance
    market={"condition_id": "...", "end_date_iso": "..."},
    expiry_guard=eg,             # ExpiryGuard instance
    is_approved=True,
    available_capital=100.0,
    required_capital=10.0,
)

if result.passed:
    # Emir ver
else:
    print(result.blockers)  # ["control.json: live_trading=false", ...]
```

## Davranis Kurallari

1. **Fail-safe**: Parametre None/default -> kontrol atlanir (PASS olur)
2. **Orchestrator'da tam kontrol**: Tum parametreler doldurulur
3. **Dashboard'da snapshot**: Bazi parametreler (daily_stop) tam hesaplanamaz
4. **Loglama**: Basarisiz kontroller `logger.warning` ile loglanir

## Dashboard Entegrasyonu

- `/api/gate` endpoint'i: 10 kontrolun anlik durumunu doner
- Dashboard'da sag panelde "Live Gate" karti
- Her kontrol yesil (tik) veya kirmizi (carpi) gosterilir
- 5 saniyede bir yenilenir

## Orchestrator Entegrasyonu

`_execute_approved_orders()` metodu her emir oncesi `check_live_gate()` cagirir.
Gate basarisiz olursa emir `EXECUTION_BLOCKED` yapilir ve `block_execution()` ile
kuyruktan cikarilir.
