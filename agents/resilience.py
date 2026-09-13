"""
Resilience Layer — Bot'un ASLA durmamasını garanti eden koruma katmanı.

Prensipler:
1. Her hata yakalanır ve graceful degrade yapılır
2. Ağ hataları otomatik retry ile çözülür
3. API rate limit / timeout → backoff ile bekle, sonra devam
4. Bellekte birikim → periyodik temizlik
5. Sonsuz döngü koruması → cycle timeout
6. Tüm hatalar loglanır ama bot durmaz

Kullanım:
    from agents.resilience import resilient, with_retry, CycleGuard

    @resilient
    async def risky_operation():
        ...

    async with CycleGuard("cycle_name", timeout=120):
        await long_operation()
"""
from __future__ import annotations

import asyncio
import functools
import gc
import os
import sys
import time
import traceback
from contextlib import asynccontextmanager
from typing import Any, Callable, TypeVar

from loguru import logger

T = TypeVar("T")


# ─── Python 3.10 compat: asyncio.timeout was added in 3.11 ───
@asynccontextmanager
async def _compat_timeout(seconds: float):
    """asyncio.timeout() polyfill for Python 3.10."""
    if hasattr(asyncio, "timeout"):
        async with asyncio.timeout(seconds):
            yield
    else:
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        handle = loop.call_later(seconds, task.cancel)  # type: ignore[union-attr]
        try:
            yield
        except asyncio.CancelledError:
            raise asyncio.TimeoutError()
        finally:
            handle.cancel()


# ═══════════════════════════════════════════════════════════════════
# DEKORATÖRLER
# ═══════════════════════════════════════════════════════════════════

def resilient(func: Callable) -> Callable:
    """
    Herhangi bir async fonksiyonu resilient yapar.
    Hata durumunda None döner, bot durmaz.
    """
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except asyncio.CancelledError:
            logger.warning(f"[RESILIENCE] {func.__name__} cancelled — propagating")
            raise  # CancelledError propagate edilmeli
        except Exception as e:
            logger.error(
                f"[RESILIENCE] {func.__name__} failed: {type(e).__name__}: {e}\n"
                f"{traceback.format_exc()}"
            )
            return None
    return wrapper


def resilient_sync(func: Callable) -> Callable:
    """Sync fonksiyonlar için resilient dekoratör."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(
                f"[RESILIENCE] {func.__name__} failed: {type(e).__name__}: {e}"
            )
            return None
    return wrapper


async def with_retry(
    coro_func: Callable,
    *args,
    max_retries: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    on_error: str = "retry",
    **kwargs,
) -> Any:
    """
    Async fonksiyonu exponential backoff ile retry et.

    Args:
        coro_func: Çağrılacak async fonksiyon
        max_retries: Max deneme sayısı
        base_delay: İlk bekleme süresi (saniye)
        max_delay: Max bekleme süresi
        backoff_factor: Bekleme çarpanı
        on_error: "retry" | "skip" | "default"

    Returns:
        Fonksiyonun sonucu veya None
    """
    last_error = None
    delay = base_delay

    for attempt in range(max_retries + 1):
        try:
            return await coro_func(*args, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                logger.warning(
                    f"[RETRY] {coro_func.__name__} attempt {attempt + 1}/{max_retries + 1} "
                    f"failed: {e}. Retrying in {delay:.1f}s..."
                )
                await asyncio.sleep(delay)
                delay = min(delay * backoff_factor, max_delay)
            else:
                logger.error(
                    f"[RETRY] {coro_func.__name__} exhausted {max_retries + 1} attempts. "
                    f"Last error: {last_error}"
                )

    return None


# ═══════════════════════════════════════════════════════════════════
# CYCLE GUARD — Döngü koruma
# ═══════════════════════════════════════════════════════════════════

@asynccontextmanager
async def cycle_guard(name: str, timeout: float = 120.0):
    """
    Bir döngü iterasyonunu timeout ile korur.

    Usage:
        async with cycle_guard("main_cycle", timeout=120):
            await expensive_operation()
    """
    start = time.monotonic()
    try:
        async with _compat_timeout(timeout):
            yield
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - start
        logger.error(
            f"[CYCLE_GUARD] '{name}' timed out after {elapsed:.1f}s "
            f"(limit={timeout}s). Continuing to next cycle."
        )
        raise
    except asyncio.CancelledError:
        raise
    except Exception as e:
        elapsed = time.monotonic() - start
        logger.error(
            f"[CYCLE_GUARD] '{name}' failed after {elapsed:.1f}s: "
            f"{type(e).__name__}: {e}"
        )
        raise


# ═══════════════════════════════════════════════════════════════════
# HEALTH MONITOR — Sistem sağlık kontrolü
# ═══════════════════════════════════════════════════════════════════

class HealthMonitor:
    """
    Bot'un sağlık durumunu izler ve otomatik düzeltme yapar.

    Kontrol edilen metrikler:
    - Bellek kullanımı
    - Döngü süresi ortalaması
    - Hata oranı
    - API yanıt süreleri
    """

    def __init__(self):
        self._cycle_times: list[float] = []     # Son 100 döngü süresi
        self._error_count: int = 0
        self._total_cycles: int = 0
        self._last_gc: float = time.time()
        self._gc_interval: float = 300.0        # 5dk'da bir GC
        self._consecutive_errors: int = 0
        self._max_consecutive_errors: int = 10  # 10 ardışık hata → alarm

    def record_cycle(self, duration_ms: float, had_error: bool = False):
        """Bir döngü sonucunu kaydet."""
        self._total_cycles += 1
        self._cycle_times.append(duration_ms)
        if len(self._cycle_times) > 100:
            self._cycle_times = self._cycle_times[-100:]

        if had_error:
            self._error_count += 1
            self._consecutive_errors += 1
        else:
            self._consecutive_errors = 0

        # Periyodik GC
        if time.time() - self._last_gc > self._gc_interval:
            self._run_gc()

    def _run_gc(self):
        """Garbage collection çalıştır."""
        try:
            collected = gc.collect()
            self._last_gc = time.time()
            if collected > 100:
                logger.info(f"[HEALTH] GC collected {collected} objects")
        except Exception:
            pass

    def get_health(self) -> dict:
        """Sağlık raporu."""
        avg_cycle = sum(self._cycle_times) / max(len(self._cycle_times), 1)
        error_rate = self._error_count / max(self._total_cycles, 1)

        # Bellek kullanımı (basit)
        try:
            import resource
            mem_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        except Exception:
            mem_mb = 0

        status = "HEALTHY"
        if self._consecutive_errors >= self._max_consecutive_errors:
            status = "DEGRADED"
        if error_rate > 0.5:
            status = "CRITICAL"

        return {
            "status": status,
            "total_cycles": self._total_cycles,
            "avg_cycle_ms": f"{avg_cycle:.0f}",
            "error_rate": f"{error_rate:.1%}",
            "consecutive_errors": self._consecutive_errors,
            "memory_mb": f"{mem_mb:.0f}",
        }

    def should_backoff(self) -> float:
        """
        Ardışık hata durumunda ekstra bekleme süresi öner.
        Returns: Ekstra bekleme saniyesi (0 = normal devam)
        """
        if self._consecutive_errors < 3:
            return 0
        # 3 hata → 10s, 5 hata → 30s, 10 hata → 60s
        return min(60.0, self._consecutive_errors * 5.0)


# ═══════════════════════════════════════════════════════════════════
# INFINITE LOOP WRAPPER — Bot ana döngüsü için
# ═══════════════════════════════════════════════════════════════════

async def infinite_loop(
    cycle_func: Callable,
    interval: float = 60.0,
    name: str = "main",
    health_monitor: HealthMonitor | None = None,
):
    """
    Bir async fonksiyonu sonsuz döngüde çalıştır.
    HİÇBİR hata botu durduramaz.

    Args:
        cycle_func: Her döngüde çağrılacak async fonksiyon
        interval: Döngüler arası bekleme (saniye)
        name: Döngü adı (log için)
        health_monitor: Opsiyonel sağlık izleyici
    """
    monitor = health_monitor or HealthMonitor()
    restart_count = 0

    while True:
        cycle_start = time.monotonic()
        had_error = False

        try:
            async with cycle_guard(name, timeout=max(interval * 3, 180)):
                await cycle_func()

        except asyncio.CancelledError:
            logger.info(f"[INFINITE_LOOP] '{name}' cancelled — shutting down gracefully")
            break

        except SystemExit:
            logger.info(f"[INFINITE_LOOP] '{name}' SystemExit — shutting down")
            break

        except KeyboardInterrupt:
            logger.info(f"[INFINITE_LOOP] '{name}' KeyboardInterrupt — shutting down")
            break

        except Exception as e:
            had_error = True
            restart_count += 1
            logger.error(
                f"[INFINITE_LOOP] '{name}' unhandled error #{restart_count}: "
                f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            )

        # Döngü metrikleri
        duration_ms = (time.monotonic() - cycle_start) * 1000
        monitor.record_cycle(duration_ms, had_error)

        # Backoff kontrolü
        backoff = monitor.should_backoff()
        if backoff > 0:
            logger.warning(f"[INFINITE_LOOP] Backoff: +{backoff:.0f}s (consecutive errors)")

        await asyncio.sleep(interval + backoff)
