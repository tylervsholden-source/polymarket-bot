"""
Orchestrator: Ana döngü koordinatörü.
6-model arbitrage engine (Bayesian + Edge + Spread + Stoikov + Kelly + Monte Carlo) kullanır.
Claude AI signal kaldırıldı — Bayesian estimator spot fiyat bazlı sinyal üretir.
"""
import asyncio
import os
import uuid
from datetime import datetime, timezone
from loguru import logger

from core.dashboard import dashboard as _dash
import core.status_writer as _sw
from agents.market_index_watcher import market_watcher
from agents.binance_feed import BinanceFeed
from agents.smart_trader_tracker import SmartTraderTracker
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


class Orchestrator:
    def __init__(self, process_lock=None):
        self.interval = int(os.getenv("CYCLE_INTERVAL_SECONDS", 60))
        self.client = PolymarketClient()
        self.position_manager = PositionManager()
        self.binance_feed = BinanceFeed(session=self.client.session)
        self.smart_trader = SmartTraderTracker(session=self.client.session)
        self.arb_engine = ArbitrageEngine(
            http_session=self.client.session,
            binance_feed=self.binance_feed,
            smart_trader_tracker=self.smart_trader,
        )

        self.max_open_positions = int(os.getenv("MAX_OPEN_POSITIONS", 5))
        self.min_edge = float(os.getenv("MIN_EDGE_THRESHOLD", 0.04))
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
        self._max_orders_per_hour = int(os.getenv("MAX_ORDERS_PER_HOUR", 3))
        self._order_timestamps: list[float] = []  # son emirlerin epoch zamanları (1 saatlik pencere)

        # Pozisyon boyut limitleri
        self._min_bet = float(os.getenv("MIN_BET_SIZE", 2.0))
        self._max_bet = float(os.getenv("MAX_BET_SIZE", 5.0))
        self._max_total_exposure = float(os.getenv("MAX_TOTAL_EXPOSURE", 50.0))

        # Loss streak koruması — ardışık kayıplardan sonra durakla
        self._consecutive_losses: int = 0
        self._max_consecutive_losses: int = 3  # 3 kayıptan sonra 1 döngü dur
        self._loss_cooldown_until: float = 0.0  # epoch — bu zamana kadar trade yapma

        # Shadow journal — rotates daily, records all evaluated candidates
        self._shadow_writer: JournalWriter | None = None
        self._shadow_writer_date: str = ""
        self._shadow_run_id: str = str(uuid.uuid4())

        self._process_lock = process_lock

        # ── Sim position tracking ──
        self._sim_trades: list[dict] = []
        self._sim_results: list[dict] = []  # WIN/LOSS results
        self._sim_target: int = int(os.getenv("SIM_TRADE_TARGET", 10))
        self._sim_seen_markets: set[str] = set()  # aynı markete tekrar girme

        logger.info("Orchestrator başlatıldı — Arbitrage Engine (6 model) aktif.")

    async def run(self):
        asyncio.create_task(market_watcher.run())
        asyncio.create_task(self._live_data_loop())
        await self._sync_real_balance()
        while True:
            try:
                await self._cycle()
            except Exception as e:
                logger.error(f"Döngü hatası: {e}")
            # Her 10 döngüde bir CLOB bakiyesini senkronize et
            if self._cycle_count % 10 == 0:
                await self._sync_real_balance()
            logger.info(f"Sonraki döngü {self.interval}s sonra...")
            await asyncio.sleep(self.interval)

    async def _cycle(self):
        if not self._is_live_trading() and not self._is_simulation_running():
            logger.debug("Simülasyon ve canlı işlem kapalı. Döngü atlandı.")
            return

        self._cycle_count += 1
        # Order timestamps: 1 saatten eski kayıtları temizle
        import time as _t
        _cutoff = _t.time() - 3600
        self._order_timestamps = [t for t in self._order_timestamps if t > _cutoff]
        logger.info(f"=== Döngü #{self._cycle_count}: {datetime.now().strftime('%H:%M:%S')} ===")

        # Sim modda: önce açık sim trade'leri kontrol et
        if self._is_simulation_running() and self._sim_trades:
            await self._check_sim_resolutions()

        if self.position_manager.daily_loss_exceeded(self.daily_stop_loss) and self._is_live_trading():
            logger.warning("Günlük stop-loss tetiklendi. Bot bugün durdu.")
            _sw.update(running=True, capital=self.position_manager.available_capital(), cycle=self._cycle_count)
            _sw.save()
            return

        # ── Loss streak koruması: son kapanışlardan streak hesapla ──
        self._update_loss_streak()
        if _t.time() < self._loss_cooldown_until:
            remaining = int(self._loss_cooldown_until - _t.time())
            logger.warning(f"Loss streak cooldown aktif ({self._consecutive_losses} kayıp). {remaining}s kaldı.")
            _sw.update(running=True, capital=self.position_manager.available_capital(), cycle=self._cycle_count)
            _sw.save()
            return

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
        markets = await self.client.get_active_markets(min_volume=0)
        logger.info(f"{len(markets)} aktif market bulundu.")

        candidates = self._pre_filter(markets)
        logger.info(f"Filtre sonrası: {len(candidates)} market arbitraj analizine giriyor.")

        capital = self.position_manager.available_capital()

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

        # Arbitrage Engine — 6 model çalıştır
        signals = await self.arb_engine.analyze(candidates, capital)
        logger.info(f"{len(signals)} arbitraj sinyali üretildi.")

        # ── Crypto Consensus Filter ──────────────────────────────────────────
        # Aynı zaman diliminde çoğunluk yönüne aykırı sinyalleri filtrele.
        # Kripto marketler yüksek korelasyonlu — çoğunluk UP ise NO bet riskli.
        signals = self._apply_consensus_filter(signals)

        # Shadow journal — tüm adayları kaydet (EXECUTE + REJECT)
        ctrl = self._read_control()
        intended_size = float(ctrl.get("min_bet", 20.0))
        self._record_shadow_decisions(candidates, signals, intended_size)

        # Dashboard'dan min_bet oku — kullanıcının seçtiği miktar (1/5/10/20$)
        ctrl = self._read_control()
        min_bet_override = float(ctrl.get("min_bet", 1.0))

        for signal in signals:
            if open_count >= self.max_open_positions:
                break

            market = signal.market
            market_id = market["condition_id"]

            if self.position_manager.has_position(market_id):
                continue
            if self._reentry_guard.is_blocked(market_id):
                continue

            # ── NO side filtresi: regime bazlı ──────────────────────────────
            # NO (DOWN) trade sadece BTC+ETH BEARISH rejimde açılır.
            # BULLISH/NEUTRAL'da NO trade = kayıp (tarihsel: 0W/18L)
            if signal.direction == "NO":
                regime = self.arb_engine._current_regime
                if regime.get("regime") != "BEARISH":
                    logger.info(
                        f"NO BLOCKED (regime={regime.get('regime')}): "
                        f"{market['question'][:50]}"
                    )
                    continue
                logger.info(
                    f"NO ALLOWED (BEARISH str={regime.get('strength', 0):.2f}): "
                    f"{market['question'][:50]}"
                )

            # ── Bayesian güven filtresi: model emin değilse trade etme ──
            effective_prob = signal.bayesian_prob if signal.direction == "YES" else (1.0 - signal.bayesian_prob)
            if effective_prob < 0.51:
                logger.debug(f"Bayesian belirsiz (eff={effective_prob:.3f}, dir={signal.direction}), atlanıyor: {market['question'][:50]}")
                continue

            # BTC min edge kaldırıldı — 0.25 erişilemez eşikti, normal min_edge yeterli

            # Bet size: Kelly önerisini max_bet ile kısıtla, ama min_bet'in altındaysa trade yapma
            bet_size = min(self._max_bet, signal.size)
            if bet_size < self._min_bet:
                logger.debug(
                    f"Kelly önerisi (${signal.size:.2f}) min_bet'in (${self._min_bet:.2f}) "
                    f"altında, atlanıyor: {market['question'][:50]}"
                )
                continue

            logger.info(
                f"SİNYAL [{signal.signal_type}]: {market['question'][:55]} | "
                f"Bayesian={signal.bayesian_prob:.3f} | Fiyat={signal.market_price:.3f} | "
                f"Edge={signal.edge:.3f} | Z={signal.z_score:.1f} | ${bet_size:.2f}"
            )

            _sw.add_decision(
                market=market["question"],
                category="CRYPTO",
                prob=signal.bayesian_prob,
                price=signal.market_price,
                edge=signal.edge,
                confidence="HIGH" if signal.edge > 0.06 else "MEDIUM",
                action="ORDER" if self._is_live_trading() else "SIM_BUY",
                reasoning=signal.reasoning,
                size=bet_size,
            )

            if self._is_live_trading():
                # direction'a göre doğru token seç
                token_id = signal.token_id or (
                    market.get("yes_token_id") if signal.direction == "YES"
                    else market.get("no_token_id")
                )

                # Toplam exposure kontrolü
                existing_exposure = sum(
                    p.get("amount", 0)
                    for p in self.position_manager.data.get("positions", {}).values()
                )
                if existing_exposure + bet_size > self._max_total_exposure:
                    logger.info(
                        f"Exposure limiti: ${existing_exposure:.0f}+${bet_size:.0f} > "
                        f"${self._max_total_exposure:.0f}, atlanıyor."
                    )
                    continue

                # ── 11-NOKTA LİVE GATE KONTROLÜ ──
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

                # ── DOĞRUDAN EMİR VER (onay kuyruğu bypass) ──
                order = await self.client.place_order(
                    market_id=market_id,
                    outcome=signal.direction,
                    amount=bet_size,
                    price=signal.entry_price,
                    token_id=token_id,
                )
                if order:
                    import time as _time
                    self._order_timestamps.append(_time.time())
                    order["outcome"] = signal.direction
                    self.position_manager.add_position(market_id, order, market["question"])
                    self._reentry_guard.mark_traded(market_id)
                    open_count += 1
                    capital -= bet_size
                    logger.success(
                        f"EMİR VERİLDİ: {market['question'][:50]} | "
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
                if market_id not in self._sim_seen_markets and len(self._sim_trades) < self._sim_target:
                    import time as _st
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
                        "ts": _st.time(),
                    }
                    self._sim_trades.append(sim_entry)
                    logger.success(
                        f"[SIM #{len(self._sim_trades)}/{self._sim_target}] "
                        f"{signal.direction} {market['question'][:50]} | "
                        f"Edge={signal.edge:.3f} Bayesian={signal.bayesian_prob:.3f} ${signal.size:.2f}"
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

        await self._finalize_cycle(markets, candidates)

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
            )
            if order:
                import time as _time
                self._order_timestamps.append(_time.time())
                order["outcome"] = direction
                self.position_manager.add_position(market_id, order, question)
                self._reentry_guard.mark_traded(market_id)
                open_count += 1  # Sonraki emirler için güncelle
                capital -= amount
                logger.success(
                    f"ONAYLANMIŞ EMİR VERİLDİ: {question[:50]} | "
                    f"${amount:.2f} @ {price:.4f}"
                )
            else:
                logger.error(f"Onaylı emir başarısız: {question[:50]}")
                _block_order_execution(order_req["id"], reason="CLOB_REJECTED")
                continue

            _mark_order_executed(order_req["id"])

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
        )
        _sw.save()

    # ------------------------------------------------------------------ #
    # Filtre: sadece BTC/ETH/SOL/XRP up-or-down, 5dk ve 15dk marketler
    # ------------------------------------------------------------------ #

    _CRYPTO_UPDOWN_KEYWORDS = [
        "bitcoin up or down", "ethereum up or down", "solana up or down",
        "btc up or down", "eth up or down", "sol up or down", "xrp up or down",
        "dogecoin up or down", "doge up or down", "bnb up or down",
        "hyperliquid up or down", "hype up or down",
    ]

    def _pre_filter(self, markets: list) -> list:
        from zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo("America/New_York"))
        today_str = now_et.strftime("%B %d").replace(" 0", " ")  # "March 15" ET format
        result = []
        for m in markets:
            question = m.get("question", "")
            q_lower = question.lower()
            if not any(kw in q_lower for kw in self._CRYPTO_UPDOWN_KEYWORDS):
                continue
            # Sadece bugünün marketleri (March 16 gibi)
            if today_str.lower() not in q_lower:
                continue
            # Sadece 5dk ve 15dk marketleri kabul et (zaman aralığından hesapla)
            horizon = self._parse_horizon_minutes(question)
            if horizon not in (5,):
                continue
            # Kapanışa max 15dk kalan marketler (30dk+ uzaktakilere dokunma)
            mins_left = self._minutes_to_market_end(question)
            if mins_left is None or mins_left <= 0 or mins_left > 15:
                continue
            h = self._hours_to_close(m)
            if h is None or h <= 0 or h > self.max_hours or h < self.min_hours:
                continue
            # Fiyat filtresi: 0.05 - 0.95 arası
            price = float(m.get("best_ask", 0) or 0)
            if not (0.05 <= price <= 0.95):
                continue
            result.append(m)
        return result

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

    async def _check_sim_resolutions(self):
        """Sim trade'lerin market'lerini kontrol et — resolve olduysa WIN/LOSS belirle."""
        import time as _t
        if not self._sim_trades:
            return

        still_open = []
        for trade in self._sim_trades:
            # 5dk+ geçmişse marketi kontrol et
            elapsed = _t.time() - trade["ts"]
            if elapsed < 300:  # 5 dakikadan az — henüz resolve olmamış olabilir
                still_open.append(trade)
                continue

            # YES token fiyatını kontrol et — resolve olduysa 0.95+ veya 0.05-
            yes_tid = trade.get("yes_token_id")
            if not yes_tid:
                still_open.append(trade)
                continue

            try:
                book = self.client.get_orderbook(yes_tid)
                if not book:
                    if elapsed < 1200:  # 20dk'dan az bekle
                        still_open.append(trade)
                    else:  # çok eski, at
                        trade["result"] = "EXPIRED"
                        self._sim_results.append(trade)
                    continue

                yes_mid = (book["best_bid"] + book["best_ask"]) / 2.0

                # Market resolve oldu mu? (YES > 0.90 = UP kazandı, YES < 0.10 = DOWN kazandı)
                if yes_mid > 0.90:
                    actual_winner = "YES"  # UP kazandı
                elif yes_mid < 0.10:
                    actual_winner = "NO"   # DOWN kazandı
                elif elapsed > 1800:  # 30dk geçti — EXPIRED (coin-flip yapmıyoruz)
                    trade["result"] = "EXPIRED"
                    trade["final_yes_price"] = yes_mid
                    self._sim_results.append(trade)
                    logger.info(
                        f"SIM EXPIRED: {trade['question'][:45]} | YES={yes_mid:.2f} (30dk+ resolve olmadı)"
                    )
                    continue
                else:
                    still_open.append(trade)
                    continue

                won = (trade["direction"] == actual_winner)
                trade["result"] = "WIN" if won else "LOSS"
                trade["final_yes_price"] = yes_mid
                self._sim_results.append(trade)

                wins = sum(1 for r in self._sim_results if r.get("result") == "WIN")
                losses = sum(1 for r in self._sim_results if r.get("result") == "LOSS")
                total = wins + losses
                wr = (wins / total * 100) if total > 0 else 0

                emoji = "✅" if won else "❌"
                logger.success(
                    f"{emoji} SIM SONUÇ [{total}/{self._sim_target}]: "
                    f"{trade['direction']} {trade['question'][:45]} → {trade['result']} "
                    f"(YES={yes_mid:.2f}) | WR: {wins}W/{losses}L = {wr:.0f}%"
                )

                # Hedefe ulaştık mı?
                if total >= self._sim_target:
                    logger.success(
                        f"{'='*60}\n"
                        f"SIM TEST TAMAMLANDI: {wins}W / {losses}L = {wr:.1f}% WIN RATE\n"
                        f"{'='*60}"
                    )
                    # Sonuçları dosyaya yaz
                    import json
                    with open("data/sim_results.json", "w") as f:
                        json.dump({
                            "total": total, "wins": wins, "losses": losses,
                            "win_rate": round(wr, 1),
                            "trades": self._sim_results,
                        }, f, indent=2, default=str)
                    logger.info("Sim sonuçları data/sim_results.json'a yazıldı.")

            except Exception as e:
                logger.debug(f"Sim resolution kontrol hatası: {e}")
                still_open.append(trade)

        self._sim_trades = still_open

    def _update_loss_streak(self):
        """Son kapanan trade'lerden ardışık kayıp sayısını güncelle."""
        import time as _t
        closed = self.position_manager.data.get("closed", [])
        # Son 10 kapanışı ters sırada kontrol et (NEUTRAL atla)
        streak = 0
        for trade in reversed(closed[-10:]):
            result = trade.get("result", "")
            if result == "LOSS":
                streak += 1
            elif result == "WIN":
                break
        prev_streak = self._consecutive_losses
        self._consecutive_losses = streak
        # Yeni streak tetiklendiğinde cooldown başlat (tekrar tetikleme için streak artmalı)
        if streak >= self._max_consecutive_losses and streak > prev_streak:
            self._loss_cooldown_until = _t.time() + 180
            logger.warning(
                f"Loss streak koruması: {streak} ardışık kayıp → 180s cooldown başladı"
            )

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
        """Polymarket bakiyesi + açık pozisyonları senkronize et.

        CLOB balance = serbest USDC (açık emirler zaten düşülmüş).
        capital = CLOB balance + açık pozisyonlarda kilitli miktar.
        Bu sayede available_capital = capital - locked = CLOB balance (doğru).
        """
        try:
            balance = self.client.get_real_balance()
            if balance >= 0:
                locked = sum(
                    p.get("amount", 0)
                    for p in self.position_manager.data.get("positions", {}).values()
                )
                new_capital = balance + locked
                prev = self.position_manager.data.get("capital", 0)
                self.position_manager.data["capital"] = new_capital
                self.position_manager._save()
                if abs(prev - new_capital) > 0.01:
                    logger.warning(
                        f"Sermaye guncellendi: ${prev:.4f} → ${new_capital:.4f} "
                        f"(CLOB=${balance:.4f} + locked=${locked:.4f})"
                    )
                else:
                    logger.info(f"Polymarket bakiyesi senkronize: ${balance:.4f} (capital=${new_capital:.4f})")
        except Exception as e:
            logger.warning(f"Bakiye sync hatasi: {e}")

        # Polymarket'taki açık pozisyonları çek ve bot'a ekle
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
                        er = compute_executable_ev(
                            side=sig_match.direction,  # type: ignore[union-attr]
                            calibrated_event_probability=yes_prob,
                            ask_price=ask_yes,
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
