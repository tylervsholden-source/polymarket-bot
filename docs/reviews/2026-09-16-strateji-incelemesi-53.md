# Günlük Strateji İncelemesi — 2026-09-16 (53. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum açıldığında `main` ve çalışma dalı `dae0237` (52. çalışma, PR #86)
üzerinde eşitti — açık/merge edilmemiş PR yok, birleştirilecek bir kuyruk
yoktu. Taban test suite'i: `pytest tests/` → **748 passed, 2 skipped**.

52. çalışmanın notunda işaret edilen `control_plane/*` alanı (14. çalışmadan
beri sadece toplu ekleme commit'i görmüş, satır satır incelenmemiş) bu
turun kapsamı olarak seçildi: `entry_window_guard.py`, `live_gate.py`,
`reentry_guard.py`, `process_lock.py`, `expiry_guard.py`,
`approval_queue.py`.

## Bulgu (53.) — Readiness gate, `generated_utc` alanı eksik/boşsa yaş kontrolünü tamamen atlıyor

### Kapsam
`control_plane/live_gate.py::_check_readiness()` — 11-nokta live gate'in
3. kontrolü. `INC-2026-03-15-001` doğrudan bu kontrolün var olma
sebebiydi: incident timeline'ında operatör `readiness_verdict.json`'ı
`INSUFFICIENT_EVIDENCE`'tan `TINY_PILOT_CANDIDATE`'e **manuel olarak**
override etmişti, ve bot bu dosyayı 26 saat boyunca güvenilir kabul etti.

### Kök neden
```python
generated_str = data.get("generated_utc", "")
if generated_str:                      # ← boşsa/yoksa if bloğunun TAMAMI atlanıyor
    try:
        generated = datetime.fromisoformat(...)
        ...
        if age > timedelta(hours=max_age_hours):
            return False, f"verdict {hours_old:.1f}h eski..."
    except Exception:
        return False, "generated_utc parse hatası"

return True, ""                        # ← generated_str yoksa direkt buraya düşüyor
```

`generated_utc` alanı **var ama bozuksa** (parse edilemezse) kod doğru
şekilde fail-closed davranıyor (`except` → `False`). Ama alan **hiç yoksa
veya boş string ise**, `if generated_str:` guard'ı yaş kontrolünü tamamen
by-pass ediyor ve `return True, ""` ile geçiyor — yani "yaşı bilinmiyor"
durumu "sonsuz taze" ile eşdeğer davranıyor. Tam olarak incident'taki
senaryo: operatörün manuel yazdığı/düzenlediği veya eski formatlı bir
`readiness_verdict.json`, `generated_utc` alanını içermezse (ya da bir
üretim hatasıyla boş string olursa), 26 saatlik tazelik zorunluluğu
sessizce devre dışı kalıyor — dosya günler/haftalar eski olsa bile geçer.

Normal yazıcı yol (`monitoring/daily_review.py`) her zaman
`generated_utc`'yi dolduruyor, dolayısıyla günlük otomatik akışta bu tetiklenmiyor.
Ama bu kontrolün var olma sebebi zaten "normal yol" değil — insan
müdahalesi/manuel override/bozuk dosya senaryosu. `tests/test_live_gate.py`
bu iki durumu (alan yok / alan boş) hiç kapsamıyordu.

Reprodüksiyon (düzeltme öncesi):
```
generated_utc alanı hiç yok  -> _check_readiness() = (True, '')
generated_utc = ""           -> _check_readiness() = (True, '')
```

### Düzeltme
`control_plane/live_gate.py::_check_readiness()`: `generated_str` boşsa/yoksa
artık `return False, "generated_utc eksik — verdict yaşı doğrulanamıyor"`
ile fail-closed davranıyor. Var olan parse-hatası davranışı (`except` →
`False`) değişmedi; sadece "alan mevcut değil" durumu "alan mevcut ve
taze" ile eşitlenmekten çıkarıldı.

### Test
`tests/test_live_gate.py`'e iki yeni test eklendi:
- `test_readiness_missing_generated_utc_blocks` — `generated_utc` alanı
  hiç yoksa gate engellemeli.
- `test_readiness_empty_generated_utc_blocks` — `generated_utc=""` ise
  gate engellemeli.

Düzeltme öncesi (`git stash` ile doğrulandı): **2 failed**. Düzeltme
sonrası: **15/15 passed** (`tests/test_live_gate.py`).

Tam suite: `pytest tests/` → **750 passed, 2 skipped** (748 taban + 2 yeni
test), sıfır regresyon.

## Sonuç
`readiness` kontrolü artık "yaş bilinmiyor" durumunu "taze" yerine
"güvenilmez" olarak ele alıyor — 11-nokta live gate'in 3. kontrolü artık
incident'taki tam senaryoya (timestamp'siz/manuel bir verdict dosyası) karşı
dayanıklı. CLAUDE.md'nin risk kuralları veya diğer 10 kontrol değişmedi.

## Sıradaki tur için notlar (devralınan + yeni)
- `data/trade_memory.json`'daki `CAPITAL_LOW` uyarısı ve sim-live WR farkı
  (`docs/architecture.md`: "Sim'de NO %60-75 WR, canlıda %0 WR") hâlâ
  araştırılmayı bekliyor.
- `MC_GATE_SHADOW` loglarını izlemeye devam et; `MC_GATE_ENFORCE=true`'ya
  geçiş kararı hâlâ bekliyor.
- `control_plane/entry_window_guard.py::parse_market_start_time()` ve
  `parse_market_times()`'ın `reference_year` parametresi hardcoded
  `2026` varsayılanı kullanıyor; tüm çağıranlar bu varsayılanı kullanıyor
  (dinamik yıl geçilmiyor). 2026 içinde risksiz, ama yıl dönümünde
  (2026-12-31 → 2027-01-01) yanlış yıl hesaplanmasına yol açabilir —
  gelecekte izlenmeli.
- `control_plane/`'in geri kalanı (`types.py`, `approval_queue.py`'nin
  state machine geçiş matrisi) bu turda satır satır okundu, ek bulgu
  çıkmadı.
