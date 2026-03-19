# Approval Workflow Specification

## Amac
Her canli emrin dashboard'dan onaylanmasi zorunludur.
Dogrudan emir verme yolu kapatilmistir.

## State Machine

```
             enqueue()
               |
               v
           [PENDING]
           /   |   \
     approve  reject  (TTL expire)
        |      |        |
        v      v        v
   [APPROVED] [REJECTED] [EXPIRED]
      / |  \
execute block (TTL expire)
   |     |      |
   v     v      v
[EXECUTED] [EXECUTION_BLOCKED] [EXPIRED]
```

## Gecis Kurallari

| Kaynak | Gecerli Hedefler |
|--------|------------------|
| PENDING | APPROVED, REJECTED, EXPIRED |
| APPROVED | EXECUTED, EXECUTION_BLOCKED, EXPIRED |
| REJECTED | (terminal) |
| EXPIRED | (terminal) |
| EXECUTED | (terminal) |
| EXECUTION_BLOCKED | (terminal) |

Gecersiz gecis denemesi `ValueError` firlatir.

## Emir Yasam Dongusu

1. **Orchestrator sinyali**: ArbitrageEngine sinyal uretir
2. **enqueue()**: Emir PENDING olarak kuyruga eklenir
3. **Dashboard bildirim**: `/api/pending` endpoint'i PENDING emirleri gosterir
4. **Operator karari**: Approve veya Reject
5. **LiveGate kontrolu**: 10-nokta kontrol gecmeli
6. **Execution**: CLOB API'ye emir verilir
7. **Sonuc**: EXECUTED veya EXECUTION_BLOCKED

## Dosya Yapisi

```json
// data/pending_orders.json
[
  {
    "id": "uuid",
    "market_id": "condition_id",
    "question": "...",
    "direction": "YES|NO",
    "amount": 10.0,
    "entry_price": 0.4500,
    "edge": 0.08,
    "token_id": "...",
    "state": "pending",
    "state_history": [
      {"state": "pending", "at": "2026-03-15T14:30:00Z", "reason": ""}
    ],
    "created_at": "2026-03-15T14:30:00Z",
    "ttl_seconds": 300
  }
]
```

## TTL (Time-to-Live)

- PENDING emirler: varsayilan 300 saniye (5 dakika)
- APPROVED emirler: varsayilan 600 saniye (10 dakika)
- Suresi dolan emirler `cleanup_expired()` ile EXPIRED yapilir
- Orchestrator her dongude `cleanup_expired()` cagirir

## API Endpointleri

| Endpoint | Metod | Aciklama |
|----------|-------|----------|
| /api/pending | GET | Bekleyen ve son 20 emir |
| /api/pending/approve | POST | `{"id": "..."}` ile onayla |
| /api/pending/reject | POST | `{"id": "..."}` ile reddet |

## Backward Compatibility

`core/approval_queue.py` dosyasi `control_plane/approval_queue.py`'ye
yonlendirme (shim) yapar. Eski importlar calismaya devam eder:
```python
from core.approval_queue import enqueue, approve, reject, ...
```
