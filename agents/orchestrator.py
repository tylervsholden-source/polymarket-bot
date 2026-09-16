"""
Orchestrator: Ana döngü koordinatörü.
6-model arbitrage engine (Bayesian + Edge + Spread + Stoikov + Kelly + Monte Carlo) kullanır.
Claude AI signal kaldırıldı — Bayesian estimator spot fiyat bazlı sinyal üretir.
"""
import asyncio
import importlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from loguru import logger

from core.dashboard import dashboard as _dash
import core.status_writer as _sw
from agents.market_index_watcher import market_watcher
from agents.binance_feed import BinanceFeed
from agents.smart_trader_tracker import SmartTraderTracker
from agents.top_trader_signal import TopTraderTracker
from agents.kalshi_arb import KalshiArbTracker
from core.polymarket_client import PolymarketClient
from core.position_manager import PositionManager
from strategies.arbitrage_engine import ArbitrageEngine
from shadow_runner.journal import JournalWriter
from shadow_runner.types import (
    DecisionSummary,
    PricingSnapshot,
    ShadowDecisionRecord,
    SignalSnapshot,
)
from execution_realism.core import compute_executable_ev
from core.approval_queue import (
    enqueue as _enqueue_order,
    get_approved as _get_approved_orders,
    mark_executed as _mark_order_executed,
    cleanup_expired as _cleanup_expired_orders,
    block_execution as _block_order_execution,
)
from control_plane.live_gate import check_live_gate
from control_plane.reentry_guard import ReentryGuard
from control_plane.expiry_guard import ExpiryGuard
from control_plane.entry_window_guard import (
    EntryWindowPolicy,
    DEFAULT_ENTRY_WINDOW_POLICY,
    check_entry_window,
)
from agents.subagents.coordinator import AgentCoordinator
from agents.subagents.reviewer_agent import ReviewVerdict
from agents.whale_tracker import WhaleTracker
from agents.latency_arb import LatencyArbEngine, LatencyArbConfig
from strategies.walk_forward import WalkForwardValidator
from strategies.maker_engine import MakerEngine
from strategies.bond_scanner import BondScanner

# ── Autonomous Engine & Trade Analyzer ──
from agents.autonomous_engine import AutonomousDecisionEngine, ActionType
from agents.trade_analyzer import TradeAnalyzer
from agents.resilience import resilient, with_retry, cycle_guard, HealthMonitor


def compute_bet_size(
    capital: float,
    signal_size: float,
    min_bet: float,
    max_bet: float,
    max_position_pct: float,
    hard_max_bet: float = 4.0,
) -> tuple[float, float]:
    """Bet size: Kelly's signal_size clamped into a capital-scaled min/max band.

    Low capital (<$20) uses a wide survival-mode band so tiny accounts stay
    tradeable; normal capital uses a tighter band. Either way, the band is
    clamped to max_position_pct of capital: CLAUDE.md's "Max tek pozisyon:
    portföyün %20'si (Kelly override yapmaz)" is non-negotiable, and the
    survival-mode floor must never override Kelly's own (already capped)
    signal_size above that ceiling.

    Returns (bet_size, effective_min) — effective_min is reused by callers
    as the floor for later size adjustments (autonomous engine, walk-forward).
    """
    if capital < 20:
        cap_pct, min_pct = 0.80, 0.40  # survival mode — $5 capital → max $4 bet
    else:
        cap_pct, min_pct = 0.12, 0.04  # MANTIKLI: $71 → max ~$8.5/trade

    effective_min = max(1.0, min(min_bet, capital * min_pct))
    effective_max = min(max_bet, capital * cap_pct, hard_max_bet)

    position_cap = capital * max_position_pct
    effective_min = min(effective_min, position_cap)
    effective_max = min(effective_max, position_cap)

    # A caller-supplied floor (e.g. the dashboard's min_bet control) can exceed
    # the ceiling above for large-capital accounts — min_pct's floor scales
    # faster with capital than hard_max_bet does. Never let the floor win.
    effective_min = min(effective_min, effective_max)

    bet_size = max(effective_min, min(effective_max, signal_size))
    return bet_size, effective_min


def apply_risk_size_multiplier(bet_size: float, multiplier: float) -> float:
    """Apply AutonomousDecisionEngine's risk-based size_multiplier to bet_size.

    Bug (22nd daily review): the call site used to re-clamp the result up to
    compute_bet_size()'s `effective_min` — i.e.
    `bet_size = max(effective_min, bet_size * multiplier)`. `effective_min`
    exists to keep a *Kelly-derived* signal_size within a tradeable
    capital-scaled band; it has nothing to do with how far the autonomous
    engine is allowed to shrink bet_size once it has decided a signal is
    risky (REVIEWER_VETO ×0.25, CRITICAL/DRAWDOWN ×0.3-0.5, LOSS_STREAK
    ×0.5-0.6, SURVIVAL_MODE ×0.3). Whenever bet_size already sat at or near
    effective_min — the common case for small accounts, since
    compute_bet_size() floors signal_size up to effective_min in the first
    place — multiplying by e.g. 0.25 and then re-clamping back up to
    effective_min silently threw the entire reduction away, defeating the
    exact protection the multiplier exists to apply. The walk-forward and
    adaptive-bet-multiplier adjustments a few lines below this call site
    apply their own multipliers directly with no such re-clamp; this makes
    the autonomous engine's adjustment consistent with them.
    """
    return bet_size * multiplier


def apply_adaptive_bet_multiplier(
    bet_size: float,
    multiplier: float,
    capital: float,
    max_position_pct: float,
) -> float:
    """Apply AutonomousDecisionEngine.get_adaptive_params()'s performance-based
    max_bet_multiplier to bet_size, re-clamped to CLAUDE.md's non-negotiable
    20%-of-capital position cap ("Max tek pozisyon: portföyün %20'si — Kelly
    override yapmaz").

    Bug: AGGRESSIVE mode (win_rate>65%, 10+ trades) sets max_bet_multiplier to
    1.15. compute_bet_size() already clamps bet_size to
    min(HARD_MAX_BET, capital*max_position_pct) — for capital below roughly
    $33 that position cap binds tighter than the flat $4.00 HARD_MAX_BET, so
    multiplying by 1.15 here pushed bet_size back above the cap (e.g.
    capital=$15 → cap=$3.00 → 1.15x = $3.45, still under the $4 HARD_MAX_BET
    check that ran after this, so nothing caught it). Same "boost applied
    after the cap, never re-clamped" shape as the GOLDEN_HOUR/GOOD_HOUR bug
    (39th daily review) in strategies/arbitrage_engine.py, just at a
    different multiplier and call site.
    """
    adjusted = bet_size * multiplier
    position_cap = capital * max_position_pct
    return min(adjusted, position_cap)


class Orchestrator:
    def __init__(self, process_lock=None):
        self.interval = int(os.getenv("CYCLE_INTERVAL_SECONDS", 60))
        self.client = PolymarketClient()
        self.position_manager = PositionManager()
        self.binance_feed = BinanceFeed(session=self.client.session)
        self.smart_trader = SmartTraderTracker(session=self.client.session)
        self.top_trader = TopTraderTracker(session=self.client.session)
        self.kalshi_arb = KalshiArbTracker(session=self.client.session)
        self.arb_engine = ArbitrageEngine(
            http_session=self.client.session,
            binance_feed=self.binance_feed,
            smart_trader_tracker=self.smart_trader,
            top_trader=self.top_trader,
            kalshi_arb=self.kalshi_arb,
            clob_client=self.client._clob,
        )
        # AutonomousDecisionEngine.get_adaptive_params() diğer alanları (min_edge_yes/no,
        # max_bet_multiplier) sadece cycle_interval_seconds/aggression için okunuyordu —
        # DEFENSIVE/SURVIVAL moda geçildiğinde gerçek edge eşiği/bet boyutu hiç
        # sıkılaşmıyordu. Base değerler burada saklanıyor: adaptif eşik hiçbir zaman bu
        # tasarım gereği static tabanın (0.12/0.18) ALTINA düşürmesin, sadece sıkılaştırsın.
        self._base_min_edge_yes = self.arb_engine.min_edge_yes
        self._base_min_edge_no = self.arb_engine.min_edge_no
        self._adaptive_bet_multiplier = 1.0

        # ── SUBAGENT COORDINATOR ──────────────────────────────────────
        # Multi-agent orchestration: Research + Signal (parallel) → Review (sequential)
        _enhanced = None
        try:
            from agents.enhanced_signals import EnhancedSignals
            _enhanced = EnhancedSignals(http_session=self.client.session)
        except Exception:
            pass

        self.coordinator = AgentCoordinator(
            binance_feed=self.binance_feed,
            arb_engine=self.arb_engine,
            smart_tracker=self.smart_trader,
            whale_tracker_cls=WhaleTracker,
            enhanced_signals=_enhanced,
            reviewer_model=os.getenv("REVIEWER_MODEL", "claude-sonnet-4-20250514"),
            enable_research=os.getenv("ENABLE_RESEARCH_AGENT", "true").lower() == "true",
            enable_review=os.getenv("ENABLE_REVIEWER_AGENT", "true").lower() == "true",
        )
        logger.info("Subagent Coordinator initialized (Research + Signal + Reviewer)")

        self.max_open_positions = int(os.getenv("MAX_OPEN_POSITIONS", 5))  # CLAUDE.md: max 5 açık pozisyon (non-negotiable)
        self.min_edge = float(os.getenv("MIN_EDGE_THRESHOLD", 0.08))
        self.min_market_volume = float(os.getenv("MIN_MARKET_VOLUME", 10_000))
        self.daily_stop_loss = float(os.getenv("DAILY_STOP_LOSS_PCT", 0.15))
        # Tüm zaman dilimlerine izin ver: 5m, 15m, 1h, 4h
        self.max_hours = float(os.getenv("MAX_HOURS_TO_CLOSE", 24.0))
        self.min_hours = float(os.getenv("MIN_HOURS_TO_CLOSE", 0.0))
        # Control Plane modülleri
        self._reentry_guard = ReentryGuard(
            cooldown_file="data/market_cooldowns.json",
            cooldown_hours=24.0,
        )
        self._expiry_guard = ExpiryGuard(
            min_hours=self.min_hours,
            max_hours=self.max_hours,
        )
        self._entry_window_policy = DEFAULT_ENTRY_WINDOW_POLICY

        self._cycle_count: int = 0
        # Rate limiter: saatte max N emir (INC-2026-03-15 dersi)
        self._max_orders_per_hour = int(os.getenv("MAX_ORDERS_PER_HOUR", 6))
        self._order_timestamps: list[float] = []  # son emirlerin epoch zamanları (1 saatlik pencere)

        # Pozisyon boyut limitleri
        self._min_bet = float(os.getenv("MIN_BET_SIZE", 3.0))
        self._max_bet = float(os.getenv("MAX_BET_SIZE", 8.0))
        self._max_total_exposure = float(os.getenv("MAX_TOTAL_EXPOSURE", 40.0))

        # Loss streak koruması — ardışık kayıplardan sonra durakla
        self._consecutive_losses: int = 0
        self._max_consecutive_losses: int = 3   # 3 ardışık kayıp → cooldown (mantıklı)
        self._loss_cooldown_seconds: int = 600  # 10 dk cooldown — sakinleş, düşün
        self._loss_cooldown_until: float = 0.0  # epoch — bu zamana kadar trade yapma

        # Shadow journal — rotates daily, records all evaluated candidates
        self._shadow_writer: JournalWriter | None = None
        self._shadow_writer_date: str = ""
        self._shadow_run_id: str = str(uuid.uuid4())

        self._process_lock = process_lock

        # ── Latency Arb (sinyal kaynağı olarak) ──
        self.latency_arb = LatencyArbEngine(
            client=self.client,
            position_manager=self.position_manager,
            process_lock=process_lock,
            config=LatencyArbConfig(
                bet_size=self._min_bet,
                max_open_positions=self.max_open_positions,
                min_capital=self._min_bet,
            ),
        )
        # Latency arb'ı arb engine'e sinyal kaynağı olarak bağla
        self.arb_engine.latency_arb = self.latency_arb
        logger.info("LatencyArbEngine initialized (signal source → Bayesian boost)")

        # ── Sim position tracking ──
        self._sim_trades: list[dict] = []
        self._sim_results: list[dict] = []  # WIN/LOSS results
        self._sim_target: int = int(os.getenv("SIM_TRADE_TARGET", 10))
        self._sim_seen_markets: set[str] = set()  # aynı markete tekrar girme

        # ── Regime decay guard ──
        # Track regime strength history to detect momentum loss → bounce risk
        self._regime_strength_history: list[float] = []
        self._regime_decay_pause: bool = False

        # ── OPT-6: Loss slot cooldown ──
        # Son kayıp olan slot'tan sonra 1 slot bekle (dead cat bounce pattern)
        self._last_loss_slots: set[str] = set()  # kayıp olan slotlar

        # ── OPT-7: Consecutive WIN guard ──
        # Aynı coin'de 2+ ardışık NO WIN → bounce riski yüksek → half-kelly
        # 3+ ardışık NO WIN → skip (SOL 5:25 bounce pattern)
        self._consecutive_wins_per_coin: dict[str, int] = {}  # {"SOL": 3, "BTC": 1, ...}

        # ── Walk-Forward Validation ──
        # Monitor model drift: compare train vs test performance
        self._walk_forward = WalkForwardValidator(train_window=50, test_window=20)

        # ── Hybrid Strategy Engines ──
        self._maker_enabled = os.getenv("MAKER_ENABLED", "false").lower() == "true"  # KAPALI
        self._bond_enabled = os.getenv("BOND_ENABLED", "false").lower() == "true"  # KAPALI
        self._directional_enabled = os.getenv("DIRECTIONAL_ENABLED", "true").lower() == "true"  # SADECE directional
        self._maker_engine = MakerEngine() if self._maker_enabled else None
        self._bond_scanner = BondScanner() if self._bond_enabled else None

        # ── AUTONOMOUS ENGINE + TRADE ANALYZER + HEALTH MONITOR ──
        self.autonomous_engine = AutonomousDecisionEngine()
        self.trade_analyzer = TradeAnalyzer()
        self._health_monitor = HealthMonitor()
        self._last_analyzed_count: int = 0  # Analiz edilen son closed trade index'i
        logger.info("AutonomousEngine + TradeAnalyzer + HealthMonitor initialized")

        logger.info("Orchestrator baslatildi — Arbitrage Engine (6+4 model) aktif.")
        logger.info("  + OrderbookAnalyzer, SumMonitor, TopTraderSignal, KalshiArb")

    def _sync_open_orders_from_clob(self):
        """Restart duplicate order koruması.

        CLOB API'den açık emirleri çek. Önceki instance'ın yerleştirdiği
        emirler varsa position_manager ve reentry_guard'a yükle.
        Bu sayede aynı markete tekrar giriş yapılmaz.
        """
        try:
            open_orders = self.client.get_open_orders()
            if not open_orders:
                logger.info("CLOB_SYNC: Açık emir yok, temiz başlangıç.")
                return

            synced = 0
            for order in open_orders:
                market_id = order.get("market") or order.get("asset_id", "")
                if not market_id:
                    continue

                # Zaten position_manager'da varsa atla
                if self.position_manager.has_position(market_id):
                    continue

                # Reentry guard'a ekle — bu markete tekrar girilmesin
                self._reentry_guard.mark_traded(market_id)

                # Position olarak da ekle (capital tracking için)
                price = float(order.get("price", 0) or 0)
                size = float(order.get("original_size", 0) or order.get("size", 0) or 0)
                amount = round(price * size, 4) if price and size else 0
                status = (order.get("status") or "").upper()

                if status == "MATCHED" and amount > 0:
                    self.position_manager.add_position(
                        market_id,
                        {
                            "order_id": order.get("id", market_id),
                            "outcome": "YES",  # CLOB doesn't expose side easily
                            "amount": amount,
                            "price": price,
                            "status": "matched",
                        },
                        question=f"[CLOB_SYNC] {market_id[:40]}",
                    )
                    synced += 1
                    logger.warning(
                        f"CLOB_SYNC: Önceki instance emri yüklendi — "
                        f"market={market_id[:40]} amount=${amount:.2f} price={price}"
                    )

            if synced:
                logger.warning(f"CLOB_SYNC: {synced} eski emir position_manager'a eklendi.")
            else:
                logger.info(f"CLOB_SYNC: {len(open_orders)} açık emir var ama hepsi zaten takipte.")
        except Exception as e:
            logger.error(f"CLOB_SYNC hatası (devam ediliyor): {e}")

    def _check_hot_reload(self):
        """control.json'da reload_engine=true ise tüm strateji modüllerini yeniden yükle."""
        try:
            import json
            ctrl_path = os.path.join("data", "control.json")
            with open(ctrl_path) as f:
                ctrl = json.load(f)
            if ctrl.get("reload_engine"):
                # Reload dependency chain: edge_model → arbitrage_engine
                import strategies.edge_model as _em_mod
                importlib.reload(_em_mod)
                logger.warning("HOT_RELOAD: edge_model yeniden yüklendi!")

                import strategies.arbitrage_engine as _ae_mod
                importlib.reload(_ae_mod)
                from strategies.arbitrage_engine import ArbitrageEngine as _AE
                self.arb_engine = _AE(
                    http_session=self.client.session,
                    binance_feed=self.binance_feed,
                    smart_trader_tracker=self.smart_trader,
                    top_trader=self.top_trader,
                    kalshi_arb=self.kalshi_arb,
                    clob_client=self.client._clob,
                )
                ctrl["reload_engine"] = False
                with open(ctrl_path, "w") as f:
                    json.dump(ctrl, f)
                logger.warning("HOT_RELOAD: ArbitrageEngine + EdgeModel yeniden yüklendi!")
        except Exception as e:
            logger.error(f"HOT_RELOAD HATASI — eski engine devam ediyor: {e}")

    async def run(self):
        asyncio.create_task(market_watcher.run())
        asyncio.create_task(self._live_data_loop())
        # ── Latency Arb arka plan başlat ──
        try:
            await self.latency_arb.start()
        except Exception as e:
            logger.warning(f"LatencyArb start failed: {e}")
        await self._sync_real_balance()
        # ── Restart duplicate order koruması ──
        # CLOB'dan açık emirleri çek, position_manager + reentry_guard'a yükle.
        # Önceki instance'ın yerleştirdiği emirler böylece tekrar girilmez.
        self._sync_open_orders_from_clob()
        while True:
            cycle_start = time.time()
            had_error = False
            try:
                async with cycle_guard("main_cycle", timeout=max(self.interval * 3, 180)):
                    self._check_hot_reload()
                    # Refresh top trader & kalshi data (5dk cache, non-blocking)
                    try:
                        await self.top_trader.refresh()
                    except Exception as _e:
                        logger.debug(f"TopTrader refresh: {_e}")
                    try:
                        await self.kalshi_arb.refresh()
                    except Exception as _e:
                        logger.debug(f"KalshiArb refresh: {_e}")
                    await self._cycle()

                    # ── POST-CYCLE: Yeni kapanan trade'leri analiz et ──
                    self._analyze_new_closed_trades()

            except asyncio.CancelledError:
                logger.info("Orchestrator döngüsü iptal edildi — kapatılıyor.")
                break
            except KeyboardInterrupt:
                logger.info("KeyboardInterrupt — kapatılıyor.")
                break
            except Exception as e:
                had_error = True
                logger.error(f"Döngü hatası (devam ediyor): {e}")

            # Health monitor güncelle
            cycle_ms = (time.time() - cycle_start) * 1000
            self._health_monitor.record_cycle(cycle_ms, had_error)

            # Her döngüde CLOB bakiyesini senkronize et (1 API call/30s = düşük yük)
            await self._sync_real_balance()

            # Adaptif bekleme: hata durumunda backoff
            backoff = self._health_monitor.should_backoff()
            wait_time = self.interval + backoff
            if backoff > 0:
                logger.warning(f"HEALTH_BACKOFF: +{backoff:.0f}s (ardışık hatalar)")

            # AutonomousEngine adaptif interval — gerçek sermayeyi geç, yoksa
            # perf.capital ilk trade kapanana kadar 0.0'da kalıp SURVIVAL modunu
            # yanlışlıkla kalıcı hale getirir (bkz. get_adaptive_params docstring)
            adaptive = self.autonomous_engine.get_adaptive_params(
                self.position_manager.available_capital()
            )
            if adaptive.get("cycle_interval_seconds", self.interval) != self.interval:
                wait_time = adaptive["cycle_interval_seconds"] + backoff
                logger.info(f"ADAPTIVE_INTERVAL: {wait_time:.0f}s (aggression={adaptive['aggression']})")

            # AutonomousEngine adaptif min_edge / bet boyutu — bir sonraki döngüde
            # gerçek edge gate'ine (arb_engine) ve bet sizing'e uygulanır. min_edge asla
            # base'in altına düşmez (sadece DEFENSIVE/SURVIVAL'da sıkılaştırır, NORMAL/
            # AGGRESSIVE'de get_adaptive_params()'ın base'den düşük varsayılanları
            # (0.08/0.15) statik tabanı gevşetmez).
            self.arb_engine.min_edge_yes = max(self._base_min_edge_yes, adaptive["min_edge_yes"])
            self.arb_engine.min_edge_no = max(self._base_min_edge_no, adaptive["min_edge_no"])
            self._adaptive_bet_multiplier = adaptive["max_bet_multiplier"]
            if adaptive["aggression"] != "NORMAL":
                logger.info(
                    f"ADAPTIVE_RISK: aggression={adaptive['aggression']} "
                    f"min_edge_yes={self.arb_engine.min_edge_yes:.2f} "
                    f"min_edge_no={self.arb_engine.min_edge_no:.2f} "
                    f"bet_mult={self._adaptive_bet_multiplier:.2f}"
                )

            # Her 10 döngüde sağlık raporu
            if self._cycle_count % 10 == 0:
                health = self._health_monitor.get_health()
                engine_stats = self.autonomous_engine.get_stats()
                analyzer_stats = self.trade_analyzer.get_stats()
                logger.info(
                    f"HEALTH: {health['status']} | cycles={health['total_cycles']} "
                    f"err_rate={health['error_rate']} | "
                    f"AUTO: decisions={engine_stats['session_decisions']} "
                    f"exec={engine_stats['session_executes']} skip={engine_stats['session_skips']} | "
                    f"ANALYZER: analyzed={analyzer_stats['total_analyzed']} "
                    f"patterns={analyzer_stats['pattern_count']}"
                )
                # Pattern önerileri varsa logla
                recs = self.trade_analyzer.get_recommendations()
                for rec in recs:
                    logger.info(f"  📊 RECOMMENDATION: {rec}")

            logger.info(f"Sonraki döngü {wait_time:.0f}s sonra...")
            await asyncio.sleep(wait_time)

    async def _cycle(self):
        if not self._is_live_trading() and not self._is_simulation_running():
            logger.debug("Simülasyon ve canlı işlem kapalı. Döngü atlandı.")
            return

        self._cycle_count += 1
        # Order timestamps: 1 saatten eski kayıtları temizle
        _cutoff = time.time() - 3600
        self._order_timestamps = [t for t in self._order_timestamps if t > _cutoff]
        logger.info(f"=== Döngü #{self._cycle_count}: {datetime.now().strftime('%H:%M:%S')} ===")

        # DISABLED: Binance resolution YANLIŞ SONUÇ VERİYOR
        # Binance kline boundary ≠ Polymarket market window → yanlış WIN/LOSS
        # Örnek: HYPE 9:50-9:55 → Polymarket DOWN (-0.12%) ama Binance UP (+0.11%)
        # Artık SADECE CLOB API tokens.winner kullanılıyor (position_manager.update_positions)
        # await self._check_binance_resolutions()  # KALDIRILDI — CLOB resolution güvenilir

        # Sim modda: önce açık sim trade'leri kontrol et
        if self._is_simulation_running() and self._sim_trades:
            await self._check_sim_resolutions()

        # ── WATCHDOG: CLOB BAKİYE KORUMASI ──────────────────────────
        # Her döngüde CLOB bakiye değişimini takip et.
        # Saatlik kayıp $15'ı aşarsa → botu durdur (live trading kapat).
        # Günlük kayıp $30'u aşarsa → botu durdur.
        WATCHDOG_HOURLY_LOSS_LIMIT = 15.0   # max $15 saatlik kayıp
        WATCHDOG_DAILY_LOSS_LIMIT = 30.0    # max $30 günlük kayıp
        WATCHDOG_SESSION_START_KEY = "_watchdog_session_start"

        try:
            current_balance = self.position_manager.data.get("capital", 0)
            now_ts = time.time()

            # Session başlangıç bakiyesini kaydet (bot ilk çalıştığında)
            if not hasattr(self, WATCHDOG_SESSION_START_KEY):
                setattr(self, WATCHDOG_SESSION_START_KEY, current_balance)
                self._watchdog_hourly_ref = current_balance
                self._watchdog_hourly_ts = now_ts
                self._watchdog_daily_ref = current_balance
                self._watchdog_daily_ts = now_ts
                logger.info(f"WATCHDOG_INIT: session_start=${current_balance:.2f}")

            # Saatlik referansı güncelle (her 60dk)
            if now_ts - self._watchdog_hourly_ts > 3600:
                self._watchdog_hourly_ref = current_balance
                self._watchdog_hourly_ts = now_ts

            # Günlük referansı güncelle (her 24 saat)
            if now_ts - self._watchdog_daily_ts > 86400:
                self._watchdog_daily_ref = current_balance
                self._watchdog_daily_ts = now_ts

            hourly_loss = self._watchdog_hourly_ref - current_balance
            daily_loss = self._watchdog_daily_ref - current_balance

            # WATCHDOG DISABLED — bot cash bitene kadar durmayacak
            if hourly_loss > WATCHDOG_HOURLY_LOSS_LIMIT:
                logger.warning(
                    f"WATCHDOG (LOG ONLY): Saatlik kayıp ${hourly_loss:.2f} > "
                    f"${WATCHDOG_HOURLY_LOSS_LIMIT:.2f} — devam ediyor"
                )

            if daily_loss > WATCHDOG_DAILY_LOSS_LIMIT:
                logger.warning(
                    f"WATCHDOG (LOG ONLY): Günlük kayıp ${daily_loss:.2f} > "
                    f"${WATCHDOG_DAILY_LOSS_LIMIT:.2f} — devam ediyor"
                )

            # Her 5 döngüde durum raporu
            if self._cycle_count % 5 == 0:
                logger.info(
                    f"WATCHDOG: balance=${current_balance:.2f} | "
                    f"hourly_loss=${hourly_loss:+.2f}/{WATCHDOG_HOURLY_LOSS_LIMIT} | "
                    f"daily_loss=${daily_loss:+.2f}/{WATCHDOG_DAILY_LOSS_LIMIT}"
                )
        except Exception as wd_e:
            logger.debug(f"Watchdog check error: {wd_e}")

        # ── Loss streak koruması: son kapanışlardan streak hesapla ──
        self._update_loss_streak()
        # ── Dynamic Kelly: streak multiplier güncelle ──
        closed_trades = self.position_manager.data.get("closed", [])
        self.arb_engine.kelly.update_streak(closed_trades)

        # ── Walk-Forward Validation ──
        wf_result = self._walk_forward.validate(closed_trades)
        if wf_result["recommendation"] == "STOP":
            logger.warning(f"WALK_FORWARD: STOP recommendation! test_WR={wf_result['test_wr']:.1%}")
            # Don't stop entirely, just severely reduce sizes (handled via confidence_multiplier)

        # CIRCUIT_BREAKER devre dışı — kullanıcı talebi (2026-03-21)
        # if _t.time() < self._loss_cooldown_until:
        #     remaining = int(self._loss_cooldown_until - _t.time())
        #     logger.warning(f"Loss streak cooldown aktif ({self._consecutive_losses} kayıp). {remaining}s kaldı.")
        #     _sw.update(running=True, capital=self.position_manager.available_capital(), cycle=self._cycle_count)
        #     _sw.save()
        #     return

        # Resolution check — max pozisyon kontrolünden ÖNCE çalışmalı
        # Yoksa pozisyonlar resolve olmadan bot kilitlenir
        await self.position_manager.update_positions(self.client)

        open_count: int = self.position_manager.open_position_count()
        if open_count >= self.max_open_positions and self._is_live_trading():
            logger.info(f"Max pozisyon limitinde ({open_count}/{self.max_open_positions}).")
            _sw.update(
                running=True,
                capital=self.position_manager.available_capital(),
                initial_capital=float(os.getenv("INITIAL_CAPITAL", 500)),
                cycle=self._cycle_count,
                open_positions=open_count,
                max_positions=self.max_open_positions,
                positions=self.position_manager.data.get("positions", {}),
                closed=self.position_manager.data.get("closed", []),
            )
            _sw.save()
            return

        # Market fetch — 5dk'lık crypto up/down marketler
        # Bug: min_volume=0 hardcode edilmişti; CLAUDE.md'nin "Min market hacmi: $5,000"
        # kuralı (ve .env.example'daki MIN_MARKET_VOLUME=10000) hiç uygulanmıyordu —
        # QualityFilter doğru yazılmış ama sadece scan_markets.py'de kullanılıyordu,
        # canlı orchestrator'a hiç bağlı değildi.
        markets = await self.client.get_active_markets(min_volume=self.min_market_volume)
        logger.info(f"{len(markets)} aktif market bulundu.")

        candidates = self._pre_filter(markets)
        logger.info(f"Filtre sonrası: {len(candidates)} market arbitraj analizine giriyor.")

        capital = self.position_manager.available_capital()
        # Sim modda sanal sermaye kullan (gerçek sermaye $0.75 ile trade açılamaz)
        if self._is_simulation_running() and not self._is_live_trading():
            capital = max(capital, float(os.getenv("SIM_CAPITAL", 100)))

        _dash.update("orchestrator",
            cycle=self._cycle_count,
            scanned=len(markets),
            candidates=len(candidates),
            open_pos=open_count,
            max_pos=self.max_open_positions,
            next_in=f"{self.interval}s",
        )
        _dash.update("portfolio",
            capital=capital,
            initial=float(os.getenv("INITIAL_CAPITAL", 500)),
            open=open_count,
        )

        if not candidates:
            logger.info("Uygun market bulunamadı.")
            await self._finalize_cycle(markets, candidates)
            return

        # NO token gerçek orderbook fiyatlarını ekle
        no_enriched = 0
        for m in candidates:
            no_tid = m.get("no_token_id")
            if no_tid:
                no_book = self.client.get_orderbook(no_tid)
                if no_book:
                    m["no_best_ask"] = no_book["best_ask"]
                    m["no_best_bid"] = no_book["best_bid"]
                    no_enriched += 1
        if no_enriched:
            logger.info(f"NO orderbook: {no_enriched}/{len(candidates)} market zenginleştirildi.")

        # ═══════════════════════════════════════════════════════════════
        # MULTI-AGENT PIPELINE
        # Phase 1: Research + Signal (PARALLEL)
        # Phase 2: Merge (enrich signals with research)
        # Phase 3: Review (SEQUENTIAL — Claude API veto/approve)
        # ═══════════════════════════════════════════════════════════════
        coord_result = await self.coordinator.run_cycle(
            candidates=candidates,
            capital=capital,
        )
        logger.info(f"[COORDINATOR]\n{coord_result.summary()}")

        # COIN_LIMIT + Consensus filter uygulanır (coordinator sonrası)
        all_signals = coord_result.signal_result.signals if coord_result.signal_result else []

        # Shadow journal — tüm adayları kaydet
        ctrl = self._read_control()
        min_bet_override = float(ctrl.get("min_bet", 1.0))
        _raw_for_journal = []
        for es in all_signals:
            _raw_for_journal.append(type('_S', (), {
                'market': es.market, 'direction': es.direction,
                'bayesian_prob': es.bayesian_prob, 'market_price': es.market_price,
                'edge': es.edge, 'entry_price': es.entry_price,
                'size': es.size, 'z_score': es.z_score,
                'signal_type': es.signal_type, 'reasoning': es.reasoning,
                'token_id': es.token_id, 'side_diagnostics': es.side_diagnostics,
            })())
        self._record_shadow_decisions(candidates, _raw_for_journal, min_bet_override)

        # ── COIN_LIMIT (OPT-2): max 1 coin/slot — korelasyon %99, 2 coin = 2x risk 1x bilgi ──
        all_signals = self._limit_coins_per_period(all_signals, max_per_period=1)

        # Apply COIN_LIMIT to approved_signals too (BUG FIX: execution loop
        # was using coord_result.approved_signals which bypassed COIN_LIMIT)
        allowed_market_ids = {s.market.get("condition_id") for s in all_signals}
        coord_result.approved_signals = [
            (sig, dec) for sig, dec in coord_result.approved_signals
            if sig.market.get("condition_id") in allowed_market_ids
        ]

        # ── TOPLAM RİSK LİMİTİ ──────────────────────────────────────────
        try:
            locked = self.position_manager.locked_capital()
        except AttributeError:
            locked = sum(
                p.get("amount", 0)
                for p in self.position_manager.data.get("positions", {}).values()
            )
        # Bond positions don't count against directional risk budget
        bond_locked = sum(
            p.get("amount", 0)
            for p in self.position_manager.data.get("positions", {}).values()
            if p.get("strategy") == "bond"
        )
        directional_locked = locked - bond_locked
        max_risk = min(capital * 0.18, 20.0)
        remaining_risk = max(0.0, max_risk - directional_locked)
        cycle_budget = remaining_risk
        cycle_spent = 0.0

        # ── EXECUTE APPROVED SIGNALS (coordinator-reviewed + autonomous engine) ──
        # Edge varsa her zaman girebilir — max_open_positions (5) yeterli koruma
        MAX_DIRECTIONAL = 2  # PIVOT: reduced from 5 — maker gets most capital
        directional_count = self.position_manager.pool_position_count("directional")
        for signal, review_decision in coord_result.approved_signals:
            if open_count >= self.max_open_positions:
                break
            if directional_count >= MAX_DIRECTIONAL:
                logger.info(f"DIRECTIONAL_CAP: Max {MAX_DIRECTIONAL} directional position(s) reached, skipping.")
                break
            if cycle_spent >= cycle_budget:
                logger.info(f"CYCLE_CAP: Döngü bütçesi doldu (${cycle_spent:.2f}/${cycle_budget:.2f})")
                break

            market = signal.market
            market_id = market["condition_id"]

            if self.position_manager.has_position(market_id):
                continue
            if self._reentry_guard.is_blocked(market_id):
                continue

            # ═══════════════════════════════════════════════════════════
            # AUTONOMOUS DECISION ENGINE — risk analizi + adaptif boyut
            # Bot asla durmaz: SKIP bile sadece bu trade'i atlar, döngü devam eder
            # ═══════════════════════════════════════════════════════════
            try:
                auto_decision = self.autonomous_engine.evaluate(
                    signal=signal,
                    review_decision=review_decision,
                    capital=capital,
                    open_count=open_count,
                    max_positions=self.max_open_positions,
                    closed_trades=closed_trades,
                )
                logger.info(
                    f"[AUTONOMOUS] {market['question'][:40]} → {auto_decision.summary()}"
                )

                if not auto_decision.should_execute:
                    logger.info(
                        f"[AUTONOMOUS] SKIP: {market['question'][:40]} | "
                        f"Reason: {' | '.join(auto_decision.reasoning)}"
                    )
                    continue

                # Otonom boyut çarpanı uygula
                _auto_size_mult = auto_decision.size_multiplier
            except Exception as auto_err:
                logger.warning(f"[AUTONOMOUS] Engine error (devam ediyor): {auto_err}")
                _auto_size_mult = 1.0  # Hata durumunda normal boyut

            # Bayesian güven filtresi KALDIRILDI:
            # Edge-first direction selection in arbitrage_engine already handles this.
            # This filter was killing 21%+ edge NO trades because prob=0.51 → eff=0.49 < 0.51.
            # 5 signals produced, 0 executed because of this double-filter.

            # BTC min edge kaldırıldı — 0.25 erişilemez eşikti, normal min_edge yeterli

            # ── OPT-7: CONSECUTIVE_WIN_GUARD ──────────────────────────
            # Aynı coin'de 3+ ardışık NO WIN → bounce riski → SKIP
            # 2 ardışık NO WIN → half-kelly
            _coin = self._shadow_detect_asset(market.get("question", ""))
            _coin_streak = self._consecutive_wins_per_coin.get(_coin, 0)
            if _coin_streak >= 3 and signal.direction == "NO":
                logger.warning(
                    f"CONSEC_WIN_GUARD: {_coin} {_coin_streak} ardışık NO WIN → SKIP "
                    f"(bounce riski) | {market['question'][:50]}"
                )
                continue

            # Bet size: Kelly belirler, min/max cap uygula
            if signal.size <= 0:
                logger.debug(f"Kelly=0, atlanıyor: {market['question'][:50]}")
                continue
            # ── WATCHDOG: HARD BET SIZE CAP ──────────────────────────────
            # MUTLAK LİMİT: Tek bir trade ASLA $5'dan fazla olamaz.
            # Capital %5 cap da uygulanır ama $5 hard cap her durumda geçerli.
            HARD_MAX_BET = 4.0  # max $4 per trade

            # Dynamic min/max bet: scale with capital (see compute_bet_size for the
            # survival-mode band and the CLAUDE.md 20%-of-capital ceiling it respects).
            # min_bet_override is the dashboard's live min_bet control (docs/architecture.md:
            # "min_bet: dashboard 1/5/10/20$ -> orchestrator okur -> bet_size = max(min_bet, kelly)");
            # it used to be read into the shadow journal only and never reached real sizing.
            bet_size, _effective_min = compute_bet_size(
                capital=capital,
                signal_size=signal.size,
                min_bet=min_bet_override,
                max_bet=self._max_bet,
                max_position_pct=self.position_manager.max_position_pct,
                hard_max_bet=HARD_MAX_BET,
            )
            if bet_size < 1.0:
                logger.warning(f"Capital too low for any trade: ${capital:.2f}")
                break

            # ── REVIEWER REDUCE ──────────────────────────────────────────
            # agents/subagents/coordinator.py no longer bakes suggested_size_pct
            # into signal.size (52nd daily review) — doing so fed an
            # already-shrunk size into compute_bet_size()'s effective_min floor
            # above, which silently re-inflated small/REDUCE'd sizes back up,
            # discarding the reviewer's risk-based reduction. Apply it here
            # instead, directly to bet_size with no re-clamp — same pattern as
            # apply_risk_size_multiplier() below.
            if review_decision.verdict == ReviewVerdict.REDUCE:
                original_bet = bet_size
                bet_size = apply_risk_size_multiplier(bet_size, review_decision.suggested_size_pct)
                logger.info(
                    f"[REVIEWER] REDUCE: ${original_bet:.2f} × "
                    f"{review_decision.suggested_size_pct:.2f} = ${bet_size:.2f}"
                )

            # ── AUTONOMOUS ENGINE SIZE ADJUSTMENT ──
            if _auto_size_mult < 1.0:
                original_bet = bet_size
                bet_size = apply_risk_size_multiplier(bet_size, _auto_size_mult)
                logger.info(
                    f"[AUTONOMOUS] Size adjust: ${original_bet:.2f} × {_auto_size_mult:.2f} "
                    f"= ${bet_size:.2f}"
                )

            # ── Walk-Forward Confidence Adjustment ──
            wf_mult = self._walk_forward.last_check.get("confidence_multiplier", 1.0)
            if wf_mult < 1.0:
                bet_size *= wf_mult
                logger.info(f"WALK_FORWARD: bet_size adjusted by {wf_mult:.2f} → ${bet_size:.2f}")

            # ── AutonomousEngine adaptive bet multiplier (performans bazlı) ──
            # get_adaptive_params()'ın max_bet_multiplier'ı önceden hiç bet_size'a
            # uygulanmıyordu (run()'da sadece cycle_interval_seconds okunuyordu) —
            # DEFENSIVE/SURVIVAL'da gerçek sipariş boyutu küçülmüyordu.
            if self._adaptive_bet_multiplier != 1.0:
                original_bet = bet_size
                bet_size = apply_adaptive_bet_multiplier(
                    bet_size,
                    self._adaptive_bet_multiplier,
                    capital,
                    self.position_manager.max_position_pct,
                )
                logger.info(
                    f"[ADAPTIVE] Bet multiplier: ${original_bet:.2f} × "
                    f"{self._adaptive_bet_multiplier:.2f} = ${bet_size:.2f}"
                )

            # Final hard cap — hiçbir koşulda aşılmaz
            if bet_size > HARD_MAX_BET:
                logger.warning(f"HARD_CAP: ${bet_size:.2f} → ${HARD_MAX_BET:.2f} (max bet limit)")
                bet_size = HARD_MAX_BET
            # Capital sufficiency: aynı cycle'da 2+ order → capital tükendi mi?
            if bet_size > capital:
                logger.info(f"Yetersiz capital: ${capital:.2f} < ${bet_size:.2f}, atlıyorum.")
                continue

            # ── Mum içi giriş zamanlaması ──
            # Edge disappears in 30-60 seconds after market open.
            # Old filter (15-270s) was blocking the ONLY tradeable window.
            # BTC 3:20 market: edge=+0.20 at candle_sec=3, gone by candle_sec=65.
            # New: allow entry from second 3 onwards (API needs ~3s to process).
            # Late filter: still skip last 20s (edge fully eroded).
            candle_sec = int(time.time()) % 300
            if candle_sec > 280:
                logger.debug(
                    f"Mum zamanlaması: {candle_sec}sn > 280sn (son 20sn), "
                    f"atlıyorum: {market['question'][:50]}"
                )
                continue

            logger.info(
                f"SİNYAL [{signal.signal_type}] [{review_decision.verdict.value}]: "
                f"{market['question'][:50]} | "
                f"Edge={signal.edge:.3f} | Confluence={signal.confluence_score:.2f} | "
                f"${bet_size:.2f} | Reviewer: {review_decision.reasoning[:80]}"
            )

            _sw.add_decision(
                market=market["question"],
                category="CRYPTO",
                prob=signal.bayesian_prob,
                price=signal.market_price,
                edge=signal.edge,
                confidence="HIGH" if signal.confluence_score > 0.6 else "MEDIUM",
                action="ORDER" if self._is_live_trading() else "SIM_BUY",
                reasoning=f"[{review_decision.verdict.value}] {signal.reasoning}",
                size=bet_size,
            )

            if self._is_live_trading():
                # direction'a göre doğru token seç
                token_id = signal.token_id or (
                    market.get("yes_token_id") if signal.direction == "YES"
                    else market.get("no_token_id")
                )

                # Toplam exposure kontrolü (bond positions excluded)
                existing_exposure = sum(
                    p.get("amount", 0)
                    for p in self.position_manager.data.get("positions", {}).values()
                    if p.get("strategy") != "bond"
                )
                if existing_exposure + bet_size > self._max_total_exposure:
                    logger.info(
                        f"Exposure limiti: ${existing_exposure:.0f}+${bet_size:.0f} > "
                        f"${self._max_total_exposure:.0f}, atlanıyor."
                    )
                    continue

                # ORCH_NO_BLOCK kaldırıldı — sinyal neyse o

                # ── 11-NOKTA LİVE GATE KONTROLÜ (sadece canlı modda) ──
                if self._is_live_trading():
                    end_iso = market.get("end_date_iso", "")
                    gate_result = check_live_gate(
                        process_lock=self._process_lock,
                        control_file="data/control.json",
                        readiness_file="data/readiness_verdict.json",
                        daily_loss_exceeded=self.position_manager.daily_loss_exceeded(self.daily_stop_loss),
                        open_position_count=open_count,
                        max_open_positions=self.max_open_positions,
                        order_timestamps=self._order_timestamps,
                        max_orders_per_hour=self._max_orders_per_hour,
                        market_id=market_id,
                        reentry_guard=self._reentry_guard,
                        market={"condition_id": market_id, "end_date_iso": end_iso} if "T" in end_iso else None,
                        expiry_guard=self._expiry_guard if "T" in end_iso else None,
                        is_approved=True,
                        available_capital=capital,
                        required_capital=bet_size,
                        market_question=market.get("question", ""),
                        entry_window_policy=self._entry_window_policy,
                    )

                    if not gate_result.passed:
                        blockers = ", ".join(gate_result.blockers)
                        logger.warning(f"LiveGate ENGELLEDİ: {market['question'][:50]} | {blockers}")
                        continue

                # ── FRESH_PRICE_ABORT ──────────────────────────────────
                # Ders #32: sinyal fiyatı ≠ execution fiyatı. Stale sinyale güvenme.
                # Execution anında taze fiyat al, kayma > edge'in %60'ı → iptal.
                if not await self._fresh_price_ok(market, market_id, signal):
                    continue

                # ── DOĞRUDAN EMİR VER (onay kuyruğu bypass) ──
                order = await self.client.place_order(
                    market_id=market_id,
                    outcome=signal.direction,
                    amount=bet_size,
                    price=signal.entry_price,
                    token_id=token_id,
                    question=market.get("question", ""),
                )
                if order:
                    self._order_timestamps.append(time.time())
                    order["outcome"] = signal.direction
                    order["token_id"] = token_id or ""
                    self.position_manager.add_position(
                        market_id, order, market["question"],
                        edge=signal.edge,
                        confluence_score=signal.confluence_score,
                        risk_flags=signal.risk_flags,
                    )
                    self._reentry_guard.mark_traded(market_id)
                    open_count += 1
                    directional_count += 1
                    # CLOB'un 5-share min-size tabani, kucuk bet_size + yuksek
                    # fiyat kombinasyonunda gercek maliyeti (order["amount"])
                    # istenen bet_size'in kat kat uzerine cikarabiliyor
                    # (orn. bet_size=$1 @ price=0.90 -> gercek $4.55). bet_size
                    # ile dusulunce bu cycle'in gercek capital/harcama
                    # gorunumu sisirilir; sonraki sinyaller icin
                    # compute_bet_size()'in %20 pozisyon tavani da bu sisirilmis
                    # capital'e gore hesaplanir.
                    real_cost = order.get("amount", bet_size)
                    capital -= real_cost
                    cycle_spent += real_cost
                    logger.success(
                        f"EMİR VERİLDİ [{review_decision.verdict.value}]: "
                        f"{market['question'][:50]} | "
                        f"${bet_size:.2f} @ {signal.entry_price:.4f}"
                    )
                else:
                    logger.error(f"Emir başarısız: {market['question'][:50]}")

                _sw.add_order(
                    market=market["question"],
                    outcome=signal.direction,
                    amount=bet_size,
                    price=signal.entry_price,
                    order_id=order.get("id", "DIRECT") if order else "FAILED",
                    status="EXECUTED" if order else "FAILED",
                    edge=signal.edge,
                )
                _sw.save()
            else:
                # ── Sim position tracking: aynı markete tekrar girme ──
                total_sim = len(self._sim_trades) + len(self._sim_results)
                if market_id not in self._sim_seen_markets and total_sim < self._sim_target:
                    self._sim_seen_markets.add(market_id)
                    sim_entry = {
                        "market_id": market_id,
                        "question": market.get("question", ""),
                        "direction": signal.direction,
                        "entry_price": signal.entry_price,
                        "bayesian_prob": signal.bayesian_prob,
                        "edge": signal.edge,
                        "size": signal.size,
                        "yes_token_id": market.get("yes_token_id"),
                        "reviewer_verdict": review_decision.verdict.value,
                        "confluence_score": signal.confluence_score,
                        "risk_flags": signal.risk_flags,
                        "ts": time.time(),
                    }
                    self._sim_trades.append(sim_entry)
                    logger.success(
                        f"[SIM #{total_sim + 1}/{self._sim_target}] "
                        f"[{review_decision.verdict.value}] "
                        f"{signal.direction} {market['question'][:50]} | "
                        f"Edge={signal.edge:.3f} Confluence={signal.confluence_score:.2f} ${signal.size:.2f}"
                    )
                else:
                    logger.info(f"[SIM] {market['question'][:50]} | ${signal.size:.2f}")
                _sw.add_order(
                    market=market["question"],
                    outcome=signal.direction,
                    amount=signal.size,
                    price=signal.entry_price,
                    order_id="SIM",
                    status="SIMULATED",
                    edge=signal.edge,
                )
                _sw.save()


        # ── Onaylanan emirleri execute et ──
        await self._execute_approved_orders()
        # ── Süresi dolmuş bekleyen emirleri temizle ──
        _cleanup_expired_orders()

        # ── Latency Arb stats log ──
        if self.latency_arb._running and self._cycle_count % 10 == 0:
            stats = self.latency_arb.get_stats()
            if stats.get("spikes_detected", 0) > 0:
                logger.info(
                    f"LATENCY_ARB_STATS: spikes={stats['spikes_detected']} "
                    f"orders={stats['orders_placed']} skipped={stats['orders_skipped']}"
                )

        # ═══════════════════════════════════════════════════════════════
        # HYBRID STRATEGIES: Maker + Bond (alongside directional above)
        # ═══════════════════════════════════════════════════════════════

        # Phase A: Market Making — refresh two-sided quotes (every cycle)
        # Must respect the same daily stop-loss / process-lock / account-wide
        # position-cap guards as Phase B (bond, 43rd daily review) and
        # _execute_approved_orders() — otherwise a losing day that trips the
        # -15% stop-loss (or a stale process lock) would not stop maker's
        # real GTC orders.
        if (
            self._maker_enabled
            and self._maker_engine
            and self._is_live_trading()
            and not self.position_manager.daily_loss_exceeded(self.daily_stop_loss)
            and (self._process_lock is None or self._process_lock.is_mine())
            and self.position_manager.open_position_count() < self.max_open_positions
        ):
            try:
                # pool_available("maker") only reads PositionManager's shared
                # ledger, which maker fills never enter — subtract what
                # MakerEngine itself already has committed to real standing
                # orders / unresolved fills, or refresh_quotes() re-commits
                # the same capital again on every cycle.
                maker_capital = max(
                    0.0,
                    self.position_manager.pool_available("maker")
                    - self._maker_engine.get_committed_capital(),
                )
                # Use ALL markets (not just candidates) — maker needs wider selection
                maker_stats = await self._maker_engine.refresh_quotes(
                    markets=markets if markets else [],
                    capital=maker_capital,
                    client=self.client,
                )
                if maker_stats.get("placed", 0) > 0:
                    logger.info(
                        f"MAKER_CYCLE: placed={maker_stats['placed']} "
                        f"cancelled={maker_stats['cancelled']} capital=${maker_capital:.2f}"
                    )
            except Exception as maker_err:
                logger.error(f"MAKER_CYCLE error: {maker_err}")

        # Phase B: Bond scan — every 5th cycle (~5 min)
        # Must respect the same master live-trading switch as Phase A
        # (maker) and every other real-order path (_cycle direct orders,
        # _execute_approved_orders) — otherwise turning off control.json's
        # live_trading (e.g. dashboard pause, or LIVE_TRADING_ENABLED/
        # readiness failing) does not stop bond orders from being placed.
        if (
            self._bond_enabled
            and self._bond_scanner
            and self._cycle_count % 5 == 0
            and self._is_live_trading()
        ):
            try:
                await self._bond_cycle()
            except Exception as bond_err:
                logger.error(f"BOND_CYCLE error: {bond_err}")

        await self._finalize_cycle(markets, candidates)

    async def _fresh_price_ok(self, market: dict, market_id: str, signal) -> bool:
        """Execution anındaki taze fiyatı kontrol et; kayma edge'in %60'ını aşarsa False dön.

        NOT: get_market() (Gamma API) sadece YES-side best_ask/best_bid alanlarını
        normalize eder — no_best_ask hiç set edilmez. Bu yüzden NO yönü için
        no_token_id'nin kendi orderbook'u ayrıca çekilir (market taramasında
        no_best_ask'ın doldurulduğu yöntemle aynı).
        """
        try:
            if signal.direction == "YES":
                fresh = await self.client.get_market(market_id)
                fresh_ask = float(fresh.get("best_ask", 0) or 0) if fresh else 0.0
            else:
                no_tid = market.get("no_token_id")
                no_book = self.client.get_orderbook(no_tid) if no_tid else None
                fresh_ask = float(no_book.get("best_ask", 0) or 0) if no_book else 0.0

            if fresh_ask > 0:
                slippage = abs(fresh_ask - signal.entry_price)
                if slippage > signal.edge * 0.60:
                    logger.warning(
                        f"FRESH_PRICE_ABORT: {market['question'][:40]} | "
                        f"stale={signal.entry_price:.3f} fresh={fresh_ask:.3f} "
                        f"slip={slippage:.3f} > edge*0.6={signal.edge * 0.60:.3f}"
                    )
                    return False
        except Exception as e:
            logger.debug(f"Fresh price fetch failed: {e}")
        return True

    async def _execute_approved_orders(self):
        """Dashboard'dan onaylanan emirleri gerçekten execute et.

        Her emir öncesi 10-nokta LiveGate kontrolü çalışır.
        Herhangi bir kontrol başarısız olursa emir EXECUTION_BLOCKED olur.
        """
        approved = _get_approved_orders()
        if not approved:
            return

        # Live gate için ortak parametreler (döngü başına 1 kez hesapla)
        _lock = self._process_lock
        capital = self.position_manager.available_capital()
        open_count = self.position_manager.open_position_count()
        daily_stop = self.position_manager.daily_loss_exceeded(self.daily_stop_loss)

        for order_req in approved:
            # Re-fetch capital/open_count per order (stale after previous execution)
            capital = self.position_manager.available_capital()
            open_count = self.position_manager.open_position_count()
            daily_stop = self.position_manager.daily_loss_exceeded(self.daily_stop_loss)

            market_id = order_req.get("market_id", "")
            token_id = order_req.get("token_id", "")
            amount = order_req.get("amount", 0)
            price = order_req.get("entry_price", 0)
            direction = order_req.get("direction", "YES")
            question = order_req.get("question", "")

            if not token_id:
                logger.error(f"Onaylı emir token_id eksik, atlanıyor: {question[:50]}")
                _mark_order_executed(order_req["id"])
                continue

            # ── DUPLICATE POZİSYON KORUMASI ──
            if self.position_manager.has_position(market_id):
                logger.warning(
                    f"Zaten açık pozisyon var, onaylı emir atlanıyor: {question[:50]}"
                )
                _mark_order_executed(order_req["id"])
                continue

            # ── 11-NOKTA LİVE GATE KONTROLÜ (re-check after approval delay) ──
            gate_result = check_live_gate(
                process_lock=_lock,
                control_file="data/control.json",
                readiness_file="data/readiness_verdict.json",
                daily_loss_exceeded=daily_stop,
                open_position_count=open_count,
                max_open_positions=self.max_open_positions,
                order_timestamps=self._order_timestamps,
                max_orders_per_hour=self._max_orders_per_hour,
                market_id=market_id,
                reentry_guard=self._reentry_guard,
                market={"condition_id": market_id, "end_date_iso": order_req.get("end_date_iso", "")} if "T" in order_req.get("end_date_iso", "") else None,
                expiry_guard=self._expiry_guard if "T" in order_req.get("end_date_iso", "") else None,
                is_approved=True,  # Zaten approved listesinden geldi
                available_capital=capital,
                required_capital=amount,
                market_question=question,
                entry_window_policy=self._entry_window_policy,
                is_recheck_after_approval=True,
            )

            if not gate_result.passed:
                blockers = ", ".join(gate_result.blockers)
                logger.warning(
                    f"LiveGate ENGELLEDİ: {question[:50]} | Sebepler: {blockers}"
                )
                _block_order_execution(order_req["id"], reason=blockers)
                continue

            order = await self.client.place_order(
                market_id=market_id,
                outcome=direction,
                amount=amount,
                price=price,
                token_id=token_id,
                question=question,
            )
            if order:
                self._order_timestamps.append(time.time())
                order["outcome"] = direction
                order["token_id"] = token_id or ""
                self.position_manager.add_position(market_id, order, question)
                self._reentry_guard.mark_traded(market_id)
                open_count += 1  # Sonraki emirler için güncelle
                # bkz. üstteki doğrudan emir yolundaki aynı düzeltme: CLOB'un
                # 5-share min-size tabanı yüzünden gerçek maliyet (order["amount"])
                # istenen `amount`'tan büyük olabilir.
                capital -= order.get("amount", amount)
                logger.success(
                    f"ONAYLANMIŞ EMİR VERİLDİ: {question[:50]} | "
                    f"${amount:.2f} @ {price:.4f}"
                )
            else:
                logger.error(f"Onaylı emir başarısız: {question[:50]}")
                _block_order_execution(order_req["id"], reason="CLOB_REJECTED")
                continue

            _mark_order_executed(order_req["id"])

    async def _bond_cycle(self):
        """Scan ALL markets for high-probability bond opportunities.

        _bond_cycle() places real orders via client.place_passive_order()
        directly — it never calls check_live_gate(). The 42nd daily review
        already gated the *caller* on self._is_live_trading(), but noted
        as a follow-up that bond orders still skipped the daily -15%
        stop-loss, process-lock, and account-wide position-cap checks that
        check_live_gate() (and _execute_approved_orders()) enforce for
        every other real-order path. Restored here: a losing day that
        trips daily_loss_exceeded() (or a directional loop that fills the
        last open-position slot earlier in this same cycle) must stop bond
        orders exactly like it stops directional ones.
        """
        if self.position_manager.daily_loss_exceeded(self.daily_stop_loss):
            logger.warning("BOND: Günlük -%15 stop-loss aşıldı, bond cycle atlanıyor.")
            return
        if self._process_lock is not None and not self._process_lock.is_mine():
            logger.warning("BOND: Process lock bu process'e ait değil, bond cycle atlanıyor.")
            return
        if self.position_manager.open_position_count() >= self.max_open_positions:
            logger.debug("BOND: Hesap-genelinde max pozisyon limitinde, bond cycle atlanıyor.")
            return

        bond_capital = self.position_manager.pool_available("bond")
        bond_positions = self.position_manager.pool_position_count("bond")

        if bond_capital < 3.0:
            logger.debug(f"BOND: Insufficient capital (${bond_capital:.2f})")
            return
        if bond_positions >= self._bond_scanner.MAX_POSITIONS:
            logger.debug(f"BOND: Max positions reached ({bond_positions})")
            return

        opportunities = await self._bond_scanner.scan(self.client)
        if not opportunities:
            logger.info("BOND_SCAN: No opportunities found")
            return

        placed = 0
        for opp in opportunities:
            if bond_capital < 3.0:
                break
            if bond_positions >= self._bond_scanner.MAX_POSITIONS:
                break
            if self.position_manager.open_position_count() >= self.max_open_positions:
                logger.debug("BOND: Hesap-genelinde max pozisyon limitine ulaşıldı, döngü durduruluyor.")
                break

            # Skip if already have position in this market
            if self.position_manager.has_position(opp.condition_id):
                continue
            if self._reentry_guard.is_blocked(opp.condition_id):
                continue

            # Size: max $10 per bond, max 40% of remaining bond capital
            bet_size = min(10.0, bond_capital * 0.40)
            if bet_size < 3.0:
                break

            # Place order (use passive — no price bump for bonds)
            order_result = await self.client.place_passive_order(
                token_id=opp.token_id,
                price=opp.price,
                size=round(bet_size / opp.price, 2),
                question=f"BOND_{opp.side}: {opp.question[:50]}",
            )

            if order_result and order_result.get("order_id"):
                # Register as position
                self.position_manager.add_position(
                    opp.condition_id,
                    {
                        "order_id": order_result["order_id"],
                        "outcome": opp.side,
                        "amount": order_result.get("amount", bet_size),
                        "price": opp.price,
                        "status": order_result.get("status", "LIVE"),
                        "token_id": opp.token_id,
                    },
                    question=opp.question,
                    strategy="bond",
                )
                self._reentry_guard.mark_traded(opp.condition_id)
                # bkz. yukarıdaki doğrudan emir yolu (_cycle) ve _execute_approved_orders()
                # ile aynı düzeltme: CLOB'un 5-share min-size tabanı, küçük bet_size +
                # yüksek fiyat (bond'lar 0.93-0.97 bandında) kombinasyonunda gerçek
                # maliyeti (order_result["amount"]) istenen bet_size'ın üzerine
                # çıkarabiliyor (örn. bet_size=$4 @ price=0.95 -> size 5-share tabanına
                # yuvarlanır -> gerçek $4.75). bet_size ile düşülünce bu döngünün yerel
                # bond_capital sayacı gerçekte harcanandan fazla kalan gösterir ve aynı
                # _bond_cycle() çağrısı içinde gerçekte karşılanamayacak bir sonraki
                # bond emrine izin verebilir (sıradaki gerçek bakiye senkronuna kadar).
                real_cost = order_result.get("amount", bet_size)
                bond_capital -= real_cost
                bond_positions += 1
                placed += 1
                logger.success(
                    f"BOND_ORDER: {opp.question[:50]} | {opp.side} @ {opp.price:.3f} | "
                    f"yield={opp.expected_yield:.1%} | ${real_cost:.2f} | "
                    f"resolve in {opp.days_to_resolve:.1f}d"
                )

        if placed:
            logger.info(f"BOND_CYCLE: {placed} bond(s) placed, remaining=${bond_capital:.2f}")

    def _analyze_new_closed_trades(self):
        """Yeni kapanan trade'leri analiz et — bot durmadan çalışır."""
        try:
            closed_trades = self.position_manager.data.get("closed", [])
            new_count = len(closed_trades) - self._last_analyzed_count

            if new_count <= 0:
                return

            # Sadece yeni kapananları analiz et
            new_trades = closed_trades[self._last_analyzed_count:]
            for trade in new_trades:
                try:
                    analysis = self.trade_analyzer.analyze_trade(
                        trade=trade,
                        signal_data=trade,  # Trade data sinyal bilgilerini de içerir
                        all_closed=closed_trades,
                    )
                    logger.info(
                        f"[TRADE_ANALYSIS] {analysis.summary()}"
                    )
                    for lesson in analysis.lessons:
                        logger.info(f"  📝 {lesson}")
                except Exception as e:
                    logger.debug(f"[TRADE_ANALYSIS] Single trade analysis error: {e}")

            self._last_analyzed_count = len(closed_trades)

            # Her 5 trade'de pattern raporu
            if self._last_analyzed_count > 0 and self._last_analyzed_count % 5 == 0:
                report = self.trade_analyzer.get_pattern_report()
                if report:
                    logger.info(f"[PATTERN_REPORT] {json.dumps(report, indent=2)}")
                recs = self.trade_analyzer.get_recommendations()
                for rec in recs:
                    logger.info(f"  📊 {rec}")

        except Exception as e:
            logger.debug(f"[TRADE_ANALYSIS] Batch analysis error (devam ediyor): {e}")

    async def _finalize_cycle(self, markets: list, candidates: list):
        """Pozisyonları güncelle, dashboard yaz."""
        open_before = set(self.position_manager.data.get("positions", {}).keys())
        await self.position_manager.update_positions(self.client)
        open_after = set(self.position_manager.data.get("positions", {}).keys())
        # Kapanan market_id'leri session setine ekle — aynı döngüde tekrar girilmesin
        newly_closed = open_before - open_after
        for mid in newly_closed:
            self._reentry_guard.mark_closed(mid)
        self.position_manager.print_status()

        await self._fetch_crypto_prices()
        _sw.update_indices(market_watcher._data)
        pm_data = self.position_manager.data
        # Spot fiyatları: açık pozisyonların coin'leri için Binance cache'den çek
        _spot_prices = {}
        for _pos in pm_data.get("positions", {}).values():
            _coin = self._shadow_detect_asset(_pos.get("question", ""))
            _sym = _coin + "USDT" if _coin != "UNKNOWN" else ""
            if _sym and _sym not in _spot_prices:
                _cached = self.binance_feed._cache.get(_sym, {})
                if _cached.get("price"):
                    _spot_prices[_coin] = round(_cached["price"], 4)
        _sw.update(
            running=True,
            capital=self.position_manager.available_capital(),
            initial_capital=float(os.getenv("INITIAL_CAPITAL", 500)),
            cycle=self._cycle_count,
            scanned=len(markets),
            candidates=len(candidates),
            open_positions=self.position_manager.open_position_count(),
            max_positions=self.max_open_positions,
            next_cycle_in=f"{self.interval}s",
            positions=pm_data.get("positions", {}),
            closed=pm_data.get("closed", []),
            signal_mode="arbitrage",
            spot_prices=_spot_prices,
        )
        _sw.save()

    # ------------------------------------------------------------------ #
    # Filtre: sadece BTC/ETH/SOL/XRP up-or-down, 5dk ve 15dk marketler
    # ------------------------------------------------------------------ #

    # BNB/HYPE re-enabled — 100% WR historically. Bitstamp volume low
    # but ccxt multi-exchange (OKX/Bybit/Kraken) provides reliable price data.
    _CRYPTO_UPDOWN_KEYWORDS = [
        "bitcoin up or down", "ethereum up or down",
        "btc up or down", "eth up or down",
        "solana up or down", "sol up or down",
        "xrp up or down",
        "dogecoin up or down", "doge up or down",
        "bnb up or down",
        "hype up or down", "hyperliquid up or down",
    ]

    def _pre_filter(self, markets: list) -> list:
        from zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo("America/New_York"))
        today_str = now_et.strftime("%B %d").replace(" 0", " ")  # "March 15" ET format
        # SAAT FİLTRESİ: Kaldırıldı (maker stratejisi 7/24 çalışmalı)

        result = []
        for m in markets:
            question = m.get("question", "")
            q_lower = question.lower()
            if not any(kw in q_lower for kw in self._CRYPTO_UPDOWN_KEYWORDS):
                continue
            # Sadece bugünün marketleri (March 16 gibi)
            if today_str.lower() not in q_lower:
                continue
            # SADECE 5m ve 15m marketler — 1h/4h YASAK
            horizon = self._parse_horizon_minutes(question)
            if horizon not in (5, 15):
                continue
            # Kapanışa max 20dk kalan marketler (15m marketler için yeterli)
            mins_left = self._minutes_to_market_end(question)
            if mins_left is None or mins_left <= 0 or mins_left > 20:
                continue
            # Entry window kaba filtre: çok uzak marketleri ele
            # Detaylı kontrol check_entry_window() tarafından yapılıyor
            # Pre-filter cömert olmalı — 5m: başlangıca 5dk, 15m: başlangıca 5dk
            mins_to_start = mins_left - horizon
            if mins_to_start > 5:  # 5dk'dan fazla uzaktaysa atla
                continue
            h = self._hours_to_close(m)
            if h is None or h <= 0 or h > self.max_hours or h < self.min_hours:
                continue
            # Fiyat filtresi: 0.40 - 0.75 arası (kaliteli bölge)
            # <0.40: WR=%26.5 (para kaybediyor), >0.75: az veri ama WR yüksek, riskli
            price = float(m.get("best_ask", 0) or 0)
            if not (0.40 <= price <= 0.75):
                continue
            result.append(m)
        return result

    @staticmethod
    def _extract_time_slot(question: str) -> str:
        """Market sorusundan zaman dilimini çıkar: '8:10AM-8:15AM' gibi."""
        import re
        tf_pattern = re.compile(
            r'(\d{1,2}:\d{2}\s*(?:AM|PM)\s*[-–]\s*\d{1,2}:\d{2}\s*(?:AM|PM))',
            re.IGNORECASE,
        )
        m = tf_pattern.search(question)
        return m.group(1).upper().replace(" ", "") if m else "unknown"

    def _limit_coins_per_period(self, signals: list, max_per_period: int = 1) -> list:
        """Aynı zaman diliminde max N coin'e izin ver (korelasyon riski azaltma).

        Lesson #21: All coins move together — 5 coins in same period = 5x risk, 1x info.
        Keep only the top N signals (by edge) per time slot.

        FIX v8: Artık sadece bu cycle'ın sinyallerini değil, AÇIK SIM TRADE'leri de
        sayıyor. Böylece farklı cycle'lardan aynı slot'a biriken trade'ler engelleniyor.
        """
        from collections import defaultdict

        # Önce açık sim trade'lerdeki slot kullanımını say
        existing_slot_count: dict[str, int] = defaultdict(int)
        for trade in self._sim_trades:
            slot = self._extract_time_slot(trade.get("question", ""))
            existing_slot_count[slot] += 1

        # Aynı şekilde canlı pozisyonlardan da say
        for _mid, pos in self.position_manager.data.get("positions", {}).items():
            slot = self._extract_time_slot(pos.get("question", ""))
            existing_slot_count[slot] += 1

        slots: dict[str, list] = defaultdict(list)
        for sig in signals:
            q = sig.market.get("question", "")
            slot = self._extract_time_slot(q)
            slots[slot].append(sig)

        filtered = []
        for slot, group in slots.items():
            # OPT-6: Loss slot cooldown — kayıp olan slot'tan hemen sonraki
            # slot'ta dead-cat-bounce riski var, o slot'u tamamen atla.
            if self._is_adjacent_to_loss_slot(slot):
                logger.info(
                    f"LOSS_COOLDOWN: {slot} | önceki slot kayıptı, "
                    f"{len(group)} sinyal DÜŞÜRÜLDİ"
                )
                continue

            # Kalan kapasite = max - zaten açık olan
            already_open = existing_slot_count.get(slot, 0)
            remaining = max(0, max_per_period - already_open)

            if remaining == 0:
                logger.info(
                    f"COIN_LIMIT: {slot} | zaten {already_open} açık trade var, "
                    f"{len(group)} yeni sinyal DÜŞÜRÜLDİ"
                )
                continue

            # Sort by edge descending, keep top remaining
            group.sort(key=lambda s: s.edge, reverse=True)
            kept = group[:remaining]
            dropped = len(group) - len(kept)
            if dropped > 0:
                logger.info(
                    f"COIN_LIMIT: {slot} | açık={already_open} + yeni={len(kept)} "
                    f"(max {max_per_period}), {dropped} düşürüldü"
                )
            filtered.extend(kept)

        return filtered

    def _is_adjacent_to_loss_slot(self, slot: str) -> bool:
        """Check if slot is the next 5-min window after any loss slot.

        E.g., loss at '9:20AM-9:25AM' → block '9:25AM-9:30AM'.
        """
        import re
        m = re.search(
            r'(\d{1,2}):(\d{2})(AM|PM)\s*[-–]\s*(\d{1,2}):(\d{2})(AM|PM)',
            slot, re.IGNORECASE,
        )
        if not m:
            return False
        # Start time of current slot in minutes
        h1, m1, ap1 = int(m.group(1)), int(m.group(2)), m.group(3).upper()
        slot_start = ((h1 % 12) + (12 if ap1 == "PM" else 0)) * 60 + m1

        for loss_slot in self._last_loss_slots:
            lm = re.search(
                r'(\d{1,2}):(\d{2})(AM|PM)\s*[-–]\s*(\d{1,2}):(\d{2})(AM|PM)',
                loss_slot, re.IGNORECASE,
            )
            if not lm:
                continue
            # End time of loss slot in minutes
            h2, m2, ap2 = int(lm.group(4)), int(lm.group(5)), lm.group(6).upper()
            loss_end = ((h2 % 12) + (12 if ap2 == "PM" else 0)) * 60 + m2
            # Adjacent = current slot starts exactly when loss slot ends
            if slot_start == loss_end:
                return True
        return False

    def _apply_consensus_filter(self, signals: list) -> list:
        """Aynı zaman dilimindeki sinyallerde çoğunluk yönüne aykırı olanları filtrele.

        Kripto marketler yüksek korelasyonlu — çoğunluk UP diyorsa NO bet riskli.
        Kural: >=3 sinyal varsa ve çoğunluk >=60% bir yöndeyse, azınlık filtrelenir.
        """
        import re
        # Zaman dilimini çıkar: "March 16, 4:05PM-4:10PM" → "4:05PM-4:10PM"
        tf_pattern = re.compile(
            r'(\d{1,2}:\d{2}\s*(?:AM|PM)\s*[-–]\s*\d{1,2}:\d{2}\s*(?:AM|PM))',
            re.IGNORECASE,
        )

        # Sinyalleri zaman dilimine göre grupla
        from collections import defaultdict
        slots: dict[str, list] = defaultdict(list)
        for sig in signals:
            q = sig.market.get("question", "")
            m = tf_pattern.search(q)
            slot = m.group(1).upper().replace(" ", "") if m else "unknown"
            slots[slot].append(sig)

        filtered = []
        for slot, group in slots.items():
            # NO sinyallerini consensus hesabından çıkar — NO filtresi zaten
            # bunları engelleyecek, ama çoğunluk hesabını zehirlemelerine izin verme.
            yes_signals = [s for s in group if s.direction == "YES"]
            no_signals = [s for s in group if s.direction == "NO"]

            # NO'ları olduğu gibi geçir (downstream NO filtresi halledecek)
            filtered.extend(no_signals)

            if len(yes_signals) < 3:
                # Az YES sinyal varsa consensus uygulanmaz
                filtered.extend(yes_signals)
                continue

            # YES sinyalleri arasında consensus: spot yönüne göre filtrele
            # Tüm YES sinyalleri aynı yönde (UP) — consensus artık sadece YES içinde
            # geçerli, NO zehirleme riski ortadan kalktı.
            filtered.extend(yes_signals)

            if no_signals:
                logger.debug(
                    f"CONSENSUS: {slot} | {len(no_signals)} NO sinyal consensus dışı bırakıldı"
                )

        return filtered

    @staticmethod
    def _parse_horizon_minutes(question: str) -> int | None:
        """Soru metninden market horizon'unu dakika olarak çıkar.

        Örnek: "Bitcoin Up or Down - March 16, 7:10PM-7:15PM ET" → 5
               "Solana Up or Down - March 16, 7:00PM-7:15PM ET" → 15
        """
        import re
        m = re.search(
            r'(\d{1,2}):(\d{2})\s*(AM|PM)\s*[-–]\s*(\d{1,2}):(\d{2})\s*(AM|PM)',
            question, re.IGNORECASE,
        )
        if not m:
            return None
        h1, m1, ap1, h2, m2, ap2 = m.groups()
        t1 = ((int(h1) % 12) + (12 if ap1.upper() == "PM" else 0)) * 60 + int(m1)
        t2 = ((int(h2) % 12) + (12 if ap2.upper() == "PM" else 0)) * 60 + int(m2)
        diff = t2 - t1
        if diff <= 0:
            diff += 24 * 60  # gece yarısı geçişi
        return diff

    @staticmethod
    def _minutes_to_market_end(question: str) -> float | None:
        """Soru metnindeki bitiş saatine (ET) kaç dakika kaldığını hesapla.

        Örnek: "Bitcoin Up or Down - March 16, 7:10PM-7:15PM ET"
               Şu an 7:05PM ET ise → 10 dakika (bitiş 7:15PM)
        """
        import re
        m = re.search(
            r'(\d{1,2}):(\d{2})\s*(AM|PM)\s*[-–]\s*(\d{1,2}):(\d{2})\s*(AM|PM)',
            question, re.IGNORECASE,
        )
        if not m:
            return None
        _, _, _, h2, m2, ap2 = m.groups()
        # Bitiş saati ET (DST-aware)
        end_hour = (int(h2) % 12) + (12 if ap2.upper() == "PM" else 0)
        end_min = int(m2)
        # Şu anki ET saati (DST-aware)
        from zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo("America/New_York"))
        now_minutes = now_et.hour * 60 + now_et.minute
        end_minutes = end_hour * 60 + end_min
        diff = end_minutes - now_minutes
        if diff < -720:  # gece yarısı geçişi
            diff += 24 * 60
        return diff

    # ── COIN → Binance symbol mapping ──
    _COIN_TO_BINANCE = {
        "bitcoin": "BTCUSDT", "btc": "BTCUSDT",
        "ethereum": "ETHUSDT", "eth": "ETHUSDT",
        "solana": "SOLUSDT", "sol": "SOLUSDT",
        "xrp": "XRPUSDT",
        "dogecoin": "DOGEUSDT", "doge": "DOGEUSDT",
        "bnb": "BNBUSDT",
        "hype": "HYPERUSDT", "hyperliquid": "HYPERUSDT",
    }

    async def _check_binance_resolutions(self):
        """Açık canlı pozisyonları Binance fiyatıyla resolve et.

        Market end_time geçtiyse → Binance kline'dan open/close karşılaştır
        → UP veya DOWN belirle → pozisyonu WIN/LOSS olarak kapat.
        Polymarket resolution beklemeye GEREK YOK.
        """
        from control_plane.entry_window_guard import parse_market_times
        from datetime import timedelta as _td
        import re
        import requests as _req

        now_utc = datetime.now(timezone.utc)
        positions = dict(self.position_manager.data.get("positions", {}))
        if not positions:
            return

        for mid, pos in positions.items():
            question = pos.get("question", "")
            order_id = pos.get("order_id", "")

            # Sim emirleri atla
            if order_id.startswith("SIM-"):
                continue

            # Market zamanlarını parse et
            start_utc, end_utc = parse_market_times(question)
            if not start_utc or not end_utc:
                continue

            # Market henüz bitmemişse atla
            # 60s buffer — Binance kline kapanması için bekle
            if now_utc < end_utc + _td(seconds=60):
                continue

            # Coin ismini question'dan çıkar
            coin_match = re.match(r'^(\w+)', question.lower())
            if not coin_match:
                continue
            coin_name = coin_match.group(1)
            binance_symbol = self._COIN_TO_BINANCE.get(coin_name)
            if not binance_symbol:
                continue

            # Binance kline çek — market window'undaki fiyatı al
            try:
                horizon_ms = int((end_utc - start_utc).total_seconds() * 1000)
                start_ms = int(start_utc.timestamp() * 1000)
                end_ms = int(end_utc.timestamp() * 1000)

                # Kline interval: market süresine göre
                horizon_min = int((end_utc - start_utc).total_seconds() / 60)
                if horizon_min <= 5:
                    interval = "5m"
                elif horizon_min <= 15:
                    interval = "15m"
                else:
                    interval = "1h"

                url = (
                    f"https://api.binance.com/api/v3/klines"
                    f"?symbol={binance_symbol}&interval={interval}"
                    f"&startTime={start_ms}&endTime={end_ms}&limit=5"
                )
                resp = _req.get(url, timeout=10)
                if resp.status_code != 200:
                    logger.warning(f"BINANCE_RESOLVE: {binance_symbol} API error {resp.status_code}")
                    continue

                klines = resp.json()
                if not klines:
                    logger.warning(f"BINANCE_RESOLVE: {binance_symbol} no klines for window")
                    continue

                # SADECE ilk kline — market penceresiyle eşleşen tek kline
                # Binance endTime inclusive → 2. kline (sonraki pencere) de dönebilir
                open_price = float(klines[0][1])
                close_price = float(klines[0][4])
                change_pct = ((close_price - open_price) / open_price) * 100

                if close_price > open_price:
                    actual_winner = "YES"  # UP
                else:
                    actual_winner = "NO"   # DOWN (eşitlik dahil)

                direction = pos.get("outcome", "")
                won = (direction == actual_winner)

                # Pozisyonu kapat
                if won:
                    # WIN: token_close_price = 1.0 (tam payout)
                    self.position_manager._close_position(mid, 1.0)
                else:
                    # LOSS: token_close_price = 0.0
                    self.position_manager._close_position(mid, 0.0)

                result = "WIN" if won else "LOSS"
                pnl = pos["amount"] / pos["entry_price"] - pos["amount"] if won else -pos["amount"]

                logger.info(
                    f"BINANCE_RESOLVE: {question[:50]} | "
                    f"{direction} → {actual_winner} (Binance {change_pct:+.3f}%) | "
                    f"{result} | ${open_price:.2f}→${close_price:.2f}"
                )

            except Exception as e:
                logger.warning(f"BINANCE_RESOLVE error ({question[:30]}): {e}")
                continue

        self.position_manager._save()

    async def _check_sim_resolutions(self):
        """Sim trade'lerin market'lerini kontrol et — resolve olduysa WIN/LOSS belirle.

        Resolution yöntemi (öncelik sırasıyla):
        1. CLOB API tokens.winner field (resmi sonuç)
        2. YES orderbook mid-price (> 0.85 = YES, < 0.15 = NO)
        3. 45dk sonra hala belirsiz → EXPIRED

        NOT: Gamma API resolution KALDIRILDI — conditionId mismatch yüzünden
        yanlış market dönüyor ve hep YES diyor (Lesson #20).
        """
        if not self._sim_trades:
            return

        still_open = []
        for trade in self._sim_trades:
            elapsed = time.time() - trade["ts"]
            if elapsed < 300:  # 5dk'dan az — henüz resolve olmamış olabilir
                still_open.append(trade)
                continue

            try:
                actual_winner = None
                yes_mid = None
                market_id = trade.get("market_id", "")

                # ── Yöntem 1: CLOB API tokens winner field (resmi) ──
                if actual_winner is None and market_id:
                    try:
                        import requests as _req
                        _clob_resp = _req.get(
                            f"https://clob.polymarket.com/markets/{market_id}",
                            timeout=10,
                        )
                        if _clob_resp.status_code == 200:
                            _clob_data = _clob_resp.json()
                            for _tok in _clob_data.get("tokens", []):
                                if _tok.get("winner") is True:
                                    _outcome = (_tok.get("outcome") or "").lower()
                                    if _outcome in ("up", "yes"):
                                        actual_winner = "YES"
                                        yes_mid = 1.0
                                    elif _outcome in ("down", "no"):
                                        actual_winner = "NO"
                                        yes_mid = 0.0
                                    logger.info(
                                        f"SIM_RESOLVE_DEBUG: {trade['question'][:45]} | "
                                        f"CLOB winner={_outcome} → {actual_winner}"
                                    )
                                    break
                    except Exception as _e:
                        logger.debug(f"CLOB resolution check failed: {_e}")

                # ── Yöntem 2: Orderbook mid-price (wider thresholds) ──
                if actual_winner is None:
                    yes_tid = trade.get("yes_token_id")
                    if yes_tid:
                        book = self.client.get_orderbook(yes_tid)
                        if book and (book["best_bid"] > 0 or book["best_ask"] > 0):
                            yes_mid = (book["best_bid"] + book["best_ask"]) / 2.0
                            logger.info(
                                f"SIM_RESOLVE_DEBUG: {trade['question'][:45]} | "
                                f"orderbook_mid={yes_mid:.4f} bid={book['best_bid']:.3f} ask={book['best_ask']:.3f} "
                                f"→ winner={'YES' if yes_mid > 0.85 else 'NO' if yes_mid < 0.15 else 'UNCLEAR'}"
                            )
                            if yes_mid > 0.85:
                                actual_winner = "YES"
                            elif yes_mid < 0.15:
                                actual_winner = "NO"

                # ── Henüz resolve olmadı — bekle veya EXPIRED ──
                if actual_winner is None:
                    if elapsed < 2700:  # 45dk'dan az bekle
                        still_open.append(trade)
                    else:
                        trade["result"] = "EXPIRED"
                        trade["final_yes_price"] = yes_mid
                        self._sim_results.append(trade)
                        logger.info(
                            f"SIM EXPIRED: {trade['question'][:45]} | "
                            f"YES={yes_mid} (45dk+ resolve olmadı)"
                        )
                    continue

                # ── WIN/LOSS belirle ──
                won = (trade["direction"] == actual_winner)
                trade["result"] = "WIN" if won else "LOSS"
                trade["final_yes_price"] = yes_mid
                trade["resolution_method"] = "clob_winner" if yes_mid in (0.0, 1.0) else "orderbook"
                self._sim_results.append(trade)

                # OPT-6: LOSS olan slot'u kaydet → sonraki slot'ta trade açma
                if not won:
                    loss_slot = self._extract_time_slot(trade.get("question", ""))
                    self._last_loss_slots.add(loss_slot)
                    logger.info(f"LOSS_SLOT_TRACK: {loss_slot} kaydedildi (bounce cooldown)")
                else:
                    # WIN gelince o slot'un cooldown'ını temizle
                    win_slot = self._extract_time_slot(trade.get("question", ""))
                    self._last_loss_slots.discard(win_slot)

                wins = sum(1 for r in self._sim_results if r.get("result") == "WIN")
                losses = sum(1 for r in self._sim_results if r.get("result") == "LOSS")
                total = wins + losses
                wr = (wins / total * 100) if total > 0 else 0

                logger.success(
                    f"SIM SONUC [{total}/{self._sim_target}]: "
                    f"{trade['direction']} {trade['question'][:45]} -> {'WIN' if won else 'LOSS'} "
                    f"(winner={actual_winner}) | WR: {wins}W/{losses}L = {wr:.0f}%"
                )

                # Hedefe ulaştık mı?
                if total >= self._sim_target:
                    logger.success(
                        f"{'='*60}\n"
                        f"SIM TEST TAMAMLANDI: {wins}W / {losses}L = {wr:.1f}% WIN RATE\n"
                        f"{'='*60}"
                    )
                    import json
                    with open("data/sim_results.json", "w") as f:
                        json.dump({
                            "total": total, "wins": wins, "losses": losses,
                            "win_rate": round(wr, 1),
                            "trades": self._sim_results,
                        }, f, indent=2, default=str)
                    logger.info("Sim sonuclari data/sim_results.json'a yazildi.")

            except Exception as e:
                logger.debug(f"Sim resolution kontrol hatasi: {e}")
                still_open.append(trade)

        self._sim_trades = still_open

    def _update_loss_streak(self):
        """Son kapanan trade'lerden ardışık kayıp sayısını güncelle."""
        closed = self.position_manager.data.get("closed", [])
        # Son 10 kapanışı ters sırada kontrol et
        streak = 0
        for trade in reversed(closed[-10:]):
            result = trade.get("result", "")
            if result == "LOSS":
                streak += 1
            elif result in ("WIN", "NEUTRAL"):
                break  # WIN veya NEUTRAL streak'i kırar
        prev_streak = self._consecutive_losses
        self._consecutive_losses = streak
        # CIRCUIT_BREAKER tamamen kaldırıldı — kullanıcı talebi
        if streak >= self._max_consecutive_losses:
            logger.info(f"CIRCUIT_BREAKER_INFO: {streak} ardışık kayıp (cooldown YOK)")

        # OPT-7: Consecutive WIN per coin — bounce guard
        # Her coin için son trade'lerden geriye doğru ardışık NO WIN say
        self._consecutive_wins_per_coin.clear()
        _coin_done: set[str] = set()  # streak kırılan coin'ler
        for trade in reversed(closed[-30:]):
            _q = trade.get("question", "") or ""
            _coin = self._shadow_detect_asset(_q)
            if not _coin or _coin in _coin_done:
                continue
            # Real closed positions carry "outcome" (YES/NO), not "direction" —
            # "direction" only exists on sim-mode trades. Reading "direction" here
            # always returned "" and this streak (and the OPT-7 bounce guard below)
            # never fired in live/paper trading.
            if trade.get("result") == "WIN" and trade.get("outcome", "").upper() == "NO":
                self._consecutive_wins_per_coin[_coin] = self._consecutive_wins_per_coin.get(_coin, 0) + 1
            else:
                _coin_done.add(_coin)  # Bu coin'in streak'i kırıldı
        _active_guards = {k: v for k, v in self._consecutive_wins_per_coin.items() if v >= 2}
        if _active_guards:
            logger.info(f"CONSEC_WIN_TRACKER: {_active_guards}")

        # OPT-6: Loss slot tracking — sadece bugünkü kayıpları say
        # Question'dan tarihi çek, bugünle karşılaştır
        self._last_loss_slots.clear()
        from zoneinfo import ZoneInfo as _ZI
        _today_et = datetime.now(_ZI("America/New_York")).strftime("%B %d").replace(" 0", " ")
        for trade in closed[-20:]:
            _q = trade.get("question", "") or trade.get("market_slug", "")
            # Sadece bugünün trade'leri (question'da "March 21" gibi tarih var)
            if _today_et not in _q:
                continue
            _slot = self._extract_time_slot(_q)
            if trade.get("result") == "LOSS" and _slot:
                self._last_loss_slots.add(_slot)
            elif trade.get("result") == "WIN" and _slot:
                self._last_loss_slots.discard(_slot)

    def _hours_to_close(self, market: dict) -> float | None:
        # Prefer endDate (full timestamp) over endDateIso (date-only)
        end_date = market.get("endDate") or market.get("end_date_iso") or market.get("endDateIso")
        if not end_date:
            return None
        try:
            s = str(end_date).replace("Z", "+00:00")
            if len(s) == 10:
                s += "T23:59:00+00:00"
            end_dt = datetime.fromisoformat(s)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            delta = end_dt - datetime.now(timezone.utc)
            hours = delta.total_seconds() / 3600
            return hours  # Negatif = expired, _pre_filter h <= 0 ile yakalar
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    # Per-market cooldown (kalıcı, dosya bazlı)
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # Control
    # ------------------------------------------------------------------ #

    def _read_control(self) -> dict:
        try:
            import json as _json
            ctrl_file = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data", "control.json",
            )
            with open(ctrl_file) as f:
                return _json.load(f)
        except Exception:
            return {}

    def _is_live_trading(self) -> bool:
        if os.getenv("LIVE_TRADING_ENABLED", "false").lower() != "true":
            return False
        if not self._readiness_clears_live():
            return False
        return self._read_control().get("live_trading", False)

    def _readiness_clears_live(self) -> bool:
        """
        Return True only if the last readiness verdict on disk is TINY_PILOT_CANDIDATE.

        Reads data/readiness_verdict.json — written by the shadow runner via
        monitoring.daily_review / shadow_runner.readiness after each journal review.

        If the file is missing or unreadable, live trading is BLOCKED (fail-safe).
        The file must be freshly written (within READINESS_MAX_AGE_HOURS hours).
        """
        import json as _json
        from datetime import timedelta

        max_age_h = float(os.getenv("READINESS_MAX_AGE_HOURS", "26"))
        verdict_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "readiness_verdict.json",
        )
        try:
            with open(verdict_file) as f:
                data = _json.load(f)
            verdict = data.get("verdict", "")
            if verdict != "TINY_PILOT_CANDIDATE":
                logger.warning(f"Readiness gate: verdict={verdict!r} — canlı işlem engellendi.")
                return False
            # Staleness check
            generated_utc_str = data.get("generated_utc", "")
            if generated_utc_str:
                from datetime import datetime, timezone
                generated = datetime.fromisoformat(generated_utc_str.replace("Z", "+00:00"))
                if generated.tzinfo is None:
                    generated = generated.replace(tzinfo=timezone.utc)
                age = datetime.now(timezone.utc) - generated
                if age > timedelta(hours=max_age_h):
                    logger.warning(
                        f"Readiness gate: verdict is {age.total_seconds()/3600:.1f}h old "
                        f"(max {max_age_h}h) — canlı işlem engellendi."
                    )
                    return False
            return True
        except FileNotFoundError:
            logger.warning("Readiness gate: data/readiness_verdict.json bulunamadı — canlı işlem engellendi.")
            return False
        except Exception as e:
            logger.error(f"Readiness gate okuma hatası: {e} — canlı işlem engellendi.")
            return False

    def _is_simulation_running(self) -> bool:
        return self._read_control().get("simulation_running", True)

    # ------------------------------------------------------------------ #
    # Balance sync
    # ------------------------------------------------------------------ #

    async def _sync_real_balance(self):
        """CLOB bakiyesini al ve capital'i güncelle.

        CLOB bakiyesi TEK GERÇEK KAYNAK (source of truth).
        position_manager'ın P&L hesabı güvenilmez olduğu kanıtlandı:
        - Spot-based resolution %99 trade'de yanlış WIN/LOSS kaydetti
        - 752 yanlış resolution → $670 tracking hatası

        Yeni mantık: capital = CLOB balance + locked (açık pozisyonlar)
        """
        try:
            balance = self.client.get_real_balance()
            if balance < 0:
                return

            # locked = tüm hâlâ AÇIK pozisyonlar (data["positions"] içindeki her
            # şey). Eskiden market end_time'ı geçmiş pozisyonlar burada hariç
            # tutuluyordu ("payout CLOB bakiyeye yansımış, double-count olur"
            # varsayımıyla) — ama update_positions() bu senkrondan HEMEN ÖNCE,
            # aynı cycle içinde çalışıp resolve edebildiği her pozisyonu zaten
            # data["positions"]'dan silip data["closed"]'a taşıyor (pnl'i de
            # capital'e ekleyerek). Bu yüzden buraya kadar hâlâ data["positions"]
            # içinde kalan bir pozisyon — end_time'ı geçmiş olsa bile — tanım
            # gereği CLOB tarafından henüz resolve EDİLMEMİŞ demektir (bkz.
            # position_manager.update_positions()'daki WAITING_RESOLUTION /
            # STALE_UNRESOLVED / 45dk timeout mantığı): USDC'si henüz gerçek
            # CLOB bakiyesine düşmemiştir, hâlâ kilitlidir. Onu locked'dan hariç
            # tutmak double-count'u önlemiyordu — capital'i (ve dolayısıyla
            # available_capital()'ı) o pozisyonun tutarı kadar sessizce
            # eksik hesaplatıyordu, resolve olana kadar her cycle'da diskteki
            # capital'e yazılıyordu (Kelly boyutlandırma, SURVIVAL-mode eşiği ve
            # günlük -%15 stop-loss'un paydası bunu okuyor).
            locked = sum(
                p.get("amount", 0)
                for p in self.position_manager.data.get("positions", {}).values()
            )
            new_capital = balance + locked
            old_capital = self.position_manager.data.get("capital", 0)

            # PositionManager.daily_loss_exceeded() (CLAUDE.md'nin -%15 günlük
            # stop-loss kuralı) gün başı sermayeyi "capital - daily.pnl" olarak
            # hesaplar. _close_position()/_close_position_neutral() bu iki
            # alanı hep birlikte değiştirir; burada da aynı değişmez korunmalı
            # — yoksa bu senkron (her döngüde çalışır) capital'i daily.pnl'e
            # dokunmadan düzeltince gün başı tahmini sessizce kayar ve gerçek
            # bir -%15 ihlali daily.pnl'in eski değerinde gizlenip stop-loss
            # hiç tetiklenmeyebilir (ya da tam tersi, sahte bir kayıp üretir).
            self.position_manager._roll_daily_if_needed()
            self.position_manager.data["daily"]["pnl"] += (new_capital - old_capital)

            # Capital'i güncelle
            self.position_manager.data["capital"] = round(new_capital, 4)
            self.position_manager._save()

            # Fark büyükse uyar
            diff = abs(new_capital - old_capital)
            if diff > 0.5:
                logger.warning(
                    f"CAPITAL_SYNC: ${old_capital:.4f} → ${new_capital:.4f} "
                    f"(CLOB=${balance:.4f} + locked=${locked:.4f})"
                )
            else:
                logger.info(f"CLOB bakiye: ${balance:.4f} | capital=${new_capital:.4f}")

        except Exception as e:
            logger.warning(f"Bakiye sync hatasi: {e}")

        # Polymarket data API pozisyon sync'i DEVRE DIŞI.
        return
        try:
            wallet = os.getenv("POLYMARKET_WALLET_ADDRESS", "")
            if not wallet:
                return
            resp = await self.client.session.get(
                "https://data-api.polymarket.com/positions",
                params={"user": wallet, "sizeThreshold": "0.001", "limit": "500"},
                timeout=15,
            )
            if resp.status_code != 200:
                return
            positions_data = resp.json()
            synced = 0
            _crypto_kw = [
                "bitcoin up or down", "ethereum up or down", "solana up or down",
                "xrp up or down", "btc up or down", "eth up or down", "sol up or down",
                "dogecoin up or down", "doge up or down", "bnb up or down", "hype up or down",
            ]
            for p in positions_data:
                condition_id = p.get("conditionId") or p.get("condition_id", "")
                if not condition_id:
                    continue
                # Zaten takip ediliyorsa atla
                if self.position_manager.has_position(condition_id):
                    continue
                # Zaten kapatılmış pozisyonu tekrar ekleme (condition_id veya proxyWallet ile eşleş)
                proxy_wallet = p.get("proxyWallet", "")
                closed_order_ids = {c.get("order_id", "") for c in self.position_manager.data.get("closed", [])}
                if proxy_wallet and proxy_wallet in closed_order_ids:
                    continue
                size = float(p.get("size", 0) or 0)
                avg_price = float(p.get("avgPrice", 0) or p.get("avg_price", 0) or 0)
                outcome = str(p.get("outcome", "YES")).upper()
                title = p.get("title") or (p.get("market", {}).get("question", "Unknown") if isinstance(p.get("market"), dict) else p.get("title", "Unknown"))
                # Sadece kısa vadeli crypto up/down marketleri senkronize et
                title_lower = str(title).lower()
                if not any(kw in title_lower for kw in _crypto_kw):
                    continue
                end_date = ""
                if isinstance(p.get("market"), dict):
                    end_date = p["market"].get("endDateIso") or p["market"].get("endDate", "")
                if size < 0.001 or avg_price <= 0:
                    continue
                amount = size * avg_price
                self.position_manager.data["positions"][condition_id] = {
                    "order_id": p.get("proxyWallet", "SYNCED"),
                    "question": str(title)[:100],
                    "outcome": outcome,
                    "amount": round(amount, 4),
                    "entry_price": round(avg_price, 4),
                    "status": "MATCHED",
                    "end_date_iso": end_date,
                }
                synced += 1
            if synced:
                self.position_manager._save()
                logger.info(f"Polymarket'tan {synced} açık pozisyon senkronize edildi.")

            # ── GHOST POZİSYON TEMİZLİĞİ ──
            # Lokalde takip edilen ama Polymarket'ta artık olmayan pozisyonları tespit et.
            # Bu pozisyonlar manuel kapatma / CLOB cancel sonucu oluşabilir.
            # Simülasyon (SIM-) emirlerini atla — onlar zaten CLOB'da yok.
            polymarket_condition_ids = set()
            for p in positions_data:
                cid = p.get("conditionId") or p.get("condition_id", "")
                if cid:
                    polymarket_condition_ids.add(cid)

            ghost_ids = []
            for mid, pos in list(self.position_manager.data.get("positions", {}).items()):
                order_id = pos.get("order_id", "")
                # Simülasyon emirlerini atla
                if order_id.startswith("SIM-"):
                    continue
                # Yeni pozisyonları atla — API gecikmesi yüzünden henüz görünmeyebilir
                created = pos.get("created_at", "")
                if created:
                    try:
                        from datetime import datetime, timezone
                        age = (datetime.now(timezone.utc) - datetime.fromisoformat(created)).total_seconds()
                        if age < 120:  # 2 dakikadan genç → ghost değil, API gecikmesi
                            continue
                    except Exception:
                        pass
                # SYNCED pozisyonlar zaten Polymarket'tan geldi — gerçek kontrol yap
                if mid not in polymarket_condition_ids:
                    ghost_ids.append(mid)

            for mid in ghost_ids:
                pos = self.position_manager.data["positions"].get(mid)
                if pos is None:
                    continue
                logger.warning(
                    f"GHOST pozisyon tespit edildi (Polymarket'ta yok): "
                    f"{pos.get('question', '')[:50]} | ${pos.get('amount', 0):.2f}"
                )
                # NEUTRAL olarak kapat — para zaten CLOB'a geri dönmüş
                self.position_manager._close_position_neutral(mid)

            if ghost_ids:
                self.position_manager._save()
                logger.warning(
                    f"{len(ghost_ids)} ghost pozisyon temizlendi "
                    f"(Polymarket'ta artık mevcut değil)"
                )
        except Exception as e:
            logger.warning(f"Pozisyon sync hatası: {e}")

    # ------------------------------------------------------------------ #
    # Background tasks
    # ------------------------------------------------------------------ #

    async def _live_data_loop(self):
        while True:
            try:
                await self._fetch_crypto_prices()
                _sw.update_indices(market_watcher._data)
                _sw.save()
            except Exception as e:
                logger.debug(f"Live data loop hatası: {e}")
            await asyncio.sleep(60)

    # ------------------------------------------------------------------ #
    # Shadow journal
    # ------------------------------------------------------------------ #

    def _get_shadow_writer(self) -> JournalWriter:
        """Return the journal writer for today, rotating if the date changed."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._shadow_writer_date != today:
            if self._shadow_writer is not None:
                try:
                    self._shadow_writer.flush()
                    self._shadow_writer.close()
                except Exception:
                    pass
            data_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data",
            )
            os.makedirs(data_dir, exist_ok=True)
            path = os.path.join(data_dir, f"shadow_journal_{today}.jsonl")
            self._shadow_writer = JournalWriter(path)
            self._shadow_writer_date = today
            logger.info(f"Shadow journal açıldı: {path}")
        return self._shadow_writer  # type: ignore[return-value]

    @staticmethod
    def _shadow_detect_asset(question: str) -> str:
        q = question.lower()
        if "bitcoin" in q or "btc" in q:   return "BTC"
        if "ethereum" in q or "eth" in q:  return "ETH"
        if "solana" in q or "sol" in q:    return "SOL"
        if "xrp" in q or "ripple" in q:    return "XRP"
        if "dogecoin" in q or "doge" in q: return "DOGE"
        if "bnb" in q:                     return "BNB"
        if "hyperliquid" in q or "hype" in q: return "HYPE"
        return "UNKNOWN"

    @staticmethod
    def _shadow_detect_horizon(question: str) -> int:
        q = question.lower()
        if "4 hour" in q or "4h" in q:    return 240
        if "1 hour" in q or "1h" in q:    return 60
        if "15 min" in q or "15m" in q:   return 15
        if "5 min" in q or " 5m" in q:    return 5
        return 15  # default

    def _record_shadow_decisions(
        self,
        candidates: list,
        signals: list,
        intended_size: float,
    ) -> None:
        """
        Write one ShadowDecisionRecord per evaluated candidate to the daily journal.

        Signals that produced a TradeSignal → EXECUTE.
        Candidates that didn't produce a signal → REJECT / NO_SIGNAL_PRODUCED.
        """
        if not candidates:
            return
        try:
            now = datetime.now(timezone.utc)
            writer = self._get_shadow_writer()
            executed_ids = {
                s.market.get("condition_id", "") for s in signals
            }

            for market in candidates:
                market_id = market.get("condition_id", "")
                question  = market.get("question", "")
                ask_yes   = float(market.get("best_ask", 0.5) or 0.5)
                bid_yes   = float(market.get("best_bid", 0) or 0)
                liquidity = float(market.get("volume", 0) or 0)

                sig_match = next(
                    (s for s in signals
                     if s.market.get("condition_id", "") == market_id),
                    None,
                )
                is_execute = sig_match is not None

                if is_execute:
                    pred_class = "UP" if sig_match.direction == "YES" else "DOWN"  # type: ignore[union-attr]
                    yes_prob   = sig_match.bayesian_prob  # type: ignore[union-attr]
                    gross_ev   = sig_match.edge           # type: ignore[union-attr]
                else:
                    pred_class = "NO_TRADE"
                    yes_prob   = ask_yes
                    gross_ev   = None

                # PART 4: Use REAL NO-side prices when available, not synthetic.
                # Clearly label source so shadow and live see the same truth.
                # Source labels must match ArbitrageEngine: REAL_BOOK / SYNTHETIC / MISSING
                real_no_ask = market.get("no_best_ask")
                real_no_bid = market.get("no_best_bid")
                _no_book_fetched = real_no_ask is not None
                if real_no_ask and float(real_no_ask) > 0:
                    _ask_no = float(real_no_ask)
                    _no_price_source = "REAL_BOOK"
                else:
                    _ask_no = round(1.0 - (bid_yes if bid_yes > 0 else ask_yes), 4)
                    _no_price_source = "SYNTHETIC" if not _no_book_fetched else "MISSING"
                if real_no_bid and float(real_no_bid) > 0:
                    _bid_no = float(real_no_bid)
                else:
                    _bid_no = round(1.0 - ask_yes, 4)

                # Get side diagnostics from arb engine if available
                _side_diag = self.arb_engine.get_last_diagnostics().get(market_id)
                _diag_dict = _side_diag.to_dict() if _side_diag else None

                # Compute entry window status for shadow audit
                _ew_result = check_entry_window(
                    question=question,
                    policy=self._entry_window_policy,
                )
                _ew_status = "PASS" if _ew_result.passed else (
                    _ew_result.rejection.value if _ew_result.rejection else "UNKNOWN"
                )

                # Bridge intent side from diagnostics
                _bridge_side = "YES"
                if _side_diag:
                    _bridge_side = _side_diag.selected_direction if _side_diag.selected_direction != "NONE" else "YES"

                signal_snap = SignalSnapshot(
                    asset=self._shadow_detect_asset(question),
                    horizon_minutes=self._shadow_detect_horizon(question),
                    signal_timestamp_utc=now,
                    predicted_class=pred_class,
                    raw_confidence=yes_prob,
                    class_probabilities=None,
                    effective_yes_prob=yes_prob,
                    effective_no_prob=round(1.0 - yes_prob, 6),
                    bridge_intent_side=_bridge_side,
                    mapping_context=(
                        f"no_src={_no_price_source} "
                        f"no_ask={_ask_no:.4f} "
                        f"ew={_ew_status} "
                        f"dir_reason={_side_diag.direction_reason if _side_diag else 'N/A'}"
                    ),
                )

                pricing_snap = PricingSnapshot(
                    market_id=market_id,
                    ask_yes=ask_yes,
                    bid_yes=bid_yes if bid_yes > 0 else round(ask_yes * 0.99, 4),
                    ask_no=_ask_no,
                    bid_no=_bid_no,
                    liquidity=liquidity,
                    pricing_timestamp_utc=now,
                    snapshot_age_seconds=0.0,
                )

                fill_fraction = None
                execution_adjusted_ev = gross_ev
                if is_execute and gross_ev is not None:
                    try:
                        # NO-direction signals must be priced on the NO side (1-yes_prob
                        # vs. _ask_no), not the YES side — passing YES prob/price for a
                        # NO trade computes the wrong market's mispricing entirely and
                        # corrupts execution_adjusted_ev (feeds mean_ev_haircut_pct →
                        # check_ev_haircut_pct → readiness verdict) for NO trades, the
                        # dominant direction in this bot per docs/architecture.md.
                        if sig_match.direction == "NO":  # type: ignore[union-attr]
                            _er_prob = round(1.0 - yes_prob, 6)
                            _er_ask = _ask_no
                        else:
                            _er_prob = yes_prob
                            _er_ask = ask_yes
                        er = compute_executable_ev(
                            side=sig_match.direction,  # type: ignore[union-attr]
                            calibrated_event_probability=_er_prob,
                            ask_price=_er_ask,
                            fee_pct=0.02,
                            intended_size_usdc=intended_size,
                            liquidity_usdc=liquidity,
                            snapshot_age_seconds=0.0,
                            horizon_minutes=self._shadow_detect_horizon(question),
                            required_threshold=0.03,
                            policy_mode="live",
                        )
                        fill_fraction = er.fill_fraction
                        execution_adjusted_ev = er.executable_ev
                    except Exception:
                        pass

                # Build rejection reason with NO-side detail
                if is_execute:
                    _rejection_reason = None
                elif _side_diag and _side_diag.direction_reason:
                    _rejection_reason = _side_diag.direction_reason
                else:
                    _rejection_reason = "NO_SIGNAL_PRODUCED"

                decision_summary = DecisionSummary(
                    decision="EXECUTE" if is_execute else "REJECT",
                    rejection_reason=_rejection_reason,
                    policy_mode="live",
                    passes_final_gate=is_execute,
                    intended_size_usdc_used=intended_size if is_execute else 0.0,
                    gross_ev=gross_ev,
                    execution_adjusted_ev=execution_adjusted_ev,
                    fill_fraction=fill_fraction,
                )

                record = ShadowDecisionRecord(
                    record_id=str(uuid.uuid4()),
                    run_id=self._shadow_run_id,
                    ts_recorded_utc=now,
                    signal=signal_snap,
                    pricing=pricing_snap,
                    policy_profile="live",
                    intended_size_usdc=intended_size,
                    evidence_source="live_shadow",
                    decision_summary=decision_summary,
                )
                writer.write(record)

            writer.flush()
            n_exec = len([s for s in signals if s.market.get("condition_id", "") in executed_ids])
            logger.debug(
                f"Shadow: {len(candidates)} karar yazıldı "
                f"({n_exec} EXECUTE / {len(candidates) - n_exec} REJECT)"
            )
        except Exception as e:
            logger.warning(f"Shadow journal yazma hatası: {e}")

    async def _fetch_crypto_prices(self):
        # CoinGecko dene, başarısız olursa Binance kullan
        symbols = {"bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL", "ripple": "XRP", "dogecoin": "DOGE"}
        try:
            resp = await self.client.session.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": ",".join(symbols), "vs_currencies": "usd", "include_24hr_change": "true"},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                crypto = {}
                for cg_id, sym in symbols.items():
                    d = data.get(cg_id, {})
                    crypto[sym] = {"price": d.get("usd", 0), "change_pct": round(d.get("usd_24h_change", 0), 2)}
                _sw.update_crypto(crypto)
                return
        except Exception:
            pass
        # Fallback: Bitstamp spot prices
        try:
            pairs = {"btcusd": "BTC", "ethusd": "ETH", "solusd": "SOL", "xrpusd": "XRP", "dogeusd": "DOGE"}
            tasks = {sym: self.client.session.get(
                f"https://www.bitstamp.net/api/v2/ticker/{pair}/", timeout=8
            ) for pair, sym in pairs.items()}
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)
            crypto = {}
            for sym, result in zip(tasks.keys(), results):
                if not isinstance(result, Exception) and result.status_code == 200:
                    crypto[sym] = {"price": float(result.json().get("last", 0)), "change_pct": 0}
            if crypto:
                _sw.update_crypto(crypto)
        except Exception as e:
            logger.debug(f"Crypto fiyat fetch hatası: {e}")
