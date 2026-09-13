# Günlük Strateji İncelemesi — 2026-09-13 (19. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum özeti
- Bu oturum açıldığında `main` (`22f1ed8`) üzerinde zaten açık bir PR
  bekliyordu: #37, "18. çalışma" — `agents/subagents/research_agent.py`'nin
  her döngüde yeni bir `WhaleTracker` (ve kapatılmayan bir `httpx.AsyncClient`)
  oluşturması bugı. İzole bir git worktree'de bağımsız doğrulandı:
  `requirements.txt` kuruldu, `pytest tests/` → 612 passed, 2 skipped;
  yeni regresyon testi (`test_research_agent_whale_tracker_reused.py`) PR'ın
  fix commit'inden önceki `research_agent.py` sürümüyle (`git checkout
  3d19f29 -- ...`) çalıştırılıp gerçekten FAIL ettiği (5 instance / 5 döngü),
  fix sonrası PASS ettiği doğrulandı. PR squash-merge edildi (`22f1ed8`).
- `data/control.json`/`positions.json`/`.env` bu ortamda yok → gerçek API
  kimlik bilgisi veya canlı pozisyon yok; bu çalışma tamamen statik kod
  incelemesi + testle doğrulama şeklinde yürütüldü.
- `data/positions_backup.json` ve `.bak`/`.bak2` dosyaları geçmiş simülasyon
  koşularından kalma (Mart 2026 tarihli), gerçek canlı sermaye değil.

## Bug: `cycle_guard()` hataları yutuyor, çağıranın `had_error` tespiti hiç tetiklenmiyor

`agents/resilience.py`'deki `cycle_guard()` (async context manager),
`agents/orchestrator.py::Orchestrator.run()`'daki ana döngünün **tüm**
gövdesini sarmalıyor (`_check_hot_reload`, `top_trader.refresh`,
`kalshi_arb.refresh`, `self._cycle()`, `self._analyze_new_closed_trades()`).

Fix öncesi kod:
```python
@asynccontextmanager
async def cycle_guard(name: str, timeout: float = 120.0):
    start = time.monotonic()
    try:
        async with _compat_timeout(timeout):
            yield
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - start
        logger.error(f"[CYCLE_GUARD] '{name}' timed out after {elapsed:.1f}s ...")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        elapsed = time.monotonic() - start
        logger.error(f"[CYCLE_GUARD] '{name}' failed after {elapsed:.1f}s: ...")
```

`TimeoutError` ve genel `Exception` dallarında **`raise` yok** — sadece log
yazıp normal dönüyor. `orchestrator.run()` tarafında ise:
```python
had_error = False
try:
    async with cycle_guard("main_cycle", timeout=...):
        ...
        await self._cycle()
        ...
except asyncio.CancelledError:
    break
except KeyboardInterrupt:
    break
except Exception as e:
    had_error = True
    logger.error(f"Döngü hatası (devam ediyor): {e}")

cycle_ms = (time.time() - cycle_start) * 1000
self._health_monitor.record_cycle(cycle_ms, had_error)
...
backoff = self._health_monitor.should_backoff()
```

`self._cycle()` içinde (ya da wrap edilen diğer adımlardan herhangi birinde)
`cycle_guard`'ın yakalamadığı türden olmayan **her** exception, `cycle_guard`
tarafından yutulup log'lanıyor ve `async with` bloğu normal tamamlanmış gibi
çıkıyor. Sonuç: `orchestrator.run()`'daki `except Exception as e: had_error =
True` satırına hiçbir zaman düşülmüyor — bu dal, `cycle_guard` her şeyi
kendi içinde yuttuğu sürece **ölü kod**. `had_error` her zaman `False`
kalıyor.

Bunun somut etkisi `agents/resilience.py::HealthMonitor`'a zincirleme
yansıyor:
- `record_cycle(had_error=False)` her zaman çağrıldığından
  `_consecutive_errors` hiç artmıyor → `should_backoff()`
  (3 ardışık hatada devreye girmesi gereken adaptif bekleme) **hiçbir zaman**
  tetiklenmiyor, bot %100 döngü hata oranında bile normal `interval` ile
  dönmeye devam ediyor.
- `_error_count` hiç artmadığından `get_health()`'in `status` alanı
  (`DEGRADED` / `CRITICAL`) hiçbir zaman `HEALTHY`'den çıkamıyor — dashboard
  ve periyodik health log'u, bot her döngüde sessizce patlıyor olsa bile
  "HEALTHY" raporluyor. CLAUDE.md'nin "Resilience Katmanı (Bot ASLA
  Durmaz)" bölümünün "HealthMonitor → hata oranı ... backoff hesaplama"
  vaadiyle doğrudan çelişiyor.

Aynı desen `agents/resilience.py::infinite_loop()`'da da var (aynı
`cycle_guard` kullanımı, aynı `had_error`/`restart_count` mantığı) — ama
`infinite_loop` kod tabanında hiçbir yerden çağrılmıyor (`grep -rn
"infinite_loop"` → sadece tanım), yani şu an ölü kod. Buna rağmen fix,
`cycle_guard`'ın kendisinde yapıldığı için `infinite_loop` da otomatik
düzeliyor; ayrı bir değişiklik gerekmedi.

`tests/` içinde `resilience`, `cycle_guard`, `HealthMonitor`, `had_error`
için hiçbir eşleşme yoktu (`grep -rn` boş sonuç) — bu yol daha önce hiç
test edilmemiş.

## Fix
`cycle_guard()`'ın `TimeoutError` ve genel `Exception` dallarına, zaten
`CancelledError` dalında olduğu gibi log'dan sonra `raise` eklendi.
Böylece exception, orchestrator'ın (ve `infinite_loop`'un) zaten doğru
şekilde ele aldığı dış `except Exception as e: had_error = True` bloğuna
ulaşıyor — log/timeout raporlama davranışı aynı kalıyor, sadece artık
gerçekten HealthMonitor'a besleniyor.

`tests/test_cycle_guard_propagates_errors.py` (yeni, 3 test):
1. `cycle_guard` içinde atılan `ValueError`'ın dışarı sızdığını doğrular.
2. `cycle_guard` timeout'unun `asyncio.TimeoutError` olarak dışarı sızdığını
   doğrular.
3. Orchestrator'ın gerçek try/except şeklini birebir taklit ederek 4 ardışık
   başarısız "döngü" sonrası `HealthMonitor._consecutive_errors == 4`,
   `status` alanının `DEGRADED`/`CRITICAL` olduğunu ve `should_backoff() > 0`
   olduğunu doğrular.

Fix öncesi kodla (`git stash` ile `agents/resilience.py`'yi eski haline
döndürüp) çalıştırıldığında üç testin de gerçekten FAIL ettiği doğrulandı
(`DID NOT RAISE TimeoutError`, `assert 0 == 4`), fix sonrası PASS ettiği
teyit edildi.

## Doğrulama
- `pytest tests/` → **615 passed, 2 skipped** (612 → 615: 3 yeni test,
  başka hiçbir şey bozulmadı).
- Yeni testler fix öncesi koda karşı (`git stash push --
  agents/resilience.py`) çalıştırılıp FAIL ettiği, fix sonrası PASS ettiği
  doğrulandı.
- `git status` → sadece amaçlanan 3 dosya; testlerin yan etkisi olan
  `data/autonomous_state.json` commit öncesi eski haline döndürüldü.

## Sonraki adım için notlar
- `agents/resilience.py::infinite_loop()` hâlâ kod tabanında hiçbir yerden
  çağrılmıyor (muhtemelen `orchestrator.run()`'ın kendi elle yazılmış
  döngüsüyle örtüşen, kullanılmayan bir alternatif). Canlı davranışı
  etkilemiyor, ama gelecekte "iki farklı sonsuz döngü sarmalayıcısı"
  kafa karışıklığına yol açabilir — ayrı bir temizlik kararı olarak
  bırakıldı, bu incelemenin kapsamı dışında.
