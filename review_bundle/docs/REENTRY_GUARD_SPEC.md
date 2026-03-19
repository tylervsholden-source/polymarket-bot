# Reentry Guard Specification

## Amac
Ayni market'e tekrar giris engeli.
INC-2026-03-15-001: DOGE markete 4 kez emir verildi.

## Mekanizma

Iki katmanli koruma:

### 1. Session Set (Bellek)
- `mark_closed(market_id)` veya `mark_traded(market_id)` ile eklenir
- Bot kapanincaya kadar gecerli
- Hizli kontrol: O(1) set lookup

### 2. Persistent File (Dosya)
- `data/market_cooldowns.json` dosyasina yazilir
- Varsayilan 24 saat TTL
- Bot yeniden basladiginda yuklenir
- Format: `{"condition_id": epoch_timestamp, ...}`

## API

```python
from control_plane.reentry_guard import ReentryGuard

guard = ReentryGuard(
    cooldown_file="data/market_cooldowns.json",
    cooldown_hours=24.0,
)

# Kontrol
if guard.is_blocked(market_id):
    print("Market cooldown'da")

# Isaretle
guard.mark_closed(market_id)   # Pozisyon kapandi
guard.mark_traded(market_id)   # Emir verildi

# Manuel override
guard.clear(market_id)         # Cooldown kaldir

# Temizlik
removed = guard.cleanup_stale()  # Suresi dolanlari sil
```

## Orchestrator Entegrasyonu

1. **Sinyal filtresi**: `_reentry_guard.is_blocked(market_id)` ile kontrol
2. **Pozisyon kapanisi**: `_finalize_cycle()` -> `mark_closed(mid)`
3. **Emir verildikten sonra**: `mark_traded(market_id)`
4. **LiveGate**: reentry_guard parametresi olarak gecilir

## Dosya Formati

```json
{
  "0xabc...123": 1710518400.0,
  "0xdef...456": 1710525600.0
}
```

24 saatten eski kayitlar otomatik temizlenir.
