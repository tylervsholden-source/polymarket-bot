import argparse
import asyncio
import sys
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

from control_plane.process_lock import ProcessLock

_lock = ProcessLock()

from agents.orchestrator import Orchestrator
from backtesting.engine import BacktestEngine
from core.dashboard import dashboard
from core.position_manager import PositionManager
from core.web_server import start as start_web


async def _run_with_dashboard():
    """Dashboard + Orchestrator'ı paralel başlat."""
    orchestrator = Orchestrator(process_lock=_lock)
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
    _lock.acquire()

    # Web dashboard her modda başlar (port 8080)
    start_web(port=8080)

    if args.dashboard:
        # Dashboard modunda loguru çıktısını kapat — Rich ekranı yönetir
        logger.remove()
        logger.add("logs/bot.log", rotation="1 day", retention="7 days", level="DEBUG")
        run_sync(_run_with_dashboard())
    else:
        logger.info("Bot canlı modda başlatılıyor...")
        orchestrator = Orchestrator(process_lock=_lock)
        run_sync(orchestrator.run())


if __name__ == "__main__":
    main()
