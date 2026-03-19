"""
Process Lock — Tek bot instance garantisi.

INC-2026-03-15-001 dersi: İki bot aynı anda çalıştı, biri emir verdi.
PID dosyası ile yalnızca bir canlı instance çalışabilir.
"""
from __future__ import annotations

import atexit
import os
import sys
import time
from pathlib import Path

from loguru import logger

from control_plane.types import ProcessLockInfo


class ProcessLock:
    """PID-based singleton lock.

    Kullanım:
        lock = ProcessLock()
        lock.acquire()  # Başarısızsa sys.exit(1)
        # ... bot çalışır ...
        # atexit ile otomatik release
    """

    def __init__(self, lock_path: str | Path = "data/bot.lock"):
        self._path = Path(lock_path)
        self._acquired = False

    def acquire(self) -> ProcessLockInfo:
        """Lock al. Başka instance varsa sys.exit(1)."""
        self._path.parent.mkdir(exist_ok=True)

        if self._path.exists():
            old_pid_str = self._path.read_text().strip()
            if old_pid_str.isdigit():
                old_pid = int(old_pid_str)
                if self._pid_alive(old_pid):
                    logger.error(
                        f"Başka bir bot instance'ı zaten çalışıyor (PID {old_pid}). "
                        f"Durdurmak için: kill {old_pid} veya {self._path} silin."
                    )
                    sys.exit(1)
                else:
                    logger.warning(
                        f"Eski lock dosyası (PID {old_pid}, artık çalışmıyor). Temizleniyor."
                    )

        my_pid = os.getpid()
        self._path.write_text(str(my_pid))
        self._acquired = True
        atexit.register(self.release)
        logger.info(f"Lock alındı: PID {my_pid}")

        return ProcessLockInfo(
            pid=my_pid,
            lock_file=str(self._path),
            acquired_at=time.time(),
            is_current=True,
        )

    def release(self, *_args, **_kwargs) -> None:
        """Lock'u serbest bırak. İdempotent."""
        try:
            self._path.unlink(missing_ok=True)
            self._acquired = False
        except Exception:
            pass

    def is_held(self) -> bool:
        """Lock dosyası mevcut ve canlı bir PID'e ait mi?"""
        if not self._path.exists():
            return False
        pid_str = self._path.read_text().strip()
        if not pid_str.isdigit():
            return False
        return self._pid_alive(int(pid_str))

    def holder_pid(self) -> int | None:
        """Lock'u tutan PID. Yoksa None."""
        if not self._path.exists():
            return None
        pid_str = self._path.read_text().strip()
        if pid_str.isdigit() and self._pid_alive(int(pid_str)):
            return int(pid_str)
        return None

    def is_mine(self) -> bool:
        """Lock bu process'e mi ait?"""
        return self._acquired and self.holder_pid() == os.getpid()

    def info(self) -> ProcessLockInfo:
        """Dashboard için lock durumu."""
        pid = self.holder_pid()
        if pid is None:
            return ProcessLockInfo(pid=0, lock_file=str(self._path), is_current=False)
        return ProcessLockInfo(
            pid=pid,
            lock_file=str(self._path),
            is_current=(pid == os.getpid()),
        )

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        """PID hâlâ çalışıyor mu? Cross-platform."""
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False
