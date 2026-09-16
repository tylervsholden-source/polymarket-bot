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
        """Lock al. Başka instance varsa öldür ve devral.

        Windows MSYS2 ortamında PID tracking güvenilmez — bash PID vs Windows PID
        farklı. Bu yüzden: eski Python bot process'lerini aggressive öldür.
        """
        self._path.parent.mkdir(exist_ok=True)

        # ── Eski bot instance'larını öldür ──
        # NOT: PowerShell subprocess hidden window'da crash yapıyor.
        # Lock-file PID check yeterli — aşağıda old_pid varsa taskkill ile öldürülüyor.
        # self._kill_other_bot_instances()

        if self._path.exists():
            old_pid_str = self._path.read_text().strip()
            if old_pid_str.isdigit():
                old_pid = int(old_pid_str)
                if old_pid == os.getpid():
                    logger.info(f"Lock zaten bu process'e ait (PID {old_pid}). Re-acquire.")
                elif self._pid_alive(old_pid):
                    # Hâlâ yaşıyorsa force kill (taskkill — os.kill Windows'ta güvensiz)
                    logger.warning(f"Eski bot hâlâ çalışıyor (PID {old_pid}), öldürülüyor...")
                    try:
                        if sys.platform == "win32" or os.name == "nt":
                            import subprocess as _sp
                            _sp.run(["taskkill", "/F", "/PID", str(old_pid)],
                                    capture_output=True, timeout=5)
                        else:
                            # BUG: this branch only ever ran `taskkill`, a Windows-only
                            # binary. On Linux/macOS (the actual deploy platform —
                            # see INC-2026-03-15-001, which this lock exists to
                            # prevent) `subprocess.run(["taskkill", ...])` always
                            # raises FileNotFoundError, silently swallowed by the
                            # bare `except Exception: pass` below. The old process
                            # was never killed, yet execution fell through and wrote
                            # our own PID over its lock file anyway — both processes
                            # then believed `is_mine()` was True (it only trusts the
                            # in-memory flag, never the file) and both placed real
                            # orders concurrently, exactly the incident this class's
                            # own docstring says acquire() must prevent. Use a real
                            # POSIX kill (SIGTERM, then SIGKILL) instead.
                            import signal as _signal
                            os.kill(old_pid, _signal.SIGTERM)
                            for _ in range(10):  # up to ~1s
                                if not self._pid_alive(old_pid):
                                    break
                                time.sleep(0.1)
                            if self._pid_alive(old_pid):
                                os.kill(old_pid, _signal.SIGKILL)
                        time.sleep(1)
                    except Exception as kill_err:
                        logger.debug(f"Eski bot öldürme hatası: {kill_err}")

                    # Kill'in gerçekten işe yarayıp yaramadığını doğrula — işe
                    # yaramadıysa lock'u sessizce çalmak iki canlı instance'ın
                    # aynı anda emir vermesine yol açar (bkz. yukarıdaki not).
                    # Sınıf docstring'i "Başarısızsa sys.exit(1)" diyor; eskiden
                    # bu kod yolunda hiçbir sys.exit çağrısı yoktu.
                    if self._pid_alive(old_pid):
                        logger.error(
                            f"Eski bot (PID {old_pid}) öldürülemedi — lock devralınamıyor, "
                            "çift instance riski var. Çıkılıyor."
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
        """Lock bu process'e mi ait?

        Sadece in-memory _acquired flag'ına güven.
        Dosya PID kontrolü Windows'ta subprocess PID karışıklığına
        neden oluyordu (system python vs .venv python).
        acquire() başarılıysa lock bizimdir — başka process değiştiremez.
        """
        return self._acquired

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
    def _kill_other_bot_instances() -> None:
        """Windows'ta diğer bot Python process'lerini öldür.

        taskkill /F /PID kullan — os.kill(pid, 9) Windows'ta process grubunu
        etkileyebiliyor ve kendi process'imizi de öldürebiliyor.
        """
        my_pid = os.getpid()
        logger.info(f"CLEANUP: my_pid={my_pid}, diğer main.py instance'ları aranıyor...")
        try:
            import subprocess
            # PowerShell: tüm python process'lerini PID ve CommandLine ile listele
            ps_cmd = (
                "Get-CimInstance Win32_Process "
                "-Filter \"Name like '%python%'\" "
                "| Select-Object ProcessId,CommandLine "
                "| ForEach-Object { "
                "Write-Host \"$($_.ProcessId)|$($_.CommandLine)\" }"
            )
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", ps_cmd],
                capture_output=True, text=True, timeout=15,
            )
            killed = 0
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line or "main.py" not in line:
                    continue
                parts = line.split("|", 1)
                if not parts[0].strip().isdigit():
                    continue
                pid = int(parts[0].strip())
                if pid != my_pid and pid > 0:
                    try:
                        # taskkill: sadece hedef PID'i öldürür, process grubuna dokunmaz
                        subprocess.run(
                            ["taskkill", "/F", "/PID", str(pid)],
                            capture_output=True, timeout=5,
                        )
                        killed += 1
                        logger.warning(f"Eski bot instance öldürüldü: PID {pid}")
                    except Exception:
                        pass
            logger.info(f"CLEANUP: tamamlandı, {killed} instance öldürüldü")
        except Exception as e:
            logger.debug(f"Bot cleanup hatası (önemsiz): {e}")

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        """PID hâlâ çalışıyor mu? Cross-platform.

        Windows'ta os.kill(pid, 0) güvenilmez — farklı python.exe
        instance'ları aynı PID'i paylaşabilir. tasklist ile doğrula.
        """
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            return False

        # Windows: ek doğrulama — PID gerçekten python mu?
        if sys.platform == "win32" or os.name == "nt":
            try:
                import subprocess
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True, timeout=5,
                )
                output = result.stdout.lower()
                return "python" in output
            except Exception:
                pass  # tasklist başarısız → os.kill sonucuna güven

        return True
