# Process Lock Specification

## Amac
Ayni anda tek bot instance'i calistirilabilir.
INC-2026-03-15-001: Iki instance ayni anda calisti, biri emir verdi.

## Mekanizma

PID dosyasi bazli kilitleme:

1. `acquire()` cagirildiginda:
   - Lock dosyasi varsa, eski PID hala calisiyor mu kontrol et
   - Calisiyorsa: `sys.exit(1)` (ikinci instance baslatilmaz)
   - Calismiyorsa: Eski lock temizle, yeni PID yaz
   - Lock dosyasi yoksa: PID yaz
2. `atexit.register(release)` ile otomatik temizlik

## API

```python
from control_plane.process_lock import ProcessLock

lock = ProcessLock(lock_path="data/bot.lock")

# Baslatma sirasinda (main.py)
info = lock.acquire()   # ProcessLockInfo doner, basarisizsa exit

# Kontrol
lock.is_held()          # Lock dosyasi mevcut ve canli PID?
lock.is_mine()          # Lock bu process'e mi ait?
lock.holder_pid()       # Lock'u tutan PID (None ise yok)
lock.info()             # ProcessLockInfo (dashboard icin)

# Manuel temizlik
lock.release()          # Lock'u serbest birak
```

## ProcessLockInfo

```python
@dataclass
class ProcessLockInfo:
    pid: int            # Kilitleyen PID
    lock_file: str      # Dosya yolu
    acquired_at: float  # epoch timestamp
    is_current: bool    # Bu process mi?
```

## Cross-Platform PID Kontrolu

```python
os.kill(pid, 0)  # Signal 0: process canli mi?
# OSError/ProcessLookupError -> process olu
```

## Dosya

- Varsayilan: `data/bot.lock`
- Icerik: tek satir PID numarasi (orn: "12345")
- Parent dizin yoksa olusturulur

## Entegrasyon

### main.py
```python
from control_plane.process_lock import ProcessLock
_lock = ProcessLock()
# Canli mod baslatilirken:
_lock.acquire()
```

### LiveGate
```python
check_live_gate(process_lock=_lock, ...)
# -> is_mine() kontrolu yapar
```
