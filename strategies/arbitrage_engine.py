"""
Arbitrage Engine: 6 model kombinasyonu ile fırsat bulur.

Veri akışı:
1. BinanceFeed  → gerçek spot fiyat, RSI, hacim, OB (5m/15m/1h/4h)
2. Bayesian     → adil olasılık tahmini (RSI + hacim + OB ile)
3. Edge         → EV_net maliyet sonrası
4. Spread       → z-score ile disloke çiftler
5. Stoikov      → optimal giriş fiyatı
6. Kelly        → pozisyon büyüklüğü
7. Monte Carlo  → strateji geçerliliği (periyodik)
"""
from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from loguru import logger

from strategies.bayesian import BayesianEstimator
from strategies.edge_model import EdgeModel
from strategies.spread_model import SpreadModel
from strategies.stoikov import StoikovExecutor
from strategies.kelly_criterion import KellyCriterion
from strategies.monte_carlo import MonteCarloSimulator, MonteCarloResult


@dataclass
class TradeSignal:
    market: dict
    direction: str        # "YES" | "NO"
    bayesian_prob: float
    market_price: float   # YES fiyatı (referans)
    edge: float
    entry_price: float    # alınan tokenin giriş fiyatı
    size: float
    z_score: float
    signal_type: str
    reasoning: str
    token_id: str = ""    # yes_token_id veya no_token_id


ASSET_SYMBOLS: dict[str, str] = {
    "bitcoin": "BTCUSDT", "btc": "BTCUSDT",
    "ethereum": "ETHUSDT", "eth": "ETHUSDT",
    "solana": "SOLUSDT", "sol": "SOLUSDT",
    "xrp": "XRPUSDT", "ripple": "XRPUSDT",
    "dogecoin": "DOGEUSDT", "doge": "DOGEUSDT",
    "bnb": "BNBUSDT",
    "hyperliquid": "HYPEUSDT", "hype": "HYPEUSDT",
}

# Polymarket market başlığından Binance zaman dilimi tespiti
_TF_PATTERN = re.compile(
    r'(\d{1,2}):(\d{2})\s*(?:am|pm)[^-]*[-–]\s*(\d{1,2}):(\d{2})\s*(?:am|pm)',
    re.IGNORECASE,
)


def _detect_asset(question: str) -> str | None:
    q = question.lower()
    for keyword, symbol in ASSET_SYMBOLS.items():
        if keyword in q:
            return symbol
    return None


def _detect_timeframe(question: str) -> str:
    """Market başlığından zaman dilimini tespit et."""
    m = _TF_PATTERN.search(question)
    if m:
        h1, mn1, h2, mn2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        mins = (h2 * 60 + mn2) - (h1 * 60 + mn1)
        if mins < 0:
            mins += 24 * 60
        if mins <= 7:
            return "5m"
        elif mins <= 20:
            return "15m"
        elif mins <= 90:
            return "1h"
        else:
            return "4h"
    # "1 hour", "4 hour" gibi açık ifadeler
    q = question.lower()
    if "4 hour" in q or "4h" in q:
        return "4h"
    if "1 hour" in q or "1h" in q:
        return "1h"
    if "15 min" in q or "15m" in q:
        return "15m"
    return "1h"  # varsayılan


class ArbitrageEngine:
    def __init__(self, http_session=None, binance_feed=None, smart_trader_tracker=None):
        import httpx
        self.session = http_session if http_session is not None else httpx.AsyncClient(timeout=8)
        self.binance_feed = binance_feed           # BinanceFeed instance (enjekte edilir)
        self.smart_trader = smart_trader_tracker   # SmartTraderTracker instance

        self.bayesian   = BayesianEstimator()
        self.edge_model = EdgeModel()
        self.spread_model = SpreadModel(window=30, min_z=1.8)
        self.stoikov    = StoikovExecutor(gamma=0.15)
        self.kelly      = KellyCriterion()
        self.mc         = MonteCarloSimulator(n_simulations=1000)

        self.min_edge      = float(os.getenv("MIN_EDGE_THRESHOLD", 0.04))
        self._mc_result: MonteCarloResult | None = None
        self._mc_last_run: float = 0.0
        self._mc_interval: float = 600.0

    # ------------------------------------------------------------------ #
    # Spot verisi (Binance ile gerçek, yoksa intramarket fallback)
    # ------------------------------------------------------------------ #

    async def refresh_spot_data(self, markets: list[dict]) -> None:
        """Binance feed varsa gerçek veriyi çek; yoksa intramarket fallback."""
        if self.binance_feed is not None:
            symbols = {_detect_asset(m.get("question", "")) for m in markets}
            symbols.discard(None)
            if symbols:
                await self.binance_feed.refresh(symbols)
        else:
            self._build_intramarket_fallback(markets)

    def _build_intramarket_fallback(self, markets: list[dict]) -> None:
        """Gerçek veri yoksa: Polymarket fiyatından sinyal türet (zayıf fallback)."""
        from collections import defaultdict
        by_asset: dict[str, list] = defaultdict(list)
        for m in markets:
            sym = _detect_asset(m.get("question", ""))
            if sym:
                by_asset[sym].append(m)
        for sym, asset_markets in by_asset.items():
            asset_markets.sort(key=lambda m: self._end_ts(m))
            leader = asset_markets[0]
            leader_price = float(leader.get("best_ask", 0.5) or 0.5)
            deviation = leader_price - 0.50
            logger.debug(f"[Fallback] {sym}: leader={leader_price:.3f} Δ{deviation:+.3f}")

    def _end_ts(self, m: dict) -> float:
        try:
            return datetime.fromisoformat(
                str(m.get("endDate", "")).replace("Z", "+00:00")
            ).timestamp()
        except Exception:
            return 9e18

    # ------------------------------------------------------------------ #
    # Monte Carlo
    # ------------------------------------------------------------------ #

    def _maybe_run_monte_carlo(self, edge: float, capital: float) -> bool:
        import time
        now = time.time()
        if self._mc_result is None or (now - self._mc_last_run) > self._mc_interval:
            self._mc_result = self.mc.simulate(
                edge=edge, capital=capital, n_trades=100,
                fill_rate=0.85,
                position_size_pct=float(os.getenv("MAX_POSITION_PCT", 0.10)),
            )
            self._mc_last_run = now
            mc = self._mc_result
            logger.info(
                f"Monte Carlo: E[r]={mc.mean_return:+.1%} | WR={mc.win_rate:.1%} | "
                f"DD={mc.max_drawdown:.1%} | Sharpe={mc.sharpe_ratio:.2f} | "
                f"Viable={'✅' if mc.viable else '❌'}"
            )
        return self._mc_result.viable if self._mc_result else True

    # ------------------------------------------------------------------ #
    # Ana analiz giriş noktası
    # ------------------------------------------------------------------ #

    async def analyze(self, markets: list[dict], capital: float) -> list[TradeSignal]:
        if not markets:
            return []

        await self.refresh_spot_data(markets)

        # Smart money pozisyonlarını güncelle (5dk cache, bloklamaz)
        if self.smart_trader is not None:
            try:
                await self.smart_trader.refresh()
            except Exception as e:
                logger.debug(f"SmartTrader refresh hatası: {e}")

        signals: list[TradeSignal] = []

        # Spread: disloke çiftleri bul
        dislocations = self.spread_model.find_dislocations(markets)
        processed_ids: set[str] = set()

        for disloc in dislocations:
            m = disloc["underpriced_market"]
            mid = m["condition_id"]
            if mid in processed_ids:
                continue
            signal = await self._evaluate_market(m, capital, disloc["z_score"], "cross_market")
            if signal:
                signals.append(signal)
                processed_ids.add(mid)

        for m in markets:
            mid = m["condition_id"]
            if mid in processed_ids:
                continue
            signal = await self._evaluate_market(m, capital, 0.0, "bayesian")
            if signal:
                signals.append(signal)
                processed_ids.add(mid)

        signals.sort(key=lambda s: s.edge, reverse=True)

        if signals:
            self._maybe_run_monte_carlo(signals[0].edge, capital)

        return signals

    async def _evaluate_market(
        self, market: dict, capital: float, z_score: float, signal_type: str
    ) -> TradeSignal | None:
        question  = market.get("question", "")
        yes_price = float(market.get("best_ask", 0) or 0)
        bid_price = float(market.get("best_bid", 0) or 0)
        no_price  = 1.0 - (bid_price if bid_price > 0 else yes_price)

        if yes_price <= 0.05 or yes_price >= 0.95:
            return None
        # Nötr bölge filtresi kaldırıldı — EdgeModel dinamik fee ile bunu zaten halleder

        # Binance sinyali al
        sym = _detect_asset(question)
        timeframe = _detect_timeframe(question)

        if sym and self.binance_feed is not None and self.binance_feed.has_data(sym):
            spot = self.binance_feed.get_signal(sym, timeframe)
            change_pct   = spot["change_pct"]
            volatility   = spot["volatility"]
            ob_imbalance = spot["ob_imbalance"]
            rsi          = spot["rsi"]
            volume_ratio = spot["volume_ratio"]
            data_source  = f"Bitstamp/{timeframe}"
        else:
            # Veri yok → nötr sinyal, Bayesian prior'ı değiştirmez
            change_pct = volatility = ob_imbalance = 0.0
            rsi = 50.0
            volume_ratio = 1.0
            data_source  = "no-data"

        is_up_contract = "up or down" in question.lower()

        if is_up_contract:
            bayes = self.bayesian.estimate(
                market_price=yes_price,
                spot_change_pct=change_pct,
                volatility=volatility,
                order_book_imbalance=ob_imbalance,
                rsi=rsi,
                volume_ratio=volume_ratio,
            )
        else:
            bayes = self.bayesian.estimate(
                market_price=yes_price,
                spot_change_pct=0.0,
                volatility=volatility,
                order_book_imbalance=ob_imbalance,
                rsi=rsi,
                volume_ratio=volume_ratio,
            )

        bayesian_prob = bayes.probability

        # Smart money boost — top trader pozisyonları varsa prob'u güçlendir
        if self.smart_trader is not None:
            sm = self.smart_trader.get_signal(market.get("condition_id", ""))
            if sm["total_traders"] > 0:
                # signal: -1.0 (hepsi short) → +1.0 (hepsi long), max ±0.05 etki
                boost = sm["signal"] * 0.05
                bayesian_prob = max(0.05, min(0.95, bayesian_prob + boost))
                logger.info(
                    f"SmartMoney [{','.join(sm['buyers'][:3]) or 'none'}] "
                    f"signal={sm['signal']:+.2f} boost={boost:+.3f} "
                    f"prob: {bayes.probability:.3f}→{bayesian_prob:.3f}"
                )

        # ── Yön kararı: YES mi NO mu? ─────────────────────────────────────────
        # Bayesian prob → YES olasılığı. Ters yönde edge varsa NO al.
        yes_edge = bayesian_prob - yes_price
        no_prob  = 1.0 - bayesian_prob
        no_price_ask = round(1.0 - yes_price, 4)   # yaklaşık NO ask
        no_edge  = no_prob - no_price_ask

        if yes_edge >= no_edge and yes_edge > 0:
            direction   = "YES"
            trade_price = yes_price
            trade_edge  = yes_edge
            token_id    = market.get("yes_token_id", "")
        elif no_edge > yes_edge and no_edge > 0:
            direction   = "NO"
            trade_price = no_price_ask
            trade_edge  = no_edge
            token_id    = market.get("no_token_id", "")
        else:
            return None

        # Cross / single market edge — en iyi olanı seç
        single_edge = self.edge_model.single_market_edge(yes_price, no_price)
        cross_edge  = self.edge_model.cross_market_edge(bayesian_prob, yes_price)

        if single_edge > cross_edge and single_edge > self.min_edge:
            edge = single_edge
            signal_type = "single_market_arb"
        else:
            edge = trade_edge

        if not self.edge_model.has_edge(edge, self.min_edge):
            return None

        # Stoikov giriş fiyatı — alınan token için
        time_remaining = self._time_remaining_fraction(market)
        if direction == "YES":
            entry_price = self.stoikov.adjusted_entry_price(
                ask_price=yes_price,
                bid_price=bid_price if bid_price > 0 else yes_price * 0.98,
                inventory=0.0,
                volatility=volatility,
                time_remaining=time_remaining,
            )
        else:
            no_bid = round(1.0 - yes_price, 4)  # approximate
            entry_price = self.stoikov.adjusted_entry_price(
                ask_price=no_price_ask,
                bid_price=no_bid * 0.98,
                inventory=0.0,
                volatility=volatility,
                time_remaining=time_remaining,
            )

        # Kelly pozisyon büyüklüğü
        size = self.kelly.position_size(
            edge=trade_edge,
            price=trade_price,
            capital=capital,
        )
        # Kelly çok küçük çıkarsa atla — orchestrator min_bet_override zaten uygular
        if size <= 0 or capital < size:
            return None

        reasoning = (
            f"[{data_source}] {direction} Bayesian={bayesian_prob:.3f} vs YES={yes_price:.3f} | "
            f"Edge={edge:.3f} | chg={change_pct:+.2f}% RSI={rsi:.0f} "
            f"vol={volume_ratio:.1f}x OB={ob_imbalance:+.2f} | Z={z_score:.1f}"
        )
        logger.info(
            f"ARB [{signal_type}/{timeframe}]: {question[:50]} | "
            f"{direction}@{trade_price:.3f} P={bayesian_prob:.3f} Edge={edge:.3f} ${size:.2f}"
        )

        return TradeSignal(
            market=market, direction=direction,
            bayesian_prob=bayesian_prob, market_price=yes_price,
            edge=edge, entry_price=entry_price, size=size,
            z_score=z_score, signal_type=signal_type, reasoning=reasoning,
            token_id=token_id,
        )

    def _time_remaining_fraction(self, market: dict) -> float:
        try:
            end_str = market.get("endDate") or market.get("end_date_iso") or market.get("endDateIso", "")
            if not end_str:
                return 0.5
            s = str(end_str).replace("Z", "+00:00")
            if len(s) == 10:
                s += "T23:59:00+00:00"
            end_dt = datetime.fromisoformat(s)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            time_left = (end_dt - now).total_seconds()
            start_str = market.get("startDate") or market.get("startDateIso", "")
            if start_str:
                s2 = str(start_str).replace("Z", "+00:00")
                if len(s2) == 10:
                    s2 += "T00:00:00+00:00"
                start_dt = datetime.fromisoformat(s2)
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=timezone.utc)
                total = (end_dt - start_dt).total_seconds()
                if total > 0:
                    return max(0.0, min(1.0, time_left / total))
            return max(0.0, min(1.0, time_left / 300.0))
        except Exception:
            return 0.5
