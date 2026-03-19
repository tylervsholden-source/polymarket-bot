# Control Plane Specification

## Amac
Canli emir yolundaki tum guvenlik kapilarini merkezi bir pakette toplamak.
INC-2026-03-15-001 sonrasi olusturulmustur.

## Paket Yapisi

```
control_plane/
  __init__.py          — Re-exportlar
  types.py             — Ortak tipler (ApprovalState, LiveGateCheck, ...)
  process_lock.py      — PID-based singleton
  approval_queue.py    — Emir onay state machine
  reentry_guard.py     — Per-market cooldown
  expiry_guard.py      — Market expiry kontrolu
  live_gate.py         — 10-nokta guvenlik kapisi
```

## Tip Tanimlari (types.py)

### ApprovalState (str, Enum)
```
PENDING -> APPROVED | REJECTED | EXPIRED
APPROVED -> EXECUTED | EXECUTION_BLOCKED | EXPIRED
REJECTED, EXPIRED, EXECUTED, EXECUTION_BLOCKED -> (terminal)
```

### LiveGateCheck
```python
@dataclass
class LiveGateCheck:
    name: str       # Kontrol adi (orn: "process_lock")
    passed: bool    # Gecti mi?
    reason: str     # Basarisizsa sebep
```

### LiveGateResult
```python
@dataclass
class LiveGateResult:
    passed: bool                    # Tum kontroller gecti mi?
    checks: list[LiveGateCheck]     # 10 kontrol sonucu
    checked_at: datetime            # Kontrol zamani
    blockers -> list[str]           # Basarisiz kontrol sebepleri
    to_dict() -> dict               # JSON serializasyon
```

### ExpiryRejection
```python
@dataclass
class ExpiryRejection:
    market_id: str
    reason: str         # EXPIRED | TOO_NEAR | TOO_FAR | NO_END_DATE
    hours_to_close: float | None
    question: str
```

## Modul Detaylari

### ProcessLock
- `acquire()` -> ProcessLockInfo (basarisizsa sys.exit(1))
- `release()` — idempotent
- `is_held()` -> bool
- `is_mine()` -> bool
- `holder_pid()` -> int | None

### ApprovalQueue
- `enqueue(order_dict)` -> str (order_id)
- `approve(order_id)` -> bool
- `reject(order_id)` -> bool
- `mark_executed(order_id)` -> bool
- `block_execution(order_id, reason)` -> bool
- `get_pending()` -> list
- `get_approved()` -> list
- `cleanup_expired()` -> int

### ReentryGuard
- `is_blocked(market_id)` -> bool
- `mark_closed(market_id)` — cooldown'a ekle
- `mark_traded(market_id)` — cooldown'a ekle
- `clear(market_id)` — manuel override
- `blocked_count()` -> int

### ExpiryGuard
- `check(market)` -> ExpiryRejection | None
- `filter_markets(markets)` -> (passed, rejected)
- `hours_to_close(market)` -> float | None

### LiveGate (check_live_gate)
10 keyword-only parametre ile cagrilir, LiveGateResult doner.
Parametresi None/default olan kontroller atlanir (pass).
