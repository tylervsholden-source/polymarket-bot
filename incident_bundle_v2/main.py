import argparse
import asyncio
import atexit
import sys
from pathlib import Path
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

# ── Singleton Lock ────────────────────────────────────────────────────────
LOCK_FILE = Path("data/bot.lock")


def _acquire_lock():
    """Tek instance garantisi: PID dosyası ile."""
    import os
    LOCK_FILE.parent.mkdir(exist_ok=True)
    if LOCK_FILE.exists():
        old_pid = LOCK_FILE.read_text().strip()
        if old_pid.isdigit() and _pid_alive(int(old_pid)):
            logger.error(
                f"Başka bir bot instance'ı zaten çalışıyor (PID {old_pid}). "
                f"Durdurmak için: kill {old_pid} veya data/bot.lock silin."
            )
            sys.exit(1)
        else:
            logger.warning(f"Eski lock dosyası (PID {old_pid}, artık çalışmıyor). Temizleniyor.")
    LOCK_FILE.write_text(str(os.getpid()))
    atexit.register(_release_lock)
    logger.info(f"Lock alındı: PID {os.getpid()}")


def _release_lock(*_args, **_kwargs):
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _pid_alive(pid: int) -> bool:
    import os
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False

from agents.orchestrator import Orchestrator
from backtesting.engine import BacktestEngine
from core.dashboard import dashboard
from core.position_manager import PositionManager
from core.web_server import start as start_web


async def _run_with_dashboard():
    """Dashboard + Orchestrator'ı paralel başlat."""
    orchestrator = Orchestrator()
    await asyncio.gather(
        dashboard.run(),
        orchestrator.run(),
    )


def run_sync(coro):
    return asyncio.run(coro)


def setup_logger():
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}")
    logger.add("logs/bot.log", rotation="1 day", retention="7 days", level="DEBUG")


def main():
    setup_logger()

    parser = argparse.ArgumentParser(description="Polymarket AI Trading Bot")
    parser.add_argument("--backtest", action="store_true", help="Backtest modunda çalıştır")
    parser.add_argument("--status", action="store_true", help="Mevcut durumu göster")
    parser.add_argument("--days", type=int, default=20, help="Backtest gün sayısı")
    parser.add_argument("--dashboard", action="store_true", help="Canlı kontrol odası aç")
    args = parser.parse_args()

    if args.status:
        pm = PositionManager()
        pm.print_status()
        return

    if args.backtest:
        logger.info("Backtest modu başlatılıyor...")
        engine = BacktestEngine(days=args.days)
        run_sync(engine.run())
        return

    # Canlı mod — tek instance garantisi
    _acquire_lock()

    # Web dashboard her modda başlar (port 8080)
    start_web(port=8080)

    if args.dashboard:
        # Dashboard modunda loguru çıktısını kapat — Rich ekranı yönetir
        logger.remove()
        logger.add("logs/bot.log", rotation="1 day", retention="7 days", level="DEBUG")
        run_sync(_run_with_dashboard())
    else:
        logger.info("Bot canlı modda başlatılıyor...")
        orchestrator = Orchestrator()
        run_sync(orchestrator.run())


if __name__ == "__main__":
    main()
