# Expiry Guard Specification

## Amac
Expired veya uygunsuz zaman dilimindeki marketlere emir vermeyi engellemek.
INC-2026-03-15-001: `_hours_to_close()` None donunce filtre geciriyordu.

## Red Sebepleri

| Sebep | Kosul | Aciklama |
|-------|-------|----------|
| EXPIRED | h <= 0 | Market suresi dolmus |
| TOO_NEAR | 0 < h < min_hours | Kapanisa cok yakin |
| TOO_FAR | h > max_hours | Kapanisa cok uzak |
| NO_END_DATE | h is None | end_date bilgisi yok |

**Kritik kural**: `None` = bilinmiyor = RED (fail-safe).
Eski kodda `None` filtreden geciyordu — bu bug'a sebep oldu.

## API

```python
from control_plane.expiry_guard import ExpiryGuard

eg = ExpiryGuard(min_hours=0.0, max_hours=24.0)

# Tek market kontrolu
rejection = eg.check(market_dict)
if rejection:
    print(f"Red: {rejection.reason}, h={rejection.hours_to_close}")

# Toplu filtre
passed, rejected = eg.filter_markets(markets)

# Saat hesabi
h = eg.hours_to_close(market_dict)  # None, negatif, veya pozitif
```

## Market Dict Beklentisi

```python
{
    "condition_id": "0x...",
    "question": "Will Bitcoin go up or down?",
    "end_date_iso": "2026-03-15T15:00:00Z",  # veya endDate
}
```

Desteklenen tarih alanlari (oncelik sirasina gore):
1. `end_date_iso`
2. Date-only string (orn: "2026-03-15") -> "T23:59:00+00:00" eklenir

## Tarih Parse Kurallari

1. "Z" -> "+00:00" ile degistirilir
2. 10 karakter (date-only) -> "T23:59:00+00:00" eklenir
3. `datetime.fromisoformat()` ile parse edilir
4. tzinfo yoksa UTC varsayilir
5. Parse hatasi -> None -> NO_END_DATE

## 3 Katmanli Savunma

```
1. get_active_markets() -> end_dt > now           (API seviyesi)
2. _pre_filter()        -> h <= 0 kontrolu         (Orchestrator seviyesi)
3. ExpiryGuard.check()  -> acik red sebebi         (Control Plane seviyesi)
```
