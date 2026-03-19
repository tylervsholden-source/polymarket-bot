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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from loguru import logger

from strategies.bayesian import BayesianEstimator
from strategies.edge_model import EdgeModel
from strategies.spread_model import SpreadModel
from strategies.stoikov import StoikovExecutor
from strategies.kelly_criterion import KellyCriterion
from strategies.monte_carlo import MonteCarloSimulator, MonteCarloResult
from strategies.ml_classifier import TradeClassifier
from strategies.orderbook_analyzer import OrderbookAnalyzer
from strategies.sum_monitor import SumMonitor
from core.candlestick_analyzer import CandlestickAnalyzer as CA


# ── NO-side rejection reasons (PART 1A/1D) ──────────────────────────────────
class NoSideStatus(str, Enum):
    """Explicit classification of why NO side was or was not selected."""
    YES_EDGE_DOMINATES       = "YES_EDGE_DOMINATES"
    NO_EDGE_DOMINATES        = "NO_EDGE_DOMINATES"
    NO_REAL_BOOK_MISSING     = "NO_REAL_BOOK_MISSING"
    NO_SIDE_UNTRADABLE       = "NO_SIDE_UNTRADABLE"       # real book exists but ask >= 0.95
    NO_SIDE_BOOK_SUSPICIOUS  = "NO_SIDE_BOOK_SUSPICIOUS"  # real book exists but ask >= 0.90
    BOTH_EDGES_NEGATIVE      = "BOTH_EDGES_NEGATIVE"
    NO_EDGE_NEGATIVE         = "NO_EDGE_NEGATIVE"         # yes_edge > 0 but no_edge <= 0


class NoPriceSource(str, Enum):
    """How the NO-side ask price was determined."""
    REAL_BOOK      = "REAL_BOOK"       # from actual NO token orderbook
    SYNTHETIC      = "SYNTHETIC"       # derived as 1 - yes_price
    MISSING        = "MISSING"         # no data available


@dataclass
class SideDiagnostics:
    """Full diagnostics for every YES-vs-NO direction decision."""
    # Identity
    market_id: str = ""
    market_title: str = ""
    asset: str = ""
    horizon: str = ""
    timestamp_utc: str = ""

    # YES side
    yes_best_bid: float = 0.0
    yes_best_ask: float = 0.0
    yes_edge: float = 0.0
    bayesian_prob: float = 0.0

    # NO side
    no_best_bid: float = 0.0
    no_best_ask: float = 0.0
    no_edge: float = 0.0
    no_prob: float = 0.0
    no_price_source: str = "MISSING"     # NoPriceSource value
    no_token_id_present: bool = False
    no_book_fetched: bool = False

    # Decision
    selected_direction: str = "NONE"     # YES / NO / NONE
    direction_reason: str = ""           # NoSideStatus value

    def to_dict(self) -> dict:
        return {
            "market_id": self.market_id,
            "market_title": self.market_title[:60],
            "asset": self.asset,
            "horizon": self.horizon,
            "timestamp_utc": self.timestamp_utc,
            "yes_best_bid": self.yes_best_bid,
            "yes_best_ask": self.yes_best_ask,
            "yes_edge": round(self.yes_edge, 6),
            "bayesian_prob": round(self.bayesian_prob, 6),
            "no_best_bid": round(self.no_best_bid, 4),
            "no_best_ask": round(self.no_best_ask, 4),
            "no_edge": round(self.no_edge, 6),
            "no_prob": round(self.no_prob, 6),
            "no_price_source": self.no_price_source,
            "no_token_id_present": self.no_token_id_present,
            "no_book_fetched": self.no_book_fetched,
            "selected_direction": self.selected_direction,
            "direction_reason": self.direction_reason,
        }


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
    side_diagnostics: SideDiagnostics | None = None


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
    def __init__(self, http_session=None, binance_feed=None, smart_trader_tracker=None,
                 top_trader=None, kalshi_arb=None, clob_client=None):
        import httpx
        self.session = http_session if http_session is not None else httpx.AsyncClient(timeout=8)
        self.binance_feed = binance_feed           # BinanceFeed instance (enjekte edilir)
        self.smart_trader = smart_trader_tracker   # SmartTraderTracker instance
        self.top_trader = top_trader               # TopTraderTracker instance
        self.kalshi_arb = kalshi_arb               # KalshiArbTracker instance
        self._clob_client = clob_client            # py-clob-client (for L2 orderbook)

        self.bayesian   = BayesianEstimator()
        self.edge_model = EdgeModel()
        self.spread_model = SpreadModel(window=30, min_z=1.8)
        self.stoikov    = StoikovExecutor(gamma=0.15)
        self.kelly      = KellyCriterion()
        self.mc         = MonteCarloSimulator(n_simulations=1000)
        self.ml         = TradeClassifier()    # ML trade quality predictor
        self.ob_analyzer = OrderbookAnalyzer()  # L2 orderbook depth
        self.sum_monitor = SumMonitor()         # YES+NO sum arbitrage

        # Asymmetric edge thresholds (pattern analysis: NO=61% WR, YES=31% WR)
        # NO trades at edge 0.03-0.05 had 66.7% WR — lower threshold for NO
        # YES trades unreliable below edge 0.10
        _env_edge = float(os.getenv("MIN_EDGE_THRESHOLD", 0.08))
        self.min_edge_yes  = max(0.05, _env_edge * 0.5)  # YES: need real edge after 0.01 cost
        self.min_edge_no   = max(0.08, _env_edge * 0.8)  # NO: strict — historical 18% WR
        self.min_edge      = max(0.08, _env_edge)    # legacy fallback
        # Coin blacklist — empty (BNB/HYPE re-enabled: 100% WR in live trading)
        self._coin_blacklist = set(os.getenv("COIN_BLACKLIST", "").split(",")) - {""}
        self._current_regime: dict = {"regime": "NEUTRAL", "strength": 0.0}
        # ── Regime decay guard (v8) ──────────────────────────────────────────
        # Bounce en çok regime strength HIZLA düştüğünde geliyor.
        # Örn: str 0.85 → 0.50 = -0.35 (2-3 cycle) → bounce başlıyor.
        # Bu düşüşü tespit edip NO trade'leri 1 cycle duraklatıyoruz.
        self._regime_strength_peak: float = 0.0  # son peak strength
        self._regime_decay_pause: bool = False    # True ise NO trade'ler durur
        self._mc_result: MonteCarloResult | None = None
        self._mc_last_run: float = 0.0
        self._mc_interval: float = 600.0
        # NO-side forensic diagnostics — keyed by condition_id
        self._last_diagnostics: dict[str, SideDiagnostics] = {}

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

    def get_last_diagnostics(self) -> dict[str, SideDiagnostics]:
        """Return side diagnostics from the last analyze() call."""
        return dict(self._last_diagnostics)

    async def analyze(self, markets: list[dict], capital: float) -> list[TradeSignal]:
        if not markets:
            return []

        self._last_diagnostics.clear()
        await self.refresh_spot_data(markets)

        # BTC+ETH 4h regime: tüm sinyallere leader bias olarak iletilir
        self._current_regime = {"regime": "NEUTRAL", "strength": 0.0}
        if self.binance_feed is not None:
            self._current_regime = self.binance_feed.get_market_regime()

        # ── Regime decay detection (v8) ──────────────────────────────────────
        # Peak strength'i takip et. Hızlı düşüş = bounce habercisi.
        current_str = self._current_regime.get("strength", 0.0)
        if current_str > self._regime_strength_peak:
            self._regime_strength_peak = current_str
            self._regime_decay_pause = False  # peak yenileniyor, pause kaldır

        decay = self._regime_strength_peak - current_str

        # RESET: strength çok düştüyse eski peak artık geçersiz
        # Aksi halde peak=0.48, str=0.01 → sonsuza kadar decay pause
        if current_str < 0.15:
            if self._regime_decay_pause:
                logger.info(
                    f"REGIME_DECAY_RESET: str={current_str:.2f}<0.15, "
                    f"peak {self._regime_strength_peak:.2f} sıfırlandı"
                )
            self._regime_strength_peak = current_str
            self._regime_decay_pause = False
        elif decay >= 0.20 and self._regime_strength_peak >= 0.40:
            # Güçlü düşüşten sonra hızlı zayıflama → bounce riski
            if not self._regime_decay_pause:
                logger.warning(
                    f"REGIME_DECAY: peak={self._regime_strength_peak:.2f} → "
                    f"now={current_str:.2f} (decay={decay:.2f}) → NO trade'ler 1 cycle DURDU"
                )
            self._regime_decay_pause = True
        elif decay < 0.10:
            # Decay düşük veya strength tekrar yükseliyor → pause kaldır
            if self._regime_decay_pause:
                logger.info(
                    f"REGIME_DECAY_CLEAR: decay={decay:.2f} < 0.10, NO trade'ler tekrar aktif"
                )
            self._regime_decay_pause = False
            if current_str < 0.20:
                self._regime_strength_peak = current_str

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

        # Binance sinyali al
        sym = _detect_asset(question)
        timeframe = _detect_timeframe(question)

        # ── COIN BLACKLIST ──────────────────────────────────────────────────
        # Pattern analysis: HYPE=16.7% WR, BNB=0% WR — toxic, para yakar
        if sym and any(bl in sym for bl in self._coin_blacklist):
            logger.debug(
                "BLACKLIST: %s coin=%s (toxic WR)" % (question[:40], sym)
            )
            return None

        if sym and self.binance_feed is not None and self.binance_feed.has_data(sym):
            spot = self.binance_feed.get_signal(sym, timeframe)
            change_pct   = spot["change_pct"]
            volatility   = spot["volatility"]
            ob_imbalance = spot["ob_imbalance"]
            rsi          = spot["rsi"]
            volume_ratio = spot["volume_ratio"]
            macd_hist    = spot["macd_hist"]
            patterns     = spot.get("patterns", [])
            momentum_decelerating = spot.get("momentum_decelerating", False)
            consecutive_bearish = spot.get("consecutive_bearish", 0)
            consecutive_bullish = spot.get("consecutive_bullish", 0)
            bounce_signal = spot.get("bounce_signal", False)
            pre_bounce_streak = spot.get("pre_bounce_streak", 0)
            # New technical indicators
            bb_pos       = spot["bb_pos"]
            bb_width     = spot["bb_width"]
            bb_squeeze   = spot.get("bb_squeeze", False)
            bb_breakout  = spot.get("bb_breakout", 0.0)
            ema_cross    = spot["ema_cross"]
            atr_pct      = spot["atr_pct"]
            stoch_k      = spot["stoch_k"]
            sr_position  = spot["sr_position"]
            vwap_dev     = spot["vwap_dev"]
            ichi_signal  = spot["ichi_signal"]
            ichi_tk_cross = spot["ichi_tk_cross"]
            fib_level    = spot["fib_level"]
            # ta library indicators
            adx          = spot.get("adx", 25.0)
            adx_plus     = spot.get("adx_plus", 0.0)
            adx_minus    = spot.get("adx_minus", 0.0)
            obv_slope    = spot.get("obv_slope", 0.0)
            cmf          = spot.get("cmf", 0.0)
            tech_score   = spot.get("tech_score", 0.0)
            data_source  = f"Bitstamp/{timeframe}"
        else:
            change_pct = volatility = ob_imbalance = 0.0
            rsi = 50.0
            volume_ratio = 0.0
            macd_hist = 0.0
            patterns = []
            bb_pos = 0.5
            bb_width = 0.0
            bb_squeeze = False
            bb_breakout = 0.0
            ema_cross = 0.0
            atr_pct = 0.0
            stoch_k = 50.0
            sr_position = 0.5
            vwap_dev = 0.0
            ichi_signal = 0.0
            ichi_tk_cross = 0.0
            fib_level = 0.5
            momentum_decelerating = False
            consecutive_bearish = 0
            consecutive_bullish = 0
            bounce_signal = False
            pre_bounce_streak = 0
            adx = 25.0
            adx_plus = 0.0
            adx_minus = 0.0
            obv_slope = 0.0
            cmf = 0.0
            tech_score = 0.0
            data_source  = "no-data"

        is_up_contract = "up or down" in question.lower()

        # ── SPOT MOMENTUM GATE ───────────────────────────────────────────────
        # 5dk crypto'da tek gerçek edge = güçlü spot hareket sonrası lag.
        # |change_pct| < 0.15% → yatay piyasa → trade etme (gürültü).
        # SPOT_FLAT devre dışı — edge varsa trade et, flat/non-flat farketmez
        # Eski threshold (0.05%) tüm marketleri blokluyordu flat piyasada

        # Leader bias: BTC+ETH 4h trend combined score
        regime = self._current_regime
        leader_bias = (
            regime.get("btc_4h_pct", 0.0) * 0.6 +
            regime.get("eth_4h_pct", 0.0) * 0.4
        ) * 0.6 + (
            regime.get("btc_5m_pct", 0.0) * 0.6 +
            regime.get("eth_5m_pct", 0.0) * 0.4
        ) * 0.4

        # Bayesian'a indikatörleri ilet
        ta_kwargs = dict(
            market_price=yes_price,
            volatility=volatility,
            order_book_imbalance=ob_imbalance,
            rsi=rsi,
            volume_ratio=volume_ratio,
            macd_hist=macd_hist,
            leader_bias=leader_bias,
            bb_pos=bb_pos,
            bb_width=bb_width,
            bb_squeeze=bb_squeeze,
            bb_breakout=bb_breakout,
            ema_cross=ema_cross,
            atr_pct=atr_pct,
            stoch_k=stoch_k,
            sr_position=sr_position,
            vwap_dev=vwap_dev,
            ichi_signal=ichi_signal,
            ichi_tk_cross=ichi_tk_cross,
            fib_level=fib_level,
        )

        if is_up_contract:
            bayes = self.bayesian.estimate(spot_change_pct=change_pct, **ta_kwargs)
        else:
            bayes = self.bayesian.estimate(spot_change_pct=0.0, **ta_kwargs)

        bayesian_prob = bayes.probability

        # ── CONFIDENCE DAMPENING ────────────────────────────────────────────
        # ASYMMETRIC DAMPENING: YES=99% WR (110W/1L), NO=18% WR (5W/23L).
        # YES edge is REAL — barely dampen. NO edge is mostly fake from cheap tokens.
        if bayesian_prob > 0.50:
            bayesian_prob = 0.50 + (bayesian_prob - 0.50) * 0.95  # YES: preserve edge
        else:
            bayesian_prob = 0.50 + (bayesian_prob - 0.50) * 0.70  # NO: crush overconfidence

        # Smart money boost (reduced: 0.05 → 0.02, volume-gated)
        try:
            if self.smart_trader is not None:
                sm = self.smart_trader.get_signal(market.get("condition_id", ""))
                if sm and sm.get("total_traders", 0) > 0 and volume_ratio >= 1.0:
                    boost = sm.get("signal", 0) * 0.02  # was 0.05 — too aggressive
                    bayesian_prob = max(0.05, min(0.95, bayesian_prob + boost))
                    logger.info(
                        f"SmartMoney [{','.join(sm.get('buyers', [])[:3]) or 'none'}] "
                        f"signal={sm.get('signal', 0):+.2f} boost={boost:+.3f} "
                        f"prob: {bayes.probability:.3f}→{bayesian_prob:.3f}"
                    )
        except Exception as _e:
            logger.debug(f"SmartMoney signal error: {_e}")

        # ── TOP TRADER COPY SIGNAL ────────────────────────────────────────
        try:
            if hasattr(self, 'top_trader') and self.top_trader is not None:
                _tt_boost = self.top_trader.get_boost(market.get("condition_id", ""))
                if _tt_boost and abs(_tt_boost) > 0.005:
                    bayesian_prob = max(0.05, min(0.95, bayesian_prob + _tt_boost))
                    _tt_sig = self.top_trader.get_signal(market.get("condition_id", ""))
                    if _tt_sig:
                        logger.info(
                            f"TOP_TRADER: {question[:35]} | dir={_tt_sig.direction} "
                            f"conf={_tt_sig.confidence:.2f} boost={_tt_boost:+.3f} "
                            f"YES_vol=${_tt_sig.total_yes_volume:.0f} NO_vol=${_tt_sig.total_no_volume:.0f}"
                        )
        except Exception as _e:
            logger.debug(f"TopTrader signal error: {_e}")

        # ── ORDERBOOK DEPTH ANALYSIS ──────────────────────────────────────
        _ob_analysis = None
        if hasattr(self, '_clob_client') and self._clob_client:
            try:
                _yes_tid = market.get("yes_token_id", "")
                if _yes_tid:
                    _raw_book = self._clob_client.get_order_book(_yes_tid)
                    _ob_analysis = self.ob_analyzer.analyze(_raw_book)
                    if _ob_analysis and _ob_analysis.tradeable:
                        _ob_boost = self.ob_analyzer.get_signal_boost(_ob_analysis)
                        if abs(_ob_boost) > 0.005:
                            bayesian_prob = max(0.05, min(0.95, bayesian_prob + _ob_boost))
                            logger.info(
                                f"OB_DEPTH: {question[:35]} | spread={_ob_analysis.spread_pct:.1%} "
                                f"imb={_ob_analysis.imbalance:+.2f} liq={_ob_analysis.liquidity_score:.2f} "
                                f"boost={_ob_boost:+.3f} | walls: bid=${_ob_analysis.top_bid_wall:.0f} ask=${_ob_analysis.top_ask_wall:.0f}"
                            )
                    elif _ob_analysis and not _ob_analysis.tradeable:
                        logger.info(
                            f"OB_ILLIQUID: {question[:35]} | spread={_ob_analysis.spread_pct:.1%} "
                            f"bid_depth=${_ob_analysis.bid_depth:.0f} ask_depth=${_ob_analysis.ask_depth:.0f}"
                        )
            except Exception as _e:
                logger.debug(f"OB depth analysis error: {_e}")

        # ── YES+NO SUM MONITOR ────────────────────────────────────────────
        try:
            _real_no = market.get("no_best_ask")
            if _real_no and float(_real_no) > 0:
                _sum_result = self.sum_monitor.analyze(
                    yes_ask=yes_price,
                    no_ask=float(_real_no),
                    market_id=question[:40],
                )
                if not _sum_result.market_efficient:
                    _sum_adj = self.sum_monitor.get_edge_adjustment(_sum_result)
                    if abs(_sum_adj) > 0.005:
                        bayesian_prob = max(0.05, min(0.95, bayesian_prob + _sum_adj))
                        logger.info(
                            f"SUM_MONITOR: {question[:35]} | YES+NO={_sum_result.total:.3f} "
                            f"dev={_sum_result.deviation_pct:+.1f}% adj={_sum_adj:+.3f} "
                            f"arb={'YES' if _sum_result.arbitrage_opportunity else 'NO'}"
                        )
        except Exception as _e:
            logger.debug(f"SUM_MONITOR error: {_e}")

        # ── KALSHI CROSS-ARB CHECK ────────────────────────────────────────
        try:
            if hasattr(self, 'kalshi_arb') and self.kalshi_arb is not None and sym:
                _asset_name = next(
                    (k for k, v in ASSET_SYMBOLS.items() if v == sym and len(k) > 3),
                    sym.replace("USDT", "").lower()
                )
                _kalshi_adj = self.kalshi_arb.get_edge_adjustment(_asset_name, yes_price)
                if abs(_kalshi_adj) > 0.005:
                    bayesian_prob = max(0.05, min(0.95, bayesian_prob + _kalshi_adj))
                    logger.info(
                        f"KALSHI_ARB: {question[:35]} | adj={_kalshi_adj:+.3f} "
                        f"(Kalshi fiyat farki sinyal)"
                    )
        except Exception as _e:
            logger.debug(f"KALSHI_ARB error: {_e}")

        # ── MULTI-TIMEFRAME CONSENSUS ────────────────────────────────────────
        _mtf_boost = 0.0
        try:
            if sym and self.binance_feed:
                _mtf = self.binance_feed.get_mtf_consensus(sym)
                if _mtf and _mtf.get("aligned") and _mtf.get("agreement", 0) >= 1.0:
                    _mtf_boost = _mtf.get("boost", 0)
                    if abs(_mtf_boost) > 0.003:
                        bayesian_prob = max(0.05, min(0.95, bayesian_prob + _mtf_boost))
                        _details = _mtf.get("details", {})
                        logger.info(
                            f"MTF_CONSENSUS: {question[:35]} | dir={_mtf.get('direction')} "
                            f"agree={_mtf.get('agreement', 0):.0%} boost={_mtf_boost:+.4f} | "
                            f"5m={_details.get('5m', 0):+.2f}% "
                            f"15m={_details.get('15m', 0):+.2f}% "
                            f"1h={_details.get('1h', 0):+.2f}%"
                        )
                elif _mtf and not _mtf.get("aligned") and _mtf.get("direction") == "MIXED":
                    _dampen = 0.10
                    bayesian_prob = bayesian_prob * (1 - _dampen) + 0.50 * _dampen
                    logger.debug(
                        f"MTF_MIXED: {question[:35]} | Timeframe'ler uyuşmuyor, "
                        f"edge dampened %{_dampen*100:.0f}"
                    )
        except Exception as _e:
            logger.debug(f"MTF_CONSENSUS error: {_e}")

        # ── CROSS-EXCHANGE LEAD-LAG ─────────────────────────────────────────
        try:
            if sym and self.binance_feed:
                _xex = self.binance_feed.get_cross_exchange_signal(sym)
                logger.debug(f"LEAD_LAG_RAW: {question[:30]} | sig={_xex.get('signal')} boost={_xex.get('boost', 0):+.4f}")
                if _xex and _xex.get("signal") != "NEUTRAL" and abs(_xex.get("boost", 0)) > 0.003:
                    bayesian_prob = max(0.05, min(0.95, bayesian_prob + _xex["boost"]))
                    logger.info(
                        f"LEAD_LAG: {question[:35]} | Binance=${_xex.get('binance_price', 0):.2f} "
                        f"Bitstamp=${_xex.get('bitstamp_price', 0):.2f} spread={_xex.get('spread_pct', 0):+.4f}% "
                        f"→ {_xex['signal']} boost={_xex['boost']:+.4f}"
                    )
        except Exception as _e:
            logger.debug(f"LEAD_LAG error: {_e}")

        # ── FUNDING RATE + OPEN INTEREST ─────────────────────────────────────
        try:
            if sym and self.binance_feed:
                _fund = self.binance_feed.get_funding_signal(sym)
                if _fund and _fund.get("signal") != "NEUTRAL" and abs(_fund.get("boost", 0)) > 0.003:
                    bayesian_prob = max(0.05, min(0.95, bayesian_prob + _fund["boost"]))
                    logger.info(
                        f"FUNDING: {question[:35]} | rate={_fund.get('funding_rate', 0):+.4f}% "
                        f"OI_chg={_fund.get('oi_change_pct', 0):+.1f}% risk={_fund.get('risk', '?')} "
                        f"→ {_fund['signal']} boost={_fund['boost']:+.4f}"
                    )
        except Exception as _e:
            logger.debug(f"FUNDING error: {_e}")

        # ── LIQUIDATION CASCADE DETECTION ────────────────────────────────────
        try:
            if sym and self.binance_feed:
                _liq = self.binance_feed.get_liquidation_signal(sym)
                if _liq and _liq.get("net_signal") != "NEUTRAL" and abs(_liq.get("boost", 0)) > 0.002:
                    bayesian_prob = max(0.05, min(0.95, bayesian_prob + _liq["boost"]))
                    logger.info(
                        f"LIQUIDATION: {question[:35]} | long=${_liq.get('long_liq_usd', 0):.0f} "
                        f"short=${_liq.get('short_liq_usd', 0):.0f} count={_liq.get('count', 0)} "
                        f"→ {_liq['net_signal']} boost={_liq['boost']:+.4f}"
                    )
        except Exception as _e:
            logger.debug(f"LIQUIDATION error: {_e}")

        # ── S&P 500 CORRELATION ──────────────────────────────────────────────
        try:
            if self.binance_feed:
                _spx = self.binance_feed.get_spx_signal()
                if _spx and _spx.get("active") and _spx.get("signal") != "NEUTRAL" and abs(_spx.get("boost", 0)) > 0.003:
                    bayesian_prob = max(0.05, min(0.95, bayesian_prob + _spx["boost"]))
                    logger.info(
                        f"SPX_CORR: {question[:35]} | SPX=${_spx.get('spx_price', 0):.0f} "
                        f"chg={_spx.get('spx_change_pct', 0):+.3f}% → {_spx['signal']} "
                        f"boost={_spx['boost']:+.4f}"
                    )
        except Exception as _e:
            logger.debug(f"SPX_CORR error: {_e}")

        # ── FEAR & GREED INDEX ───────────────────────────────────────────────
        if self.binance_feed:
            _fng = self.binance_feed.get_fear_greed()
            _fng_val = _fng.get("fng_value", 50)
            _fng_boost = 0.0
            if _fng_val <= 25:
                # Extreme fear → contrarian bullish (bounce likely)
                _fng_boost = min(0.015, (25 - _fng_val) / 25 * 0.015)
            elif _fng_val >= 75:
                # Extreme greed → contrarian bearish (correction risk)
                _fng_boost = max(-0.015, (_fng_val - 75) / -25 * 0.015)
            if abs(_fng_boost) > 0.003:
                bayesian_prob = max(0.05, min(0.95, bayesian_prob + _fng_boost))
                logger.info(
                    f"FEAR_GREED_SIGNAL: {question[:35]} | FnG={_fng_val} "
                    f"({_fng.get('fng_label', '?')}) boost={_fng_boost:+.4f}"
                )

        # ── ENHANCED SIGNALS (multi-exchange, options, whale, social) ───────
        # IMPORTANT: Total enhanced boost capped at ±0.03 to prevent edge inflation.
        # Each source contributes a small vote; combined they confirm or deny direction.
        _enhanced_total_boost = 0.0
        _ENHANCED_CAP = 0.03  # Max total impact from all enhanced signals
        if self.binance_feed and hasattr(self.binance_feed, 'enhanced'):
            _enh = self.binance_feed.enhanced
            try:
                _mex = _enh.get_multi_exchange_signal(sym) if sym else {}
                if _mex.get("signal") != "NEUTRAL" and abs(_mex.get("boost", 0)) > 0.003:
                    _enhanced_total_boost += _mex["boost"]
                    logger.info(
                        f"MULTI_EX: {question[:35]} | orderflow={_mex.get('orderflow', 0):+.3f} "
                        f"spread={_mex.get('spread_pct', 0):.3f}% → {_mex['signal']} "
                        f"boost={_mex['boost']:+.4f}"
                    )
            except Exception as _e:
                logger.debug(f"MULTI_EX error: {_e}")

            try:
                _opt = _enh.get_options_signal(sym) if sym else {}
                if _opt.get("signal") != "NEUTRAL" and abs(_opt.get("boost", 0)) > 0.003:
                    _enhanced_total_boost += _opt["boost"]
                    logger.info(
                        f"OPTIONS: {question[:35]} | PCR={_opt.get('pcr', 0):.2f} "
                        f"IV={_opt.get('iv', 0):.0f}% → {_opt['signal']} "
                        f"boost={_opt['boost']:+.4f}"
                    )
            except Exception as _e:
                logger.debug(f"OPTIONS error: {_e}")

            try:
                _whale = _enh.get_whale_signal(sym) if sym else {}
                if _whale.get("active"):
                    # Whale = small confirmation boost (not multiplicative)
                    _enhanced_total_boost += 0.005
                    logger.info(f"WHALE: {question[:35]} | active=True +0.005")
            except Exception as _e:
                logger.debug(f"WHALE error: {_e}")

            try:
                _soc = _enh.get_social_signal(sym) if sym else {}
                if _soc.get("signal") != "NEUTRAL" and abs(_soc.get("boost", 0)) > 0.003:
                    _enhanced_total_boost += _soc["boost"]
                    logger.info(
                        f"SOCIAL: {question[:35]} | score={_soc.get('score', 0):.0f} "
                        f"→ {_soc['signal']} boost={_soc['boost']:+.4f}"
                    )
            except Exception as _e:
                logger.debug(f"SOCIAL error: {_e}")

            # Apply capped total boost
            _capped_boost = max(-_ENHANCED_CAP, min(_ENHANCED_CAP, _enhanced_total_boost))
            if abs(_capped_boost) > 0.002:
                bayesian_prob = max(0.05, min(0.95, bayesian_prob + _capped_boost))
                logger.info(
                    f"ENHANCED_TOTAL: {question[:35]} | raw={_enhanced_total_boost:+.4f} "
                    f"capped={_capped_boost:+.4f} → prob={bayesian_prob:.3f}"
                )

        # ── Yön kararı: YES mi NO mu? (FORENSIC DIAGNOSTICS) ────────────────
        # REGIME-ADJUSTED EDGE: Piyasa yönünü edge hesabına entegre et.
        # Problem: Bayesian ~0.52 + ucuz NO token = yapay NO edge.
        # Ama BULLISH rejimde UP gerçekleşiyor ve NO kaybediyor.
        # Fix: Regime yönüne göre edge'i boost/penalize et.
        _regime_name_for_edge = self._current_regime.get("regime", "NEUTRAL")
        _regime_str_for_edge = self._current_regime.get("strength", 0.0)

        # Regime boost: 0.0 (neutral) to 0.08 (strong regime)
        # Bu, bayesian_prob'u regime yönüne doğru iter.
        _regime_edge_boost = min(0.08, _regime_str_for_edge * 0.25)

        if _regime_name_for_edge == "BULLISH":
            # BULLISH: YES edge'i artır, NO edge'i azalt
            adjusted_bayesian = bayesian_prob + _regime_edge_boost
        elif _regime_name_for_edge == "BEARISH":
            # BEARISH: NO edge'i artır, YES edge'i azalt
            adjusted_bayesian = bayesian_prob - _regime_edge_boost
        else:
            # NEUTRAL: Bayesian ~0.52 + cheap NO = fake NO edge.
            # Pull probability slightly toward 0.50 to reduce directional noise.
            # 20% pull: 0.52 → 0.516, 0.55 → 0.54, 0.60 → 0.58
            _neutral_pull = 0.20
            adjusted_bayesian = bayesian_prob * (1 - _neutral_pull) + 0.50 * _neutral_pull

        adjusted_bayesian = max(0.05, min(0.95, adjusted_bayesian))

        yes_edge = adjusted_bayesian - yes_price
        no_prob  = 1.0 - adjusted_bayesian

        # NO fiyatı: gerçek orderbook varsa onu kullan, yoksa sentetik
        real_no_ask = market.get("no_best_ask")
        real_no_bid = market.get("no_best_bid")
        has_no_token = bool(market.get("no_token_id"))
        no_book_fetched = real_no_ask is not None

        # Determine NO price source and value
        if real_no_ask and float(real_no_ask) > 0:
            no_price_ask = float(real_no_ask)
            no_price_source = NoPriceSource.REAL_BOOK
        else:
            no_price_ask = round(1.0 - yes_price, 4)
            no_price_source = NoPriceSource.SYNTHETIC if not no_book_fetched else NoPriceSource.MISSING

        no_edge = no_prob - no_price_ask

        # ── PART 1D: Classify NO-side book health ────────────────────────────
        # Explicit classification of NO ask=0.99 and similar cases
        _NO_UNTRADABLE_THRESHOLD = 0.95  # ask >= 0.95 = effectively dead
        _NO_SUSPICIOUS_THRESHOLD = 0.90  # ask >= 0.90 = suspicious

        no_side_health = "OK"
        if no_price_source == NoPriceSource.REAL_BOOK:
            if no_price_ask >= _NO_UNTRADABLE_THRESHOLD:
                no_side_health = "UNTRADABLE"
            elif no_price_ask >= _NO_SUSPICIOUS_THRESHOLD:
                no_side_health = "SUSPICIOUS"

        # ── Direction decision with explicit reason tracking ──────────────────
        direction_reason: str
        if yes_edge <= 0 and no_edge <= 0:
            direction_reason = NoSideStatus.BOTH_EDGES_NEGATIVE.value
        elif no_price_source != NoPriceSource.REAL_BOOK and no_edge > yes_edge:
            direction_reason = NoSideStatus.NO_REAL_BOOK_MISSING.value
        elif no_side_health == "UNTRADABLE" and no_edge > yes_edge:
            direction_reason = NoSideStatus.NO_SIDE_UNTRADABLE.value
        elif no_side_health == "SUSPICIOUS" and no_edge > yes_edge:
            direction_reason = NoSideStatus.NO_SIDE_BOOK_SUSPICIOUS.value
        elif yes_edge >= no_edge and yes_edge > 0:
            direction_reason = NoSideStatus.YES_EDGE_DOMINATES.value
        elif no_edge > yes_edge and no_edge > 0:
            direction_reason = NoSideStatus.NO_EDGE_DOMINATES.value
        elif yes_edge > 0:
            direction_reason = NoSideStatus.NO_EDGE_NEGATIVE.value
        else:
            direction_reason = NoSideStatus.BOTH_EDGES_NEGATIVE.value

        # Build diagnostics BEFORE direction decision
        diag = SideDiagnostics(
            market_id=market.get("condition_id", ""),
            market_title=question,
            asset=sym or "UNKNOWN",
            horizon=timeframe,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            yes_best_bid=bid_price,
            yes_best_ask=yes_price,
            yes_edge=yes_edge,
            bayesian_prob=bayesian_prob,
            no_best_bid=float(real_no_bid) if real_no_bid else 0.0,
            no_best_ask=no_price_ask,
            no_edge=no_edge,
            no_prob=no_prob,
            no_price_source=str(no_price_source.value),
            no_token_id_present=has_no_token,
            no_book_fetched=no_book_fetched,
            selected_direction="NONE",
            direction_reason=direction_reason,
        )

        # Log full diagnostics at INFO level (not just DEBUG)
        logger.info(
            f"SIDE_DIAG: {question[:45]} | YES_e={yes_edge:.4f} NO_e={no_edge:.4f} | "
            f"no_ask={no_price_ask:.3f} src={no_price_source.value} "
            f"health={no_side_health} | reason={direction_reason}"
        )

        # Store diagnostics for every candidate (even rejected ones)
        self._last_diagnostics[market.get("condition_id", "")] = diag

        # ── Actual direction selection ────────────────────────────────────────
        # MOMENTUM-FIRST: Spot yönü ile Bayesian yönü uyuşmalı.
        # Spot UP + Bayesian > 0.50 → YES. Spot DOWN + Bayesian < 0.50 → NO.
        # Çelişki varsa → trade etme.
        # Veri yoksa (no-data) → sadece Bayesian edge'e güven (fallback).
        has_spot_data = data_source != "no-data"
        # Spot direction: ignore small moves (< 0.30% = noise in 5m crypto)
        # Edge-aware: strong NO edge (>0.15) overrides mild bullish spot
        spot_bullish = change_pct > 0.30
        spot_bearish = change_pct < -0.30
        spot_neutral = not spot_bullish and not spot_bearish
        _regime_name = self._current_regime.get("regime", "NEUTRAL")
        _regime_is_bearish = _regime_name == "BEARISH"
        _regime_is_bullish = _regime_name == "BULLISH"
        bayesian_bullish = bayesian_prob > 0.50

        if has_spot_data:
            # ── EDGE-FIRST DIRECTION SELECTION ──────────────────────────────
            # Shadow data: 92.8% of profitable opportunities are YES-side.
            # Old logic required bayesian_bullish for YES → missed most YES edge.
            # New: pick direction with higher positive edge, apply safeguards.

            # NO safeguard thresholds (regime-adaptive)
            # Key insight: when 4h is strongly bearish (>-0.50%), 5m micro-bounces
            # are noise. The missed BTC drop at 10:06 had +0.06% 5m bounce within
            # -1.02% 4h drop — safeguard killed a 14% edge trade.
            _4h_change = self._current_regime.get("btc_4h_pct", 0)
            _strong_4h_bearish = _4h_change < -0.50  # 4h confirms hard
            if _strong_4h_bearish:
                _NO_SPOT_THRESHOLD = 0.30   # allow mild positive 5m — 4h strongly overrides
                _NO_MIN_ASK = 0.25
            elif _regime_is_bearish:
                _NO_SPOT_THRESHOLD = 0.00   # 5m just needs to not be rising
                _NO_MIN_ASK = 0.25
            else:
                _NO_SPOT_THRESHOLD = 0.05   # neutral: slight positive OK, edge is the signal
                _NO_MIN_ASK = 0.30
            _spot_bearish_for_no = change_pct < _NO_SPOT_THRESHOLD

            # YES safeguard thresholds (symmetric)
            _YES_MAX_PRICE = 0.70      # don't overpay — terrible risk/reward above 0.70
            _YES_MIN_PRICE = 0.15      # too cheap = market says NO strongly

            # Determine best direction by edge
            _yes_viable = (yes_edge > 0
                           and yes_price <= _YES_MAX_PRICE
                           and yes_price >= _YES_MIN_PRICE)
            _no_viable = (no_edge > 0
                          and no_price_ask >= _NO_MIN_ASK
                          and no_price_source == NoPriceSource.REAL_BOOK
                          and no_side_health == "OK")

            # ══════════════════════════════════════════════════════════════
            # CANDLESTICK PATTERN INTEGRATION
            # All 25 patterns from CandlestickAnalyzer cheat sheets.
            # These OVERRIDE Bayesian when pattern signal is strong enough.
            # ══════════════════════════════════════════════════════════════

            # Classify patterns into bullish/bearish reversal signals
            _BULLISH_REVERSAL = {
                "HAMMER", "BULLISH_ENGULFING", "MORNING_STAR",
                "THREE_WHITE_SOLDIERS", "BULLISH_MARUBOZU",
                "TWEEZER_BOTTOM", "THREE_INSIDE_UP", "THREE_OUTSIDE_UP",
                "BULLISH_THREE_LINE_STRIKE", "INVERTED_HAMMER",
                "BULLISH_HARAMI", "DRAGONFLY_DOJI",
            }
            _BEARISH_REVERSAL = {
                "SHOOTING_STAR", "BEARISH_ENGULFING", "EVENING_STAR",
                "THREE_BLACK_CROWS", "BEARISH_MARUBOZU",
                "TWEEZER_TOP", "THREE_INSIDE_DOWN", "THREE_OUTSIDE_DOWN",
                "BEARISH_THREE_LINE_STRIKE", "HANGING_MAN",
                "BEARISH_HARAMI", "GRAVESTONE_DOJI",
            }
            _BULLISH_CONTINUATION = {"RISING_THREE_METHODS"}
            _BEARISH_CONTINUATION = {"FALLING_THREE_METHODS"}

            _bullish_patterns = [p for p in patterns if p in _BULLISH_REVERSAL or p in _BULLISH_CONTINUATION]
            _bearish_patterns = [p for p in patterns if p in _BEARISH_REVERSAL or p in _BEARISH_CONTINUATION]

            # Score: strong patterns (engulfing, 3 soldiers, morning star) = 0.8+
            _pattern_score = CA.pattern_score(patterns)  # -1.0 to +1.0

            if patterns:
                logger.info(
                    f"CANDLE_PATTERNS: {question[:35]} | {patterns} "
                    f"score={_pattern_score:+.2f} bull={len(_bullish_patterns)} bear={len(_bearish_patterns)}"
                )

            # ── PATTERN-DRIVEN YES ACTIVATION ──
            # Strong bullish pattern OR 1+ full green candle after drop = YES opportunity.
            # User feedback: "even 1 full green marubozu after crash should trigger YES"
            _pattern_bullish = _pattern_score >= 0.4 or (
                consecutive_bullish >= 1 and any(
                    p in patterns for p in ("BULLISH_MARUBOZU", "BULLISH_ENGULFING",
                                             "HAMMER", "THREE_WHITE_SOLDIERS", "MORNING_STAR")
                )
            )

            # ── PATTERN-DRIVEN NO ACTIVATION ──
            # Strong bearish pattern = NO opportunity even during bounce.
            # Evening star, 3 black crows, bearish engulfing after bounce = reversal down.
            _pattern_bearish = _pattern_score <= -0.4 or (
                consecutive_bearish >= 1 and any(
                    p in patterns for p in ("BEARISH_MARUBOZU", "BEARISH_ENGULFING",
                                             "SHOOTING_STAR", "THREE_BLACK_CROWS", "EVENING_STAR")
                )
            )

            # ── BOUNCE / CANDLE DIRECTION LOGIC ──
            _bounce_active = (consecutive_bullish >= 2
                              or (bounce_signal and consecutive_bullish >= 1)
                              or _pattern_bullish)
            _bounce_faded = ((consecutive_bearish >= 2 and consecutive_bullish == 0)
                             or _pattern_bearish)

            # YES activation: bullish candle patterns can activate YES direction
            # but edge must be realistic (based on Bayesian prob, not payout)
            if _bounce_active or _pattern_bullish:
                # Use Bayesian-based edge + pattern bonus (not payout formula)
                _bounce_yes_edge = max(yes_edge, bayesian_prob - yes_price)
                # Pattern bonus: strong pattern adds small edge boost
                if _pattern_score >= 0.6:
                    _bounce_yes_edge += 0.02  # strong pattern bonus (was 0.05)
                if (_bounce_yes_edge > 0.05
                        and yes_price <= _YES_MAX_PRICE
                        and yes_price >= _YES_MIN_PRICE
                        and not _pattern_bearish):  # don't activate YES if bearish pattern
                    _yes_viable = True
                    yes_edge = _bounce_yes_edge
                    _reason = f"patterns={_bullish_patterns}" if _bullish_patterns else f"{consecutive_bullish}green"
                    logger.info(
                        f"CANDLE_YES_ACTIVATE: {question[:35]} "
                        f"{_reason} → YES (edge={_bounce_yes_edge:.4f}, price={yes_price:.2f}, "
                        f"pscore={_pattern_score:+.2f})"
                    )

            # NO block during active bounce (unless bounce faded)
            if _no_viable and _bounce_active and not _bounce_faded:
                _no_viable = False
                logger.info(
                    f"BOUNCE_NO_BLOCK: {question[:35]} -- "
                    f"{consecutive_bullish} green / patterns={_bullish_patterns}, NO blocked"
                )

            # NO re-enable when bounce faded (bearish patterns or 2+ red candles)
            if _bounce_faded and not _bounce_active:
                if _no_viable:
                    logger.info(
                        f"BOUNCE_FADED_NO: {question[:35]} -- "
                        f"{consecutive_bearish} red / patterns={_bearish_patterns} → NO re-enabled"
                    )
                # Also boost NO edge for strong bearish patterns
                if _pattern_bearish and no_edge > 0 and not _no_viable:
                    if (no_price_ask >= _NO_MIN_ASK
                            and no_price_source == NoPriceSource.REAL_BOOK
                            and no_side_health == "OK"):
                        _no_viable = True
                        logger.info(
                            f"CANDLE_NO_ACTIVATE: {question[:35]} "
                            f"bearish patterns={_bearish_patterns} → NO re-enabled "
                            f"(edge={no_edge:.4f}, pscore={_pattern_score:+.2f})"
                        )

            # YES safeguard: bearish regime'de YES dikkatli — sadece edge < 0.05 ise blokla
            # BUT: skip safeguard during active bounce — bounce overrides regime
            if _yes_viable and _regime_is_bearish and spot_bearish and yes_edge < 0.05 and not _bounce_active:
                _yes_viable = False
                logger.info(
                    f"YES_SAFEGUARD: {question[:40]} blocked — "
                    f"bearish+low_edge(chg={change_pct:+.2f}%,yes_e={yes_edge:.4f}<0.05)"
                )

            # NO safeguard: sadece spot GÜÇLÜ bullish ise ve edge düşükse blokla
            if _no_viable and change_pct > 0.30 and no_edge < 0.10:
                _no_viable = False
                logger.info(
                    f"NO_SAFEGUARD: {question[:40]} blocked — "
                    f"strong_bullish_spot(chg={change_pct:+.2f}%>0.30%) + low_edge({no_edge:.4f}<0.10)"
                )

            # Bullish regime + NO: edge 0.12+ gerekli (fake breakdown koruması)
            # str>0.05 bile bullish sayılır — NO her zaman riskli bullish'te
            if _no_viable and _regime_is_bullish and no_edge < 0.12:
                _no_viable = False
                logger.info(
                    f"NO_BULLISH_GUARD: {question[:40]} blocked — "
                    f"bullish_regime + low_edge({no_edge:.4f}<0.12)"
                )

            # BTC LEADER CHECK: For alt NO trades, verify BTC is also dropping.
            # All coins correlated ~99%. If BTC rising, alt NO will likely lose.
            if _no_viable and sym and "BTC" not in sym and self.binance_feed is not None:
                try:
                    _btc_sig = self.binance_feed.get_signal("BTCUSDT", timeframe)
                    if _btc_sig and _btc_sig["change_pct"] > 0.05:
                        _no_viable = False
                        logger.info(
                            f"BTC_LEADER_BLOCK: {question[:40]} — "
                            f"BTC +{_btc_sig['change_pct']:.2f}% (bullish), alt NO blocked"
                        )
                except Exception:
                    pass

            # ══════════════════════════════════════════════════════════════
            # COMPOSITE TECH SCORE GATE
            # tech_score aggregates ALL indicators (-1 to +1).
            # If tech_score strongly disagrees with direction → block the trade.
            # This prevents trading against the full technical picture.
            # ══════════════════════════════════════════════════════════════
            if abs(tech_score) >= 0.3:
                logger.info(
                    f"TECH_SCORE: {question[:35]} score={tech_score:+.3f} "
                    f"adx={adx:.0f} obv={obv_slope:+.3f} cmf={cmf:+.3f}"
                )
                # Block YES if tech_score strongly bearish
                if _yes_viable and tech_score <= -0.4 and not _bounce_active:
                    _yes_viable = False
                    logger.info(
                        f"TECH_BLOCK_YES: {question[:35]} — "
                        f"tech_score={tech_score:+.3f} (strongly bearish)"
                    )
                # Block NO if tech_score strongly bullish
                if _no_viable and tech_score >= 0.4 and not _bounce_faded:
                    _no_viable = False
                    logger.info(
                        f"TECH_BLOCK_NO: {question[:35]} — "
                        f"tech_score={tech_score:+.3f} (strongly bullish)"
                    )

            # ── ADX TREND STRENGTH GATE ──
            # ADX < 15 = no trend (ranging market) → skip low-edge trades
            if adx < 15 and abs(tech_score) < 0.3:
                _min_edge_ranging = 0.10  # need stronger edge in ranging market
                if _yes_viable and yes_edge < _min_edge_ranging:
                    _yes_viable = False
                    logger.info(f"ADX_RANGE_BLOCK: {question[:35]} YES — adx={adx:.0f} (ranging), edge too low")
                if _no_viable and no_edge < _min_edge_ranging:
                    _no_viable = False
                    logger.info(f"ADX_RANGE_BLOCK: {question[:35]} NO — adx={adx:.0f} (ranging), edge too low")

            # ── VOLUME FLOW CONFIRMATION ──
            # OBV + CMF disagreeing with direction = weak conviction
            # Only block if BOTH volume indicators disagree (strong signal)
            _vol_bearish = obv_slope < -0.15 and cmf < -0.05
            _vol_bullish = obv_slope > 0.15 and cmf > 0.05
            if _yes_viable and _vol_bearish and yes_edge < 0.08:
                _yes_viable = False
                logger.info(
                    f"VOL_FLOW_BLOCK_YES: {question[:35]} — "
                    f"obv={obv_slope:+.3f} cmf={cmf:+.3f} (money flowing OUT)"
                )
            if _no_viable and _vol_bullish and no_edge < 0.10:
                _no_viable = False
                logger.info(
                    f"VOL_FLOW_BLOCK_NO: {question[:35]} — "
                    f"obv={obv_slope:+.3f} cmf={cmf:+.3f} (money flowing IN)"
                )

            # Pick the better viable direction
            if _yes_viable and _no_viable:
                # Both viable — pick higher edge, with tech_score tiebreaker
                _yes_adjusted = yes_edge + tech_score * 0.02  # slight tech bias
                _no_adjusted = no_edge - tech_score * 0.02
                if _yes_adjusted >= _no_adjusted:
                    direction, trade_price, trade_edge = "YES", yes_price, yes_edge
                    token_id = market.get("yes_token_id", "")
                else:
                    direction, trade_price, trade_edge = "NO", no_price_ask, no_edge
                    token_id = market.get("no_token_id", "")
            elif _yes_viable:
                direction, trade_price, trade_edge = "YES", yes_price, yes_edge
                token_id = market.get("yes_token_id", "")
            elif _no_viable:
                direction, trade_price, trade_edge = "NO", no_price_ask, no_edge
                token_id = market.get("no_token_id", "")
            else:
                diag.selected_direction = "NONE"
                logger.debug(
                    f"NO_VIABLE: {question[:40]} spot={change_pct:+.2f}% "
                    f"bayes={bayesian_prob:.3f} yes_e={yes_edge:.4f} no_e={no_edge:.4f}"
                )
                return None
        else:
            # Spot verisi yok — sadece Bayesian edge
            if yes_edge >= no_edge and yes_edge > 0:
                direction   = "YES"
                trade_price = yes_price
                trade_edge  = yes_edge
                token_id    = market.get("yes_token_id", "")
            elif (no_edge > yes_edge and no_edge > 0
                  and no_price_source == NoPriceSource.REAL_BOOK
                  and no_side_health == "OK"):
                direction   = "NO"
                trade_price = no_price_ask
                trade_edge  = no_edge
                token_id    = market.get("no_token_id", "")
            else:
                diag.selected_direction = "NONE"
                return None

        diag.selected_direction = direction

        # ── OPT-1: REGIME STRENGTH CAP — TIERED (v9.1) ──────────────────
        # Kademeli sistem: str 0.75-0.85 → yarı Kelly, str > 0.85 → blok
        # Gerekçe: SOL chart analizi gösterdi ki orta-güçlü rejimlerde
        # NO edge doğru ama tam blok = kaçırılan kâr. Yarı Kelly ile risk/ödül dengesi.
        _REGIME_SOFT_CAP = 0.75   # yarı Kelly bölgesi başlangıcı
        _REGIME_HARD_CAP = 0.85   # tam blok eşiği
        _current_str = self._current_regime.get("strength", 0.0)
        if direction == "NO" and _current_str > _REGIME_HARD_CAP:
            logger.info(
                f"REGIME_STR_CAP: {question[:40]} | NO blocked — "
                f"str={_current_str:.2f} > {_REGIME_HARD_CAP} (hard cap, oversold bounce risk)"
            )
            diag.selected_direction = "NONE"
            return None
        _regime_kelly_multiplier = 1.0
        if direction == "NO" and _current_str > _REGIME_SOFT_CAP:
            _regime_kelly_multiplier = 0.5
            logger.info(
                f"REGIME_SOFT_CAP: {question[:40]} | NO half-Kelly — "
                f"str={_current_str:.2f} > {_REGIME_SOFT_CAP} (reduced size)"
            )

        # ── REGIME DECAY GUARD (v8) ──────────────────────────────────────
        # Bounce en çok regime strength hızla düştüğünde geliyor.
        # Peak=0.85 → now=0.50 = bounce riski. NO trade'leri duraklat.
        if direction == "NO" and self._regime_decay_pause and trade_edge < 0.08:
            logger.info(
                f"REGIME_DECAY_BLOCK: {question[:40]} | NO trade blocked — "
                f"bounce risk (peak={self._regime_strength_peak:.2f}, edge={trade_edge:.3f}<0.08)"
            )
            diag.selected_direction = "NONE"
            return None
        elif direction == "NO" and self._regime_decay_pause:
            logger.info(
                f"REGIME_DECAY_BYPASS: {question[:40]} | edge={trade_edge:.3f}>=0.08 "
                f"overrides decay pause (peak={self._regime_strength_peak:.2f})"
            )

        # ── OPT-3: MOMENTUM DECELERATION GUARD (v9) ──────────────────────
        # Son 3 mum'da momentum azalıyorsa (|chg[-1]| < |chg[-2]| < |chg[-3]|)
        # bounce riski yüksek. NO trade'leri engelle.
        # Relaxed: edge > 0.10 tolerates deceleration (strong conviction)
        if direction == "NO" and momentum_decelerating and trade_edge < 0.05:
            logger.info(
                f"MOMENTUM_DECEL: {question[:40]} | NO blocked — "
                f"momentum decelerating, edge={trade_edge:.3f} < 0.12 (bounce risk)"
            )
            diag.selected_direction = "NONE"
            return None

        # ── OPT-4: VOLUME CONFIRMATION GATE (v9.2) ───────────────────────
        # DISABLED: 5m crypto markets always have low volume (0.05-0.15x).
        # Edge model + cost model already filter junk signals.
        # Volume gate was blocking every valid 5m signal.
        # if direction == "NO" and volume_ratio < _base_vol and data_source != "no-data":
        #     ...pass

        # ── MAX YES PRICE CAP ──────────────────────────────────────────────
        # YES ask > 0.65 means the market already priced in the move.
        # Buying YES at 0.80 = pay $0.80 to maybe win $0.20. Terrible risk/reward.
        # The momentum edge only exists BEFORE the market reprices.
        # Only applies to YES direction — NO trades at high yes_price are fine.
        _MAX_YES_PRICE = float(os.getenv("MAX_YES_ENTRY_PRICE", 0.70))
        if direction == "YES" and yes_price > _MAX_YES_PRICE:
            logger.debug(
                f"YES_PRICE_CAP: {question[:40]} ask={yes_price:.3f} > {_MAX_YES_PRICE} "
                f"(move already priced in, terrible risk/reward)"
            )
            return None

        # Cost-adjusted net edge — gerçek EV hesabı
        cost = self.edge_model.total_cost(trade_price)
        net_trade_edge = trade_edge - cost

        # Single market arb (rare: YES + NO < 1)
        single_edge = self.edge_model.single_market_edge(yes_price, no_price)

        # Net edge: cost düşüldükten sonra pozitif olmalı — EV-negatif trade açmayız
        edge = max(net_trade_edge, single_edge)

        # ── COIN-SPECIFIC EDGE PENALTY ───────────────────────────────────
        # Data: Solana 57% WR (worst), Bitcoin 65% (mediocre).
        # Higher min_edge = fewer but better trades on weak coins.
        _coin_edge_addon = 0.0
        _asset_name = _detect_asset(question) or ""
        if "SOL" in _asset_name.upper() or "Solana" in question:
            _coin_edge_addon = 0.03  # SOL: 57% WR → need +3% more edge
        elif "BTC" in _asset_name.upper() or "Bitcoin" in question:
            _coin_edge_addon = 0.01  # BTC: 65% WR → slight penalty

        # Asymmetric min edge: YES needs 0.05+, NO needs 0.07+
        # OPT-5: Adaptive — regime addon ONLY for COUNTER-regime trades
        # Regime yönündeki trade'ler penalize edilmemeli (BULLISH'te YES, BEARISH'te NO)
        _regime_str = self._current_regime.get("strength", 0.0)
        _base_edge = self.min_edge_yes if direction == "YES" else self.min_edge_no
        _base_edge += _coin_edge_addon  # apply coin penalty
        _follows_regime = (
            (direction == "YES" and _regime_name_for_edge == "BULLISH") or
            (direction == "NO" and _regime_name_for_edge == "BEARISH")
        )
        _is_neutral = _regime_name_for_edge == "NEUTRAL"
        if _follows_regime or _is_neutral:
            _regime_addon = 0.0  # Regime yönünde veya NEUTRAL → addon yok
        else:
            _regime_addon = _regime_str * 0.10  # Counter-regime → daha yüksek threshold
        effective_min_edge = _base_edge + _regime_addon

        # ── REGIME-AWARE NO GATE ──────────────────────────────────────────
        # Lesson #14/#18: In BULLISH regime, 5m dips are fake breakdowns.
        # 4h BULLISH + 5m DOWN = pullback (temporary), NOT trend reversal.
        # All NO trades at edge 0.05-0.06 LOST in bullish regime (0W/5L).
        # Gate: NO in BULLISH regime requires edge >= 0.12 (near-certainty).
        # In BEARISH regime, NO is natural — keep standard 0.07 threshold.
        regime = self._current_regime
        _regime_type = regime.get("regime", "NEUTRAL")
        if direction == "NO" and _regime_type == "BULLISH":
            # BULLISH rejimde NO her zaman riskli — str>0.05 bile yeterli
            # March 19 verisi: str=0.08-0.21 BULLISH'te 9/9 NO trade LOSS
            _bull_str = regime.get("strength", 0)
            if _bull_str > 0.05:
                # Tiered: weak bullish → 0.12, strong bullish → 0.18
                _no_floor = 0.12 + _bull_str * 0.15
                effective_min_edge = max(effective_min_edge, _no_floor)
                logger.info(
                    f"REGIME_NO_GATE: {question[:40]} BULLISH regime (str={_bull_str:.2f}) "
                    f"→ NO min_edge raised to {effective_min_edge:.3f}"
                )
        elif direction == "NO" and _regime_type == "NEUTRAL":
            # NEUTRAL regime: NO tokens systematically cheap → fake edge.
            # March 19 data: ALL 5m NO trades LOST, only 15m NO won.
            # 5m is too noisy — +0.05% micro-move = UP = NO loses.
            # 15m gives time for direction to materialize.
            # Trend-aware: if spot is dropping hard, lower the floor
            _trending = abs(change_pct) > 0.15
            if timeframe == "5m" and not _trending:
                _neutral_no_floor = 0.12  # 5m flat: still cautious
            else:
                _neutral_no_floor = 0.08  # 5m trending or 15m: standard
            if effective_min_edge < _neutral_no_floor:
                effective_min_edge = _neutral_no_floor
                logger.info(
                    f"NEUTRAL_NO_GATE: {question[:40]} [{timeframe}] → NO min_edge raised to {effective_min_edge:.3f}"
                )

        if edge < effective_min_edge:
            logger.debug(
                f"EDGE_REJECT: {question[:40]} {direction} | raw={trade_edge:.4f} cost={cost:.4f} "
                f"net={net_trade_edge:.4f} single={single_edge:.4f} min={effective_min_edge:.3f}"
            )
            return None

        # ── TIME-OF-DAY NO GATE (regime-aware) ─────────────────────────────
        # Data: NO in flat/choppy market outside 6:30-8AM = 0% WR.
        # BUT: If market is BEARISH or spot is dropping hard, NO is correct.
        # Allow NO outside window ONLY when regime confirms downtrend.
        if direction == "NO":
            try:
                from zoneinfo import ZoneInfo
                _now_et = datetime.now(ZoneInfo("America/New_York"))
                _h_et = _now_et.hour + _now_et.minute / 60.0
                _outside_window = not (6.5 <= _h_et <= 8.0)
                _regime_bearish = regime.get("regime") == "BEARISH"
                _spot_dropping = change_pct < -0.15  # strong spot drop
                if _outside_window and not _regime_bearish and not _spot_dropping:
                    logger.info(
                        f"TIME_NO_GATE: {question[:40]} blocked — "
                        f"NO outside 6:30-8AM + no bearish signal, now={_now_et.strftime('%I:%M%p')} ET"
                    )
                    return None
            except Exception:
                pass

        # ── CAUTIOUS HOUR FILTER ──────────────────────────────────────────
        # Data: 9AM ET = 36% WR (-$12), 4PM ET = 38% WR (-$23), 6AM = 33%.
        # Market open/close volatility = noisy. Don't block — require stronger edge.
        # 1.5x min edge + half Kelly = only high-conviction trades survive.
        try:
            from zoneinfo import ZoneInfo
            _now_et_h = datetime.now(ZoneInfo("America/New_York"))
            _hour_et_int = _now_et_h.hour
            _CAUTIOUS_HOURS = {9, 16, 6}  # 9AM, 4PM, 6AM ET
            if _hour_et_int in _CAUTIOUS_HOURS:
                _cautious_min = edge * 0  # reset — use 1.5x effective_min_edge as floor
                _cautious_min = effective_min_edge * 1.50
                if edge < _cautious_min:
                    logger.info(
                        f"CAUTIOUS_HOUR: {question[:40]} edge={edge:.3f} < {_cautious_min:.3f} — "
                        f"{_now_et_h.strftime('%I%p')} ET needs stronger conviction"
                    )
                    return None
                # Edge passed — but still half Kelly for risk control
                _ch_original = size
                size = size * 0.50
                logger.info(
                    f"CAUTIOUS_HOUR: {question[:40]} PASS edge={edge:.3f} — "
                    f"half Kelly ${_ch_original:.2f}->${size:.2f} ({_now_et_h.strftime('%I%p')} ET)"
                )
        except Exception:
            pass

        # Stoikov giriş fiyatı
        time_remaining = self._time_remaining_fraction(market, timeframe)
        time_left_sec = self._time_left_seconds(market)
        if time_left_sec < 60:
            logger.info(f"TIME_REJECT: {question[:40]} time_left={time_left_sec:.0f}s (<60s)")
            return None
        if direction == "YES":
            entry_price = yes_price  # FOK: must be at ask to fill
        else:
            entry_price = no_price_ask  # FOK: must be at ask to fill

        # Kelly pozisyon büyüklüğü (seçilen edge ile, adaptive fraction)
        size = self.kelly.position_size(
            edge=edge,
            price=trade_price,
            capital=capital,
            signal_strength=bayes.signal_strength,
        )
        # Spot magnitude confidence: |change| < 0.10% = noise → half Kelly
        if abs(change_pct) < 0.10:
            original_size_spot = size
            size = size * 0.50
            logger.info(f"MICRO_MOVE_SCALE: {question[:40]} |chg|={abs(change_pct):.3f}%<0.10% → ${original_size_spot:.2f}→${size:.2f}")

        # 5m NO in flat market: half Kelly. In trending market (|chg|>0.15%): full Kelly.
        if direction == "NO" and timeframe == "5m" and abs(change_pct) < 0.15:
            original_size_5m = size
            size = size * 0.50
            logger.info(f"5M_NO_FLAT_SCALE: {question[:40]} → half Kelly (flat) ${original_size_5m:.2f}→${size:.2f}")

        # Regime soft cap: yarı Kelly uygula (str 0.75-0.85 arası)
        if _regime_kelly_multiplier < 1.0:
            original_size = size
            size = size * _regime_kelly_multiplier
            logger.info(f"REGIME_HALF_KELLY: {question[:40]} | ${original_size:.2f} → ${size:.2f} (×{_regime_kelly_multiplier})")
        if size <= 0 or capital < size:
            logger.debug(f"KELLY_REJECT: {question[:40]} {direction} edge={edge:.4f} size=${size:.2f} cap=${capital:.2f}")
            return None

        # ── ML QUALITY SCORE ──────────────────────────────────────────────
        _asset_short = _detect_asset(question) or ""
        _asset_short = _asset_short.replace("USDT", "")
        _now_utc = datetime.now(timezone.utc)
        _hour_et = (_now_utc.hour - 4) % 24  # crude UTC→ET
        _minute_et = _now_utc.minute
        ml_score = self.ml.predict({
            "asset": _asset_short, "direction": direction,
            "entry_price": trade_price, "edge": edge,
            "hour_et": _hour_et, "minute_et": _minute_et,
            "window_minutes": {"5m": 5, "15m": 15, "1h": 60}.get(timeframe, 15),
        })
        # ML gate: strong LOSS prediction → reduce size by 50%
        if ml_score < -0.5:
            original_ml = size
            size = size * 0.5
            logger.info(f"ML_CAUTION: {question[:40]} ml={ml_score:+.3f} → ${original_ml:.2f}→${size:.2f}")
        elif ml_score > 0.5:
            logger.info(f"ML_BOOST: {question[:40]} ml={ml_score:+.3f} (high confidence)")

        # ── GOLDEN HOUR BOOST ────────────────────────────────────────────
        # Data: 5-8PM ET (1-4AM TR) = 73-100% WR, +$138 profit.
        # These hours have highest edge — boost Kelly by 1.3x (capped at max_bet).
        # Also 11AM ET = 74% WR, 3PM ET = 70% WR → 1.15x mild boost.
        try:
            _gh_hour = _hour_et  # already computed above (crude UTC→ET)
            _GOLDEN_HOURS = {17, 18, 19}      # 5PM, 6PM, 7PM ET → 1.3x
            _GOOD_HOURS = {11, 15, 3, 4}      # 11AM, 3PM ET + 3AM, 4AM ET (TR night) → 1.15x
            if _gh_hour in _GOLDEN_HOURS:
                _gh_original = size
                size = size * 1.30
                logger.info(f"GOLDEN_HOUR: {question[:40]} {_gh_hour}:00 ET → ${_gh_original:.2f}→${size:.2f} (×1.30)")
            elif _gh_hour in _GOOD_HOURS:
                _gh_original = size
                size = size * 1.15
                logger.info(f"GOOD_HOUR: {question[:40]} {_gh_hour}:00 ET → ${_gh_original:.2f}→${size:.2f} (×1.15)")
        except Exception:
            pass

        reasoning = (
            f"[{data_source}] {direction} Bayesian={bayesian_prob:.3f} vs YES={yes_price:.3f} | "
            f"Edge={edge:.3f} | chg={change_pct:+.2f}% RSI={rsi:.0f} "
            f"vol={volume_ratio:.1f}x OB={ob_imbalance:+.2f} | Z={z_score:.1f} | "
            f"no_src={no_price_source.value} no_ask={no_price_ask:.3f}"
        )
        logger.info(
            f"ARB [{signal_type}/{timeframe}]: {question[:50]} | "
            f"{direction}@{trade_price:.3f} P={bayesian_prob:.3f} Edge={edge:.3f} ${size:.2f} "
            f"tech={tech_score:+.2f} adx={adx:.0f} ml={ml_score:+.3f}"
        )

        return TradeSignal(
            market=market, direction=direction,
            bayesian_prob=bayesian_prob, market_price=yes_price,
            edge=edge, entry_price=entry_price, size=size,
            z_score=z_score, signal_type=signal_type, reasoning=reasoning,
            token_id=token_id,
            side_diagnostics=diag,
        )

    def _time_left_seconds(self, market: dict) -> float:
        """Market kapanışına kalan saniye."""
        try:
            end_str = market.get("endDate") or market.get("end_date_iso") or market.get("endDateIso", "")
            if not end_str:
                return 300.0
            s = str(end_str).replace("Z", "+00:00")
            if len(s) == 10:
                s += "T23:59:00+00:00"
            end_dt = datetime.fromisoformat(s)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return max(0.0, (end_dt - now).total_seconds())
        except Exception:
            return 300.0

    def _time_remaining_fraction(self, market: dict, timeframe: str = "5m") -> float:
        time_left = self._time_left_seconds(market)
        # Market horizon'a göre window belirle
        window_map = {"5m": 300.0, "15m": 900.0, "1h": 3600.0, "4h": 14400.0}
        window = window_map.get(timeframe, 300.0)
        return max(0.0, min(1.0, time_left / window))
