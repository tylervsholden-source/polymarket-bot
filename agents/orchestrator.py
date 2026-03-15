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


class Orchestrator:
    def __init__(self):
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

        self._cycle_count: int = 0

        # Shadow journal — rotates daily, records all evaluated candidates
        self._shadow_writer: JournalWriter | None = None
        self._shadow_writer_date: str = ""
        self._shadow_run_id: str = str(uuid.uuid4())

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
            logger.info(f"Sonraki döngü {self.interval}s sonra...")
            await asyncio.sleep(self.interval)

    async def _cycle(self):
        if not self._is_live_trading() and not self._is_simulation_running():
            logger.debug("Simülasyon ve canlı işlem kapalı. Döngü atlandı.")
            return

        self._cycle_count += 1
        logger.info(f"=== Döngü #{self._cycle_count}: {datetime.now().strftime('%H:%M:%S')} ===")

        if self.position_manager.daily_loss_exceeded(self.daily_stop_loss):
            logger.warning("Günlük stop-loss tetiklendi. Bot bugün durdu.")
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

        # Arbitrage Engine — 6 model çalıştır
        signals = await self.arb_engine.analyze(candidates, capital)
        logger.info(f"{len(signals)} arbitraj sinyali üretildi.")

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

            # Kullanıcının seçtiği min_bet'i kullan (Kelly hesabını override eder)
            bet_size = max(min_bet_override, signal.size)

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
                order = await self.client.place_order(
                    market_id=market_id,
                    outcome=signal.direction,
                    amount=bet_size,
                    price=signal.entry_price,
                    token_id=token_id,
                )
                if order:
                    order["outcome"] = signal.direction
                    self.position_manager.add_position(market_id, order, market["question"])
                    open_count += 1
                    logger.success(
                        f"EMİR VERİLDİ: {market['question'][:50]} | "
                        f"${signal.size:.2f} @ {signal.entry_price:.4f}"
                    )
                    _sw.add_order(
                        market=market["question"],
                        outcome=signal.direction,
                        amount=signal.size,
                        price=signal.entry_price,
                        order_id=order.get("order_id", ""),
                        status=order.get("status", "PLACED"),
                        edge=signal.edge,
                    )
                    _sw.save()
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


        await self._finalize_cycle(markets, candidates)

    async def _finalize_cycle(self, markets: list, candidates: list):
        """Pozisyonları güncelle, dashboard yaz."""
        await self.position_manager.update_positions(self.client)
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
    # Filtre: sadece BTC/ETH/SOL/XRP up-or-down, max 1 saat
    # ------------------------------------------------------------------ #

    _CRYPTO_UPDOWN_KEYWORDS = [
        "bitcoin up or down", "ethereum up or down", "solana up or down",
        "btc up or down", "eth up or down", "sol up or down", "xrp up or down",
        "dogecoin up or down", "doge up or down", "bnb up or down",
        "hyperliquid up or down", "hype up or down",
    ]

    def _pre_filter(self, markets: list) -> list:
        result = []
        for m in markets:
            question = m.get("question", "").lower()
            if not any(kw in question for kw in self._CRYPTO_UPDOWN_KEYWORDS):
                continue
            h = self._hours_to_close(m)
            if h is None or h > self.max_hours or h < self.min_hours:
                continue
            # Fiyat filtresi: 0.05 - 0.95 arası
            price = float(m.get("best_ask", 0) or 0)
            if not (0.05 <= price <= 0.95):
                continue
            result.append(m)
        return result

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
            return hours if hours > 0 else None  # None = already ended
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    # Control
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # Mean Reversion Runner
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
        """Polymarket bakiyesi + açık pozisyonları senkronize et."""
        try:
            balance = self.client.get_real_balance()
            if balance > 0:
                prev = self.position_manager.data.get("capital", 0)
                self.position_manager.data["capital"] = balance
                self.position_manager._save()   # positions.json'a yaz
                if abs(prev - balance) > 0.01:
                    logger.warning(
                        f"Sermaye guncellendi: positions.json=${prev:.4f} → "
                        f"CLOB=${balance:.4f} (fark=${balance - prev:+.4f})"
                    )
                else:
                    logger.info(f"Polymarket bakiyesi senkronize edildi: ${balance:.4f}")
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

                signal_snap = SignalSnapshot(
                    asset=self._shadow_detect_asset(question),
                    horizon_minutes=self._shadow_detect_horizon(question),
                    signal_timestamp_utc=now,
                    predicted_class=pred_class,
                    raw_confidence=yes_prob,
                    class_probabilities=None,
                    effective_yes_prob=yes_prob,
                    effective_no_prob=round(1.0 - yes_prob, 6),
                )

                pricing_snap = PricingSnapshot(
                    market_id=market_id,
                    ask_yes=ask_yes,
                    bid_yes=bid_yes if bid_yes > 0 else round(ask_yes * 0.99, 4),
                    ask_no=round(1.0 - (bid_yes if bid_yes > 0 else ask_yes), 4),
                    bid_no=round(1.0 - ask_yes, 4),
                    liquidity=liquidity,
                    pricing_timestamp_utc=now,
                    snapshot_age_seconds=0.0,
                )

                decision_summary = DecisionSummary(
                    decision="EXECUTE" if is_execute else "REJECT",
                    rejection_reason=None if is_execute else "NO_SIGNAL_PRODUCED",
                    policy_mode="live",
                    passes_final_gate=is_execute,
                    intended_size_usdc_used=intended_size if is_execute else 0.0,
                    gross_ev=gross_ev,
                    execution_adjusted_ev=gross_ev,
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
