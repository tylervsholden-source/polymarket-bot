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


def _get_current_et_hour() -> int:
    """Current hour in America/New_York, as a separate patch point for tests."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York")).hour
    except Exception:
        return (datetime.now(timezone.utc).hour - 4) % 24


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


# TradeClassifier.predict()'e beslenen window_minutes özelliği — training
# tarafında ml_classifier.TradeClassifier._parse_window() sorudan gerçek
# başlangıç/bitiş saatlerini parse edip dakika farkını döner (4h market için
# ~240). Bu eşleme _time_remaining_fraction()'daki window_map (300/900/3600/
# 14400sn = 5/15/60/240dk) ile aynı dört bucket'ı kapsamalı — "4h" burada
# eksikti ve `.get(timeframe, 15)` varsayılanına düşüyordu, yani canlıdaki
# her 4h sinyali modele window_minutes=15 (gerçek: ~240) besliyordu. İzole
# test edilebilmesi için ayrı fonksiyon (bkz. _momentum_decel_blocks_no).
_ML_WINDOW_MINUTES_MAP = {"5m": 5, "15m": 15, "1h": 60, "4h": 240}


def _ml_window_minutes(timeframe: str) -> float:
    return _ML_WINDOW_MINUTES_MAP.get(timeframe, 15)


def _momentum_decel_blocks_no(direction: str, momentum_decelerating: bool) -> bool:
    """OPT-3: Momentum Deceleration Guard (CLAUDE.md v9 — "Tümü Aktif").

    Son 3 mumda |change| azalıyorsa (momentum_decelerating) → bounce riski →
    NO trade'i engelle. YES yönünü etkilemez.

    `momentum_decelerating` uzun süre hesaplanıp hiç okunmuyordu (git blame
    9b5fd52, "MOMENTUM_DECEL kaldırıldı" yorumu) — bu fonksiyon gate'i geri
    bağlar ve izole test edilebilir kılar.
    """
    return direction == "NO" and momentum_decelerating


class ArbitrageEngine:
    def __init__(self, http_session=None, binance_feed=None, smart_trader_tracker=None,
                 top_trader=None, kalshi_arb=None, clob_client=None,
                 latency_arb=None):
        import httpx
        self.session = http_session if http_session is not None else httpx.AsyncClient(timeout=8)
        self.binance_feed = binance_feed           # BinanceFeed instance (enjekte edilir)
        self.smart_trader = smart_trader_tracker   # SmartTraderTracker instance
        self.top_trader = top_trader               # TopTraderTracker instance
        self.kalshi_arb = kalshi_arb               # KalshiArbTracker instance
        self._clob_client = clob_client            # py-clob-client (for L2 orderbook)
        self.latency_arb = latency_arb             # LatencyArbEngine (spike sinyal kaynağı)

        self.bayesian   = BayesianEstimator()
        self.edge_model = EdgeModel()
        self.spread_model = SpreadModel(window=30, min_z=1.8)
        self.stoikov    = StoikovExecutor(gamma=0.15)
        self.kelly      = KellyCriterion()
        self.mc         = MonteCarloSimulator(n_simulations=1000)
        self.ml         = TradeClassifier()    # ML trade quality predictor
        self.ob_analyzer = OrderbookAnalyzer()  # L2 orderbook depth
        self.sum_monitor = SumMonitor()         # YES+NO sum arbitrage

        # TRADE MEMORY (412 trade analysis):
        # YES: 72% WR, NO: 33% WR. NO is a money pit (-$120).
        # NO needs MUCH higher edge to be profitable.
        # 5m YES is gold standard (79% WR), 15m anything is weak (44% WR).
        _env_edge = float(os.getenv("MIN_EDGE_THRESHOLD", 0.0))
        self.min_edge_yes  = max(0.12, _env_edge)  # Makul: sadece gerçek edge varsa gir
        self.min_edge_no   = max(0.18, _env_edge)  # NO çok riskli (33% WR) — yüksek edge şart
        self.min_edge      = max(0.12, _env_edge)  # legacy fallback
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

    def _maybe_run_monte_carlo(self, edge: float, capital: float, price: float = 0.50) -> bool:
        import time
        now = time.time()
        if self._mc_result is None or (now - self._mc_last_run) > self._mc_interval:
            self._mc_result = self.mc.simulate(
                edge=edge, capital=capital, price=price, n_trades=100,
                fill_rate=0.85,
                position_size_pct=float(os.getenv("MAX_POSITION_PCT", 0.20)),
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
            mc_viable = self._maybe_run_monte_carlo(
                signals[0].edge, capital, signals[0].entry_price
            )
            if not mc_viable:
                # SHADOW MODE: payout formula was only just corrected
                # (2026-09-15, 50th review) — log what the gate would do
                # without blocking trades yet, so a few cycles of live
                # data can confirm it isn't over-tripping before it's
                # allowed to actually filter signals (MC_GATE_ENFORCE=true).
                if os.getenv("MC_GATE_ENFORCE", "false").lower() == "true":
                    logger.warning(
                        f"MC_GATE: Monte Carlo not viable, blocking {len(signals)} sinyal"
                    )
                    return []
                logger.info(
                    "MC_GATE_SHADOW: Monte Carlo not viable — would block "
                    f"{len(signals)} sinyal (enforcement off, MC_GATE_ENFORCE=false)"
                )

        return signals

    async def _evaluate_market(
        self, market: dict, capital: float, z_score: float, signal_type: str
    ) -> TradeSignal | None:
        question  = market.get("question", "")
        yes_price = float(market.get("best_ask", 0) or 0)
        bid_price = float(market.get("best_bid", 0) or 0)
        no_price  = 1.0 - (bid_price if bid_price > 0 else yes_price)
        edge = 0.0  # defensive init — Python 3.14 unbound guard

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
            trend_pct    = spot.get("trend_pct", 0.0)
            macro_trend_pct = spot.get("macro_trend_pct", 0.0)
            volatility   = spot["volatility"]
            ob_imbalance = spot["ob_imbalance"]
            rsi          = spot["rsi"]
            volume_ratio = spot["volume_ratio"]
            macd_hist    = spot["macd_hist"]
            patterns     = spot.get("patterns", [])

            # ── VOLUME CONFIRMATION GATE ─────────────────────────────────────
            # Low volume = noise, not signal. Research: vol_ratio < 1.2 → skip
            # Exception: very high edge (>0.15) can override — genuine mispricing
            VOLUME_GATE_MIN = float(os.getenv("VOLUME_GATE_MIN", "0.8"))  # was 1.2, relaxed for live
            if volume_ratio < VOLUME_GATE_MIN and volume_ratio > 0:
                logger.debug(f"VOLUME_GATE: vol_ratio={volume_ratio:.2f} < {VOLUME_GATE_MIN} → skip {question[:40]}")
                return None

            # FIX-5: MOMENTUM DECELERATION — calculate inline if not provided by BinanceFeed
            momentum_decelerating = spot.get("momentum_decelerating", False)
            if not momentum_decelerating and "candle_changes" in spot:
                # If last 3 candle changes available, detect deceleration pattern
                candle_chgs = spot.get("candle_changes", [])
                if len(candle_chgs) >= 3:
                    chg1, chg2, chg3 = abs(candle_chgs[-1]), abs(candle_chgs[-2]), abs(candle_chgs[-3])
                    momentum_decelerating = (chg1 < chg2 < chg3)  # each move smaller than prior
            elif not momentum_decelerating:
                # Fallback: derive from volatility and change_pct
                # If |change_pct| < volatility * 0.5, likely momentum is weak
                if volatility > 0 and abs(change_pct) < volatility * 0.5:
                    momentum_decelerating = True
            consecutive_bearish = spot.get("consecutive_bearish", 0)
            consecutive_bullish = spot.get("consecutive_bullish", 0)
            bounce_signal = spot.get("bounce_signal", False)
            pre_bounce_streak = spot.get("pre_bounce_streak", 0)
            bullish_exhaustion = spot.get("bullish_exhaustion", False)
            bullish_exhaustion_magnitude = spot.get("bullish_exhaustion_magnitude", 0.0)
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
            trend_pct = 0.0
            macro_trend_pct = 0.0
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
            bullish_exhaustion = False
            bullish_exhaustion_magnitude = 0.0
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

        # ── CROSS-EXCHANGE BOOST (Bayesian input) ──────────────────────
        _cross_boost = 0.0
        try:
            if sym and self.binance_feed:
                _xex = self.binance_feed.get_cross_exchange_signal(sym)
                _cross_boost = _xex.get("boost", 0.0) if _xex else 0.0
        except Exception:
            pass

        # ── REGIME params for Bayesian ────────────────────────────────
        _regime_str = regime.get("strength", 0.0)
        _regime_dir = regime.get("regime", "NEUTRAL")

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
            cross_exchange_boost=_cross_boost,
            regime_strength=_regime_str,
            regime_direction=_regime_dir,
            trend_pct=trend_pct,
        )

        # FIX-2: TIMEFRAME-AWARE BAYESIAN — pass timeframe parameter
        # 5m YES: 77% WR (gold), 15m YES: 50% WR (weak), 15m NO: 32% WR (toxic)
        # For 15m: increase dampening, increase min_edge, reduce momentum weight, scale volatility
        if is_up_contract:
            bayes = self.bayesian.estimate(spot_change_pct=change_pct, **ta_kwargs)
        else:
            bayes = self.bayesian.estimate(spot_change_pct=0.0, **ta_kwargs)

        bayesian_prob = bayes.probability

        # FIX-2 part 2: 15M-SPECIFIC DAMPENING ADJUSTMENTS
        # 15m markets are significantly weaker (50% WR for YES, 32% for NO vs 77%/33% in 5m)
        if timeframe == "15m":
            if bayesian_prob > 0.50:
                # YES: dampen more aggressively for 15m (0.93 → 0.90)
                bayesian_prob = 0.50 + (bayesian_prob - 0.50) * 0.90
            else:
                # NO: dampen more aggressively for 15m (0.88 → 0.75)
                bayesian_prob = 0.50 + (bayesian_prob - 0.50) * 0.75

        # ── CONFIDENCE DAMPENING ────────────────────────────────────────────
        # FIX-4: Asymmetric dampening corrected (for 5m and other timeframes)
        # ASYMMETRIC DAMPENING: YES=99% WR (110W/1L), NO=18% WR (5W/23L).
        # YES edge is REAL — barely dampen. NO edge is mostly fake from cheap tokens.
        elif timeframe != "15m":  # Apply standard dampening only if not 15m
            if bayesian_prob > 0.50:
                bayesian_prob = 0.50 + (bayesian_prob - 0.50) * 0.93  # YES: slightly more conservative (0.95→0.93)
            else:
                bayesian_prob = 0.50 + (bayesian_prob - 0.50) * 0.88  # NO: less aggressive (0.85→0.88) for viability

        # ── HARD PROBABILITY CAP ──────────────────────────────────────────
        # Data: DOGE P=0.874→LOSS, HYPE P=0.917→LOSS. 5dk crypto'da P>0.65 gerçekçi değil.
        # Cap after dampening to prevent overconfidence from Bayesian model.
        _PROB_CAP = 0.65
        if bayesian_prob > _PROB_CAP:
            logger.info(
                f"PROB_CAP: {question[:35]} | P={bayesian_prob:.3f} → {_PROB_CAP} "
                f"(5dk window'da >{_PROB_CAP*100:.0f}% confidence gerçekçi değil)"
            )
            bayesian_prob = _PROB_CAP
        elif bayesian_prob < (1 - _PROB_CAP):
            logger.info(
                f"PROB_CAP: {question[:35]} | P={bayesian_prob:.3f} → {1-_PROB_CAP} "
                f"(5dk window'da <{(1-_PROB_CAP)*100:.0f}% confidence gerçekçi değil)"
            )
            bayesian_prob = 1 - _PROB_CAP

        # Aggregate-cap baseline: captured BEFORE any external boost (SmartMoney,
        # TopTrader, OB_Depth, KalshiArb, Enhanced) is applied, so the ±0.04
        # aggregate cap below can actually bound their *combined* effect. Using
        # `_pre_boost_prob` (captured later, after SM/TT/OB/Kalshi already ran —
        # see its own comment) for this purpose would make the cap a no-op for
        # exactly those four signals, since bayesian_prob - _pre_boost_prob is
        # always ~0 by construction.
        _prob_before_external_boosts = bayesian_prob

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
                        # FIX: SUM_MONITOR boost DISABLED — was pushing toward NO on overpriced markets
                        # bayesian_prob = max(0.05, min(0.95, bayesian_prob + _sum_adj))
                        logger.info(
                            f"SUM_MONITOR: {question[:35]} | YES+NO={_sum_result.total:.3f} "
                            f"dev={_sum_result.deviation_pct:+.1f}% adj={_sum_adj:+.3f} "
                            f"arb={'YES' if _sum_result.arbitrage_opportunity else 'NO'}"
                        )
        except Exception as _e:
            logger.debug(f"SUM_MONITOR error: {_e}")

        # ── OVERPRICED MARKET BLOCK ──────────────────────────────────────
        # YES+NO > 1.10 = market maker spread too wide, edge is fake
        try:
            _real_no_price = market.get("no_best_ask")
            if _real_no_price and float(_real_no_price) > 0:
                _sum_total = yes_price + float(_real_no_price)
                if _sum_total > 1.50:
                    logger.info(
                        f"OVERPRICED_BLOCK: {question[:40]} | YES+NO={_sum_total:.3f} "
                        f"({(_sum_total-1)*100:+.0f}% sapma) → trade blocked"
                    )
                    return None
        except Exception:
            pass

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

        # ── EXTERNAL BOOST TRACKING ────────────────────────────────────────
        # Track total external boost to enforce aggregate cap of ±0.04
        # BUG (16th daily review): this used to be captured BEFORE SmartMoney/
        # TopTrader/OB_Depth/KalshiArb ran, so the reset below silently wiped
        # those four (not in the "Kapatılan" list) along with the intentionally
        # disabled macro signals. Captured here — after they've applied — so
        # only the signals actually named below get reset.
        _pre_boost_prob = bayesian_prob
        _TOTAL_BOOST_CAP = 0.04  # Max total impact from ALL external signals combined

        # ══════════════════════════════════════════════════════════════════════
        # TÜM EXTERNAL BOOST'LAR DEVRE DIŞI — 5dk window için macro sinyaller zararlı
        # YES %70 WR vs NO %35 WR → boost'lar sürekli bearish push yapıyordu
        # Sadece Bayesian core (spot price action) kalıyor
        # Kapatılan: SPIKE, MTF, LEAD_LAG, FUNDING, LS_RATIO, LIQUIDATION,
        #            SPX_CORR, FNG, ENHANCED (multi-exchange, options, whale, social)
        # NOT: SmartMoney/TopTrader/OB_Depth/KalshiArb bu listede yok — kasıtlı
        # olarak açık kalıyorlar (bkz. yukarıdaki _pre_boost_prob capture noktası).
        # ══════════════════════════════════════════════════════════════════════
        bayesian_prob = _pre_boost_prob  # Tüm boost'ları sıfırla, sadece core Bayesian
        # ── LATENCY ARB SPIKE BOOST ──────────────────────────────────────────
        _spike_boost = 0.0
        try:
            if self.latency_arb and sym:
                _coin_name = sym.replace("USDT", "").lower()
                _spike_boost = self.latency_arb.get_spike_boost(_coin_name, max_age_sec=30.0)
                if abs(_spike_boost) > 0.005:
                    # FIX: SPIKE boost DISABLED — listed under "Kapatılan" above but
                    # was never actually commented out, silently undoing the reset.
                    # bayesian_prob = max(0.05, min(0.95, bayesian_prob + _spike_boost))
                    logger.info(
                        f"SPIKE_BOOST: {question[:35]} | boost={_spike_boost:+.4f} (DISABLED)"
                    )
        except Exception as _e:
            logger.debug(f"SPIKE_BOOST error: {_e}")

        # ── MULTI-TIMEFRAME CONSENSUS ────────────────────────────────────────
        _mtf_boost = 0.0
        try:
            if sym and self.binance_feed:
                _mtf = self.binance_feed.get_mtf_consensus(sym)
                if _mtf and _mtf.get("aligned") and _mtf.get("agreement", 0) >= 1.0:
                    _mtf_boost = _mtf.get("boost", 0)
                    if abs(_mtf_boost) > 0.003:
                        # FIX: MTF boost DISABLED — listed under "Kapatılan" above but
                        # was never actually commented out, silently undoing the reset.
                        # bayesian_prob = max(0.05, min(0.95, bayesian_prob + _mtf_boost))
                        _details = _mtf.get("details", {})
                        logger.info(
                            f"MTF_CONSENSUS: {question[:35]} | dir={_mtf.get('direction')} "
                            f"agree={_mtf.get('agreement', 0):.0%} boost={_mtf_boost:+.4f} (DISABLED) | "
                            f"5m={_details.get('5m', 0):+.2f}% "
                            f"15m={_details.get('15m', 0):+.2f}% "
                            f"1h={_details.get('1h', 0):+.2f}%"
                        )
                elif _mtf and not _mtf.get("aligned") and _mtf.get("direction") == "MIXED":
                    # FIX: MTF mixed-dampen DISABLED — same "Kapatılan" MTF entry covers
                    # both the aligned-boost and the mixed-dampen path.
                    # _dampen = 0.10
                    # bayesian_prob = bayesian_prob * (1 - _dampen) + 0.50 * _dampen
                    logger.debug(
                        f"MTF_MIXED: {question[:35]} | Timeframe'ler uyuşmuyor (DISABLED, dampen atlanmadı)"
                    )
        except Exception as _e:
            logger.debug(f"MTF_CONSENSUS error: {_e}")

        # ── CROSS-EXCHANGE LEAD-LAG ─────────────────────────────────────────
        try:
            if sym and self.binance_feed:
                _xex = self.binance_feed.get_cross_exchange_signal(sym)
                logger.debug(f"LEAD_LAG_RAW: {question[:30]} | sig={_xex.get('signal')} boost={_xex.get('boost', 0):+.4f}")
                if _xex and _xex.get("signal") != "NEUTRAL" and abs(_xex.get("boost", 0)) > 0.003:
                    # FIX: LEAD_LAG boost DISABLED — listed under "Kapatılan" above but
                    # was never actually commented out, silently undoing the reset.
                    # bayesian_prob = max(0.05, min(0.95, bayesian_prob + _xex["boost"]))
                    logger.info(
                        f"LEAD_LAG: {question[:35]} | Binance=${_xex.get('binance_price', 0):.2f} "
                        f"Bitstamp=${_xex.get('bitstamp_price', 0):.2f} spread={_xex.get('spread_pct', 0):+.4f}% "
                        f"→ {_xex['signal']} boost={_xex['boost']:+.4f} (DISABLED)"
                    )
        except Exception as _e:
            logger.debug(f"LEAD_LAG error: {_e}")

        # ── FUNDING RATE + OPEN INTEREST ─────────────────────────────────────
        try:
            if sym and self.binance_feed:
                _fund = self.binance_feed.get_funding_signal(sym)
                if _fund and _fund.get("signal") != "NEUTRAL" and abs(_fund.get("boost", 0)) > 0.003:
                    # FIX: FUNDING boost DISABLED — bearish bias overriding spot momentum
                    # bayesian_prob = max(0.05, min(0.95, bayesian_prob + _fund["boost"]))
                    logger.info(
                        f"FUNDING: {question[:35]} | rate={_fund.get('funding_rate', 0):+.4f}% "
                        f"OI_chg={_fund.get('oi_change_pct', 0):+.1f}% risk={_fund.get('risk', '?')} "
                        f"→ {_fund['signal']} boost={_fund['boost']:+.4f}"
                    )
        except Exception as _e:
            logger.debug(f"FUNDING error: {_e}")

        # ── LONG/SHORT RATIO (crowd contrarian + smart money follow) ────────
        _smart_signal_dir = "NEUTRAL"  # persist for smart conflict gate
        try:
            if sym and self.binance_feed:
                _ls = self.binance_feed.get_long_short_signal(sym)
                if _ls and abs(_ls.get("boost", 0)) > 0.003:
                    # FIX: LS_RATIO boost DISABLED — crowd bearish bias was overriding spot momentum
                    # bayesian_prob = max(0.05, min(0.95, bayesian_prob + _ls["boost"]))
                    _smart_signal_dir = _ls.get("smart_signal", "NEUTRAL")
                    logger.info(
                        f"LS_RATIO: {question[:35]} | global={_ls['global_ratio']:.2f} "
                        f"top={_ls['top_ratio']:.2f} crowd={_ls['crowd_signal']} "
                        f"smart={_ls['smart_signal']} boost={_ls['boost']:+.4f} (DISABLED)"
                    )
        except Exception as _e:
            logger.debug(f"LS_RATIO error: {_e}")

        # ── LIQUIDATION CASCADE DETECTION ────────────────────────────────────
        try:
            if sym and self.binance_feed:
                _liq = self.binance_feed.get_liquidation_signal(sym)
                if _liq and _liq.get("net_signal") != "NEUTRAL" and abs(_liq.get("boost", 0)) > 0.002:
                    # FIX: LIQUIDATION boost DISABLED — bearish bias overriding spot momentum
                    # bayesian_prob = max(0.05, min(0.95, bayesian_prob + _liq["boost"]))
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
                    # FIX: SPX_CORR boost DISABLED — bearish bias overriding spot momentum
                    # bayesian_prob = max(0.05, min(0.95, bayesian_prob + _spx["boost"]))
                    logger.info(
                        f"SPX_CORR: {question[:35]} | SPX=${_spx.get('spx_price', 0):.0f} "
                        f"chg={_spx.get('spx_change_pct', 0):+.3f}% → {_spx['signal']} "
                        f"boost={_spx['boost']:+.4f}"
                    )
        except Exception as _e:
            logger.debug(f"SPX_CORR error: {_e}")

        # ── FEAR & GREED INDEX ───────────────────────────────────────────────
        # FnG artık momentum-following: extreme fear = bearish, extreme greed = bullish.
        # FNG ENGINE DEVRE DIŞI — NO bias yaratıyordu, YES %70 WR vs NO %35 WR

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
                    # FIX: MULTI_EX boost DISABLED — persistent bearish orderflow bias
                    # _enhanced_total_boost += _mex["boost"]
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
                    # FIX: OPTIONS boost DISABLED — PCR bearish bias was overriding spot momentum
                    # _enhanced_total_boost += _opt["boost"]
                    logger.info(
                        f"OPTIONS: {question[:35]} | PCR={_opt.get('pcr', 0):.2f} "
                        f"IV={_opt.get('iv', 0):.0f}% → {_opt['signal']} "
                        f"boost={_opt['boost']:+.4f} (DISABLED)"
                    )
            except Exception as _e:
                logger.debug(f"OPTIONS error: {_e}")

            try:
                _whale = _enh.get_whale_signal(sym) if sym else {}
                if _whale.get("active"):
                    # FIX: WHALE boost DISABLED — removing all external boosts
                    # _enhanced_total_boost += 0.005
                    logger.info(f"WHALE: {question[:35]} | active=True +0.005")
            except Exception as _e:
                logger.debug(f"WHALE error: {_e}")

            try:
                _soc = _enh.get_social_signal(sym) if sym else {}
                if _soc.get("signal") != "NEUTRAL" and abs(_soc.get("boost", 0)) > 0.003:
                    # FIX: SOCIAL boost DISABLED — removing all external boosts
                    # _enhanced_total_boost += _soc["boost"]
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

        # ── AGGREGATE BOOST CAP ──────────────────────────────────────────
        # Allow external boosts (whale, smart trader, orderflow) to modify probability
        # but cap total boost at ±0.04 to prevent runaway.
        # Baseline is `_prob_before_external_boosts` (pre-SmartMoney/TopTrader/
        # OB_Depth/KalshiArb), not `_pre_boost_prob` — the latter is captured
        # after those four already applied, which would make this cap a no-op
        # for their combined effect (see comment above its definition).
        _total_external_boost = bayesian_prob - _prob_before_external_boosts
        if abs(_total_external_boost) > _TOTAL_BOOST_CAP:
            _clamped = max(-_TOTAL_BOOST_CAP, min(_TOTAL_BOOST_CAP, _total_external_boost))
            bayesian_prob = _prob_before_external_boosts + _clamped
            logger.info(
                f"BOOST_CAP: {question[:35]} | total_boost={_total_external_boost:+.4f} "
                f"capped to {_clamped:+.4f} → prob={bayesian_prob:.3f}"
            )

        # ── Yön kararı: YES mi NO mu? (FORENSIC DIAGNOSTICS) ────────────────
        # REGIME DOUBLE-COUNT FIX: leader_bias zaten Bayesian estimator içinde
        # kullanılıyor (bayesian.py:129, %5 ağırlıkla). Burada tekrar regime boost
        # eklemek double-count'a yol açıyordu (BULLISH'te YES 2x şişiyordu).
        # Artık sadece NEUTRAL pull var — BULLISH/BEARISH regime boost kaldırıldı.
        _regime_name_for_edge = self._current_regime.get("regime", "NEUTRAL")
        _regime_str_for_edge = self._current_regime.get("strength", 0.0)

        if _regime_name_for_edge == "NEUTRAL":
            # FIX-15: REGIME DOUBLE-COUNT IN NEUTRAL — lighter pull (5% not 10%)
            # NEUTRAL: Bayesian ~0.52 + cheap NO = fake NO edge.
            # Pull probability slightly toward 0.50 to reduce directional noise.
            # leader_bias already applied in Bayesian (5% weight) — don't double-count.
            # 5% pull: 0.52 → 0.519, 0.55 → 0.5475, 0.60 → 0.595
            _neutral_pull = 0.05  # was 0.10 (too aggressive, double-counted leader_bias)
            adjusted_bayesian = bayesian_prob * (1 - _neutral_pull) + 0.50 * _neutral_pull
        else:
            # BULLISH/BEARISH: no extra boost — leader_bias already in Bayesian (5% weight)
            adjusted_bayesian = bayesian_prob

        adjusted_bayesian = max(0.05, min(0.95, adjusted_bayesian))

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
            # FIX-3: NO SYNTHETIC PRICING FIX — add spread estimate to avoid inflation
            no_price_ask = round(1.0 - yes_price + 0.02, 4)  # was 1.0 - yes_price (too optimistic)
            no_price_source = NoPriceSource.SYNTHETIC if not no_book_fetched else NoPriceSource.MISSING

        # DIRECT PRICE EDGE CALCULATION
        # Edge = model_prob - actual_price_you_pay - costs
        # This is the TRUE expected profit per dollar.
        #
        # VIG-AWARE was WRONG for directional bets:
        #   implied_yes = 0.59/1.27 = 0.465 → edge = 0.592-0.465 = 0.127 (FAKE)
        #   But you PAY 0.59, not 0.465! Real EV = 0.592*1.0 - 0.59 = +0.002
        #   Vig-aware inflated edges in overpriced markets → systematic LOSS.
        #
        # Log vig info for monitoring only:
        _vig_sum = yes_price + no_price_ask
        _vig_pct = (_vig_sum - 1.0) * 100 if _vig_sum > 0 else 0

        no_prob  = 1.0 - adjusted_bayesian

        cost_yes = self.edge_model.total_cost(yes_price)
        cost_no = self.edge_model.total_cost(no_price_ask)
        yes_edge_raw = adjusted_bayesian - yes_price
        no_edge_raw = no_prob - no_price_ask
        yes_edge = yes_edge_raw - cost_yes
        no_edge = no_edge_raw - cost_no

        # FIX-3 part 2: If NO price is synthetic, apply penalty to min_edge downstream
        # (marked in trade diagnostics, checked in effective_min_edge calculation)

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

            # YES safeguard thresholds
            # Data: BNB@0.61→LOSS, HYPE@0.61→LOSS. Entry>0.55 = risk/reward bozuk
            # YES@0.55: win=$0.45, lose=$0.55. YES@0.61: win=$0.39, lose=$0.61
            _YES_MAX_PRICE = 0.47      # TIGHT: 0.48+ = yazı-tura, WR=%48 ile -EV
            _YES_MIN_PRICE = 0.15      # too cheap = market says NO strongly

            # Determine best direction by edge
            # Edge MUST be positive — EV-negatif trade'e girme
            _yes_viable = (yes_edge > 0
                           and yes_price <= _YES_MAX_PRICE
                           and yes_price >= _YES_MIN_PRICE)
            # CRITICAL FIX: NO trades have a 37% win rate due to 5m mean reversion.
            # Require an extreme edge (> 0.15) to even consider a NO trade,
            # effectively disabling most NO trades to stop the bleeding.
            # BUG: `_spot_bearish_for_no` (the regime-adaptive "5m spot must
            # not be rising" safeguard computed just above) was never
            # referenced here, so it never actually gated anything — NO
            # could be selected while spot was rising well past the
            # regime-adaptive threshold, directly against the momentum-first
            # strategy this module documents. Same "computed but never
            # read" bug class as `momentum_decelerating` (OPT-3, see
            # tests/test_opt3_momentum_decel_gate.py).
            _no_viable = (no_edge > 0.15
                          and no_price_ask >= _NO_MIN_ASK
                          and no_price_source == NoPriceSource.REAL_BOOK
                          and no_side_health == "OK"
                          and _spot_bearish_for_no)

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

            # ── 5M VOLUME SPIKE CONTRARIAN (56.8% fakeout rate) ──
            # Quant: 5m bullish candle + volume > 2x avg = stop-hunt / FOMO trap
            if timeframe == "5m" and volume_ratio > 2.0 and change_pct > 0:
                _pre_spike = _pattern_score
                _pattern_score = max(_pattern_score - 0.4, -1.0)
                logger.info(
                    f"5M_VOL_SPIKE_CONTRARIAN: {question[:35]} | "
                    f"vol={volume_ratio:.1f}x chg={change_pct:+.2f}% → "
                    f"score {_pre_spike:+.2f}→{_pattern_score:+.2f}"
                )

            # ── 15M PATTERN DAMPENING (45% breakout WR = retail trap) ──
            # Quant: 15m candle breakouts are the biggest retail trap
            # FIX-2c: REMOVED 15M_PATTERN_DAMPEN — Bayesian already dampens 15m (0.90/0.75)
            # Double-dampening made 15m signals too pessimistic (50% WR → ~35% effective)
            # if timeframe == "15m" and abs(_pattern_score) > 0.1:
            #     _pattern_score *= 0.5

            if patterns:
                logger.info(
                    f"CANDLE_PATTERNS: {question[:35]} | {patterns} "
                    f"score={_pattern_score:+.2f} bull={len(_bullish_patterns)} bear={len(_bearish_patterns)}"
                )

            # ── PATTERN-DRIVEN YES ACTIVATION ──
            # Need 2+ confirmations: strong pattern score alone OR pattern + consecutive green.
            # Single candle alone is not enough — prevents 1-candle bounce traps.
            _has_strong_bullish_pattern = any(
                p in patterns for p in ("BULLISH_MARUBOZU", "BULLISH_ENGULFING",
                                         "HAMMER", "THREE_WHITE_SOLDIERS", "MORNING_STAR")
            )
            _pattern_bullish = (
                _pattern_score >= 0.6  # very strong pattern score (was 0.4)
                or (_has_strong_bullish_pattern and consecutive_bullish >= 2)  # pattern + 2 green (was 1)
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
            # Bounce requires 2+ confirmations to activate (not just 1 candle)
            # FIX-9: BOUNCE LOGIC CONSISTENCY — 3rd clause requires 2 consecutive bullish per comment
            _bounce_active = (consecutive_bullish >= 2
                              or (bounce_signal and consecutive_bullish >= 2)  # was >=1
                              or (_pattern_bullish and consecutive_bullish >= 2))  # pattern + 2 green (not 1)
            _bounce_faded = ((consecutive_bearish >= 2 and consecutive_bullish == 0)
                             or _pattern_bearish)

            # ── 4H VOL DIP BOUNCE BOOST ──
            # Quant: 4h capitulation volume + red candle → 56.76% bounce
            # Only activate if there's at least 1 confirming green candle
            if self._current_regime.get("4h_vol_bounce", False) and not _bounce_faded:
                if not _bounce_active and consecutive_bullish >= 1:
                    _bounce_active = True
                    logger.info(
                        f"4H_VOL_BOUNCE_ACTIVATE: {question[:35]} | "
                        f"4h capitulation + {consecutive_bullish} green → bounce_active=True"
                    )

            # YES activation: bullish candle patterns can activate YES direction
            # but edge must be realistic (based on Bayesian prob, not payout)
            #
            # GATE 1: FnG gate DEVRE DIŞI — makro gösterge 5dk momentum'u bloklamak için uygun değil
            # GATE 2: 3+ green candles → expect red (mean reversion), block ALL YES paths
            # FIX-10: 2GREEN_YES_BLOCK — too strict, relax to 3+ for block, reduce Kelly for 2
            _green_exhaustion = consecutive_bullish >= 3

            # 3+ green candles = trend devam ediyor, YES hala viable
            # AMA aynı zamanda NO için exhaustion sinyali — NO_MIN_ASK düşür
            if _green_exhaustion:
                # YES engellenmez — trend devam edebilir
                # NO tarafı güçlendir: 3+ yeşil = pullback olasılığı artar
                # FIX: ADX>25 = güçlü trend devam ediyor, exhaustion reversal riskli → NO block
                if no_edge > 0 and no_price_source == NoPriceSource.REAL_BOOK and no_side_health == "OK":
                    if not _no_viable and no_price_ask >= 0.05:
                        if adx > 25:
                            logger.info(
                                f"3GREEN_NO_BLOCK: {question[:35]} | "
                                f"{consecutive_bullish} green candles + ADX={adx:.0f}>25 → "
                                f"strong trend, NO blocked (not exhaustion)"
                            )
                        else:
                            _no_viable = True
                            logger.info(
                                f"3GREEN_NO_ACTIVATE: {question[:35]} | "
                                f"{consecutive_bullish} green candles → NO activated "
                                f"(exhaustion reversal, no_ask={no_price_ask:.2f}, no_edge={no_edge:.4f})"
                            )
                if _yes_viable:
                    logger.info(
                        f"3GREEN_YES_ALLOW: {question[:35]} | "
                        f"{consecutive_bullish} green candles → YES still viable (trend continuation)"
                    )

            if consecutive_bullish == 2:
                if _yes_viable:
                    logger.info(
                        f"2GREEN_YES_OK: {question[:35]} | "
                        f"{consecutive_bullish} green candles → YES viable (trend building)"
                    )

            if _bounce_active or _pattern_bullish:
                # Use cost-adjusted edge only — no raw bayesian_prob - yes_price bypass
                _bounce_yes_edge = yes_edge  # already cost-adjusted (line 859)
                # Pattern bonus: strong pattern adds small edge boost
                if _pattern_score >= 0.6:
                    _bounce_yes_edge += 0.015  # strong pattern bonus (was 0.02, originally 0.05)
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

            # BOUNCE_NO_BLOCK kaldırıldı — sinyal neyse o
            if False and _no_viable and _bounce_active and not _bounce_faded:
                _no_viable = False
                logger.info(
                    f"BOUNCE_NO_BLOCK: {question[:35]} -- "
                    f"{consecutive_bullish} green / patterns={_bullish_patterns}, NO blocked"
                )

            # ── BULLISH EXHAUSTION → NO ACTIVATE ──────────────────────────
            # FIX-11: EXHAUSTION NO ACTIVATE — RELAX CONDITIONS (volume_ratio optional)
            # 2-3+ ardışık güçlü yeşil mum = piyasa aşırı yükseldi → pullback.
            # BOUNCE_NO_BLOCK'u override eder — tükenme = NO için doğru an.
            # Koşullar: gerçek NO orderbook + sağlıklı + NO ask < 80¢ + yeterli edge.
            # Volume boost optional (not required) — vol_ratio > 1.2 → boost, < 1.2 → still allow
            if (not _no_viable and bullish_exhaustion
                    and no_edge > 0
                    and consecutive_bullish >= 2):
                _exhaustion_vol_ok = volume_ratio > 1.2  # optional boost
                if (no_price_source == NoPriceSource.REAL_BOOK
                        and no_side_health == "OK"
                        and no_price_ask >= _NO_MIN_ASK
                        and no_price_ask < 0.80):
                    _no_viable = True
                    _vol_note = f"vol={volume_ratio:.1f}x" if _exhaustion_vol_ok else f"vol={volume_ratio:.1f}x (no boost)"
                    logger.info(
                        f"EXHAUSTION_NO_ACTIVATE: {question[:35]} | "
                        f"{consecutive_bullish} green, "
                        f"cum_move=+{bullish_exhaustion_magnitude:.2f}% | "
                        f"{_vol_note} NO_edge={no_edge:.4f} → "
                        f"NO activated (overextended)"
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

            # YES_SAFEGUARD + NO_SAFEGUARD kaldırıldı — sinyal neyse o

            # NO_REGIME_GUARD kaldırıldı — sinyal neyse o, guard engellemesin

            # LEADER_BLOCK kaldırıldı — sinyal neyse o

            # ══════════════════════════════════════════════════════════════
            # COMPOSITE TECH SCORE GATE
            # tech_score aggregates ALL indicators (-1 to +1).
            # If tech_score strongly disagrees with direction → block the trade.
            # This prevents trading against the full technical picture.
            # ══════════════════════════════════════════════════════════════
            # FIX-14: TECH SCORE THRESHOLD CONSISTENCY — log trigger matches block trigger
            if abs(tech_score) >= 0.4:
                logger.info(
                    f"TECH_SCORE: {question[:35]} score={tech_score:+.3f} "
                    f"adx={adx:.0f} obv={obv_slope:+.3f} cmf={cmf:+.3f}"
                )
                # TECH_BLOCK kaldırıldı — sinyal neyse o
                pass

            # ADX_RANGE_BLOCK kaldırıldı — sinyal neyse o

            # VOL_FLOW_BLOCK kaldırıldı — sinyal neyse o

            # ── REALTIME LAG PREVENTION ──────────────────────────────────
            # Bayesian uses past candles → lags when trend reverses.
            # Check last 60s actual price movement. If it contradicts the
            # signal direction, block the trade to avoid chasing stale momentum.
            _rt_change = 0.0
            if sym and self.binance_feed is not None:
                _rt_change = self.binance_feed.get_recent_change(sym, seconds=60)
            _RT_THRESHOLD = 0.15  # 0.15% = meaningful move in 60s (was 0.05 — too sensitive, blocked noise)
            if _yes_viable and _rt_change < -_RT_THRESHOLD:
                # YES signal but price dropping in last 60s → trend reversing
                _yes_viable = False
                logger.info(
                    f"RT_LAG_BLOCK_YES: {question[:35]} | "
                    f"YES signal but 60s change={_rt_change:+.3f}% (dropping) → blocked"
                )
            if _no_viable and _rt_change > _RT_THRESHOLD:
                # NO signal but price rising in last 60s → trend reversing
                _no_viable = False
                logger.info(
                    f"RT_LAG_BLOCK_NO: {question[:35]} | "
                    f"NO signal but 60s change={_rt_change:+.3f}% (rising) → blocked"
                )

            # ── 5m vs 4h CONFLICT GUARD ──────────────────────────────────
            # When 5m is bearish but 4h is bullish → DON'T buy YES
            # When 5m is bullish but 4h is bearish → DON'T buy NO
            # 5-minute markets resolve on 5m price, not 4h trend!
            _btc_5m = self._current_regime.get("btc_5m_pct", 0.0)
            _eth_5m = self._current_regime.get("eth_5m_pct", 0.0)
            _btc_4h = self._current_regime.get("btc_4h_pct", 0.0)
            _eth_4h = self._current_regime.get("eth_4h_pct", 0.0)
            _avg_5m = (_btc_5m + _eth_5m) / 2
            _avg_4h = (_btc_4h + _eth_4h) / 2

            if _avg_5m < -0.10 and _avg_4h > 0.30:
                # 5m bearish + 4h bullish = mean reversion → flip to NO
                if _yes_viable:
                    _yes_viable = False
                # Same price/data-quality gate every other NO-activation path
                # in this function enforces (e.g. EXHAUSTION_NO_ACTIVATE) —
                # without it this block could flip NO on to a SYNTHETIC/stale
                # or UNTRADABLE book.
                if (not _no_viable and no_edge > 0
                        and no_price_source == NoPriceSource.REAL_BOOK
                        and no_side_health == "OK"
                        and no_price_ask >= _NO_MIN_ASK):
                    _no_viable = True
                    logger.info(
                        f"TF_CONFLICT_FLIP_NO: {question[:35]} | "
                        f"5m={_avg_5m:+.2f}% (bearish) vs 4h={_avg_4h:+.2f}% (bullish) → NO bias"
                    )
                elif not _no_viable:
                    logger.info(
                        f"TF_CONFLICT_NO_BLOCKED: {question[:35]} | "
                        f"5m={_avg_5m:+.2f}% vs 4h={_avg_4h:+.2f}% conflict, but NO fails "
                        f"price/quality gate (ask={no_price_ask:.2f}, "
                        f"source={no_price_source}, health={no_side_health})"
                    )
            if _avg_5m > 0.10 and _avg_4h < -0.30:
                # 5m bullish + 4h bearish = mean reversion → flip to YES
                if _no_viable:
                    _no_viable = False
                # Same YES price-ceiling gate every other YES-activation path
                # in this function enforces (e.g. CANDLE_YES_ACTIVATE) —
                # without it this block could flip YES on above _YES_MAX_PRICE,
                # the exact "YES@0.61: win=$0.39, lose=$0.61" risk/reward the
                # cap exists to prevent.
                if (not _yes_viable and yes_edge > 0
                        and yes_price <= _YES_MAX_PRICE
                        and yes_price >= _YES_MIN_PRICE):
                    _yes_viable = True
                    logger.info(
                        f"TF_CONFLICT_FLIP_YES: {question[:35]} | "
                        f"5m={_avg_5m:+.2f}% (bullish) vs 4h={_avg_4h:+.2f}% (bearish) → YES bias"
                    )
                elif not _yes_viable:
                    logger.info(
                        f"TF_CONFLICT_YES_BLOCKED: {question[:35]} | "
                        f"5m={_avg_5m:+.2f}% vs 4h={_avg_4h:+.2f}% conflict, but YES fails "
                        f"price gate (price={yes_price:.2f}, "
                        f"bounds=[{_YES_MIN_PRICE:.2f},{_YES_MAX_PRICE:.2f}])"
                    )

            # ── MACRO TREND GATE (15-candle) ─────────────────────────────
            # Dead cat bounce koruması: son 5 mum yeşil olsa bile,
            # 15 mum perspektifinde düşüş varsa YES girişi tehlikeli.
            # Aynı şekilde 15 mum yükselişte NO girişi tehlikeli.
            _MACRO_THRESHOLD = 0.35  # Raised: 0.15% was too sensitive (blocked YES in bullish 4h regime)
            if abs(macro_trend_pct) >= _MACRO_THRESHOLD:
                if macro_trend_pct < -_MACRO_THRESHOLD and _yes_viable:
                    _yes_viable = False
                    logger.info(
                        f"MACRO_TREND_BLOCK_YES: {question[:35]} | "
                        f"15-candle trend={macro_trend_pct:+.3f}% (bearish) → YES blocked"
                    )
                if macro_trend_pct > _MACRO_THRESHOLD and _no_viable:
                    _no_viable = False
                    logger.info(
                        f"MACRO_TREND_BLOCK_NO: {question[:35]} | "
                        f"15-candle trend={macro_trend_pct:+.3f}% (bullish) → NO blocked"
                    )

            # Pick the better viable direction — pure edge comparison
            if _yes_viable and _no_viable:
                if yes_edge >= no_edge:
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
            # Spot verisi yok — sadece Bayesian edge (edge MUST be positive)
            if yes_edge > 0 and yes_edge >= no_edge:
                direction   = "YES"
                trade_price = yes_price
                trade_edge  = yes_edge
                token_id    = market.get("yes_token_id", "")
            elif (no_edge > 0.15
                  and no_price_source == NoPriceSource.REAL_BOOK
                  and no_side_health == "OK"):
                direction   = "NO"
                trade_price = no_price_ask
                trade_edge  = no_edge
                token_id    = market.get("no_token_id", "")
            else:
                # Her iki edge de negatif — trade etme
                logger.debug(
                    f"NO_SPOT_NO_EDGE: {question[:40]} yes_e={yes_edge:.4f} no_e={no_edge:.4f}"
                )
                return None

        diag.selected_direction = direction

        # ══════════════════════════════════════════════════════════════
        # DATA-DRIVEN PROFITABILITY GATES (476 trade analizi)
        # YES=69% WR (+$423), NO=36% WR (-$130). Bu kurallar veri-odaklı.
        # ══════════════════════════════════════════════════════════════

        # GATE 0: ADX < 20 = yönsüz piyasa → fake breakout riski çok yüksek
        # Data: HYPE ADX=18 P=0.917 → LOSS, BNB ADX=20 tech=-0.07 → LOSS
        # ADX < 20 = no trend, 5dk window'da noise dominant.
        # trade_edge kullan — `edge` bu noktada henüz set edilmemiş (satır ~1570'te
        # ilk atanıyor), yani her zaman 404'teki 0.0 varsayılanıydı ve edge<0.25
        # her zaman True olup gate'i "genuine mispricing override"sız bir
        # `if adx < 20: return None`'a indirgiyordu.
        if adx < 20 and trade_edge < 0.25:
            logger.info(
                f"LOW_ADX_BLOCK: {question[:40]} | ADX={adx:.0f}<20 edge={trade_edge:.3f}<0.25 "
                f"→ blocked (no trend, fake breakout risk)"
            )
            return None

        # GATE 1: NO edge gate — çok düşük edge NO trade'leri engelle
        if direction == "NO" and trade_edge < 0.05:
            logger.info(
                f"NO_EDGE_GATE: {question[:40]} | NO edge={trade_edge:.3f}<0.05 "
                f"→ blocked (NO WR düşük, min 5% edge gerekli)"
            )
            # YES'e geçmeyi dene
            if _yes_viable and yes_edge > 0.03:
                direction, trade_price, trade_edge = "YES", yes_price, yes_edge
                token_id = market.get("yes_token_id", "")
                logger.info(f"NO→YES_FALLBACK: {question[:40]} | YES edge={yes_edge:.3f}")
            else:
                return None

        # GATE 2: Cheap entry block — REMOVED
        # 5min crypto up/down markets have YES prices 0.40-0.60 normally.
        # Blocking < 0.45 eliminated half the tradeable universe.
        # Edge calculation already accounts for price via Kelly (low price = high payout).
        if trade_price < 0.20:
            logger.info(
                f"EXTREME_CHEAP_BLOCK: {question[:40]} | {direction}@{trade_price:.3f} < 0.20 → blocked"
            )
            return None

        # GATE 3: Kötü saatler (21-00 ET) → block (10-37% WR)
        _hour_et_gate = _get_current_et_hour()
        _BAD_HOURS = {21, 22, 23, 0}  # 21:xx=10% WR, 22:xx=36%, 23:xx=37%, 00:xx=33%
        if _hour_et_gate in _BAD_HOURS:
            logger.info(
                f"BAD_HOUR_BLOCK: {question[:40]} | hour={_hour_et_gate}:xx ET → blocked "
                f"(data: WR=10-37% in these hours)"
            )
            return None

        # GATE4_NO_PENALTY kaldırıldı — sinyal neyse o.
        # Bu bayrak "NO edges less reliable (data: NO 18% WR)" varsayımına
        # dayanıyordu ve Factor 2'de (aşağıda) her NO trade'de koşulsuz
        # -0.20 Kelly-boyut cezası uyguluyordu — YES tarafında hiçbir eşdeğeri
        # yok (Factor 3 sadece 3+ yeşil mumda ve sadece -0.10). Dosyadaki
        # diğer tüm tek-taraflı NO blokları (COINFLIP_BLOCK, ZONE_ADAPT,
        # REGIME_STR_CAP, BOUNCE_NO_BLOCK, ...) aynı gerekçeyle zaten
        # kaldırılmış; bu sadece "Single confidence multiplier" refactor'ünde
        # (yorum: "Old system... NO-dir(0.5)") gözden kaçmış. data/3day_eval.txt
        # (son 44 gerçek trade) varsayımın artık tersini gösteriyor: NO %55.6
        # WR / +$15.40 PnL, YES %47.1 WR / -$14.39 PnL — NO'yu sistematik
        # olarak küçültmek kazandığı trade'lerin büyümesini bastırıp net PnL'i
        # YES'in daha büyük kayıplarının domine etmesine bırakıyordu.

        # COINFLIP_BLOCK kaldırıldı — sinyal neyse o

        # ZONE_ADAPT kaldırıldı — sinyal neyse o, zone multiplier engellemesin
        _zone_edge_mult = 1.0

        # REGIME_STR_CAP + REGIME_DECAY kaldırıldı — sinyal neyse o
        _regime_kelly_multiplier = 1.0

        # OPT-3: Momentum Deceleration Guard (CLAUDE.md v9 spec, bkz. docstring).
        if _momentum_decel_blocks_no(direction, momentum_decelerating):
            logger.info(
                f"MOMENTUM_DECEL_BLOCK: {question[:40]} | NO blocked — son 3 mumda "
                f"|change| azalıyor (bounce riski, OPT-3)"
            )
            return None

        # OPT4_BLOCK kaldırıldı — sinyal neyse o

        # ── MAX YES PRICE CAP — REMOVED (v3) ─────────────────────────────
        # Data: YES@0.65+ has +8% edge over market (77.8% WR, market implies 70%).
        # The zone-adaptive system (v3) already handles this via _zone_edge_mult=0.7
        # which EASES the threshold for high-confidence trades. Don't block them.

        # Note: trade_edge already includes cost adjustment (applied at lines ~820-830)
        # Single market arb (rare: YES + NO < 1)
        # BUG (56th daily review): this used the crude `no_price` proxy from
        # line 435 (1 - yes_bid_price, computed before the real NO orderbook
        # was even read) instead of `no_price_ask` (the real orderbook ask,
        # or the FIX-3 synthetic with spread padding) computed above. Since
        # 1-yes_bid is always >= the true no_price_ask (bid <= ask), this
        # made single_edge = 1-(yes_price+no_price)-cost permanently <= 0,
        # so genuine single-market arbitrage (real YES_ask + real NO_ask < 1,
        # a guaranteed-profit trade) could never be detected or sized.
        single_edge = self.edge_model.single_market_edge(yes_price, no_price_ask)

        # BUG: `edge = max(trade_edge, single_edge)` fed single_edge straight
        # into Kelly sizing / the min-edge gate for a ONE-SIDED position.
        # single_market_edge() = 1-(yes_price+no_price_ask)-cost is only a
        # real, riskless edge when BOTH sides are bought (see its own
        # docstring: "If YES + NO < 1 ... buy both sides"). This engine only
        # ever places a single order for `direction` (one token_id) — there
        # is no code path anywhere that buys both tokens. Both sides being
        # underpriced in combination says nothing about how underpriced
        # *this* side is individually (e.g. bayesian_prob=0.50,
        # yes_price=no_price_ask=0.46: single_edge≈0.08-cost but the real
        # one-sided edge is only ≈0.04-cost). Using single_edge here let a
        # one-sided bet silently clear kelly.position_size()'s `p = price +
        # edge` and OPT-5's effective_min_edge gate on an edge that was
        # never actually available to the trade being placed, oversizing
        # (and sometimes only enabling) it. This was inert before the 56th
        # daily review (single_edge was fed a proxy that made it always <=
        # 0, so max() always picked trade_edge) but became live once that
        # fix let single_edge be genuinely positive. Keep it as a diagnostic
        # only, until/unless a real two-sided execution path exists.
        if single_edge > trade_edge:
            logger.debug(
                f"SINGLE_ARB_INFO: {question[:40]} | single_edge={single_edge:.4f} > "
                f"trade_edge={trade_edge:.4f}, but only {direction} is actually "
                f"bought here — single_edge requires buying both sides to be "
                f"real, so it is not used for sizing/gating this one-sided trade."
            )

        # Net edge: cost düşüldükten sonra pozitif olmalı — EV-negatif trade açmayız
        edge = trade_edge

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
        # OPT-5: Adaptive — regime addon with different logic:
        # - When MATCHING regime direction (BULLISH→YES, BEARISH→NO): increase min_edge
        #   based on regime strength (research: strong regime = higher bounce risk)
        # - When COUNTER-regime: also penalize
        _regime_str = self._current_regime.get("strength", 0.0)
        _base_edge = self.min_edge_yes if direction == "YES" else self.min_edge_no
        _base_edge += _coin_edge_addon  # apply coin penalty

        _follows_regime = (
            (direction == "YES" and _regime_name_for_edge == "BULLISH") or
            (direction == "NO" and _regime_name_for_edge == "BEARISH")
        )
        _is_neutral = _regime_name_for_edge == "NEUTRAL"

        if _is_neutral:
            _regime_addon = 0.0  # NEUTRAL → no addon
        elif _follows_regime:
            # MATCHING regime direction: higher regime strength = higher bounce risk
            # Formula: addon = regime_strength × 0.08 (research: str=0.75 → addon=0.06)
            _regime_addon = _regime_str * 0.08
            logger.debug(
                f"REGIME_MATCHING_ADDON: {direction} in {_regime_name_for_edge} regime "
                f"(str={_regime_str:.2f}) → addon={_regime_addon:.3f}"
            )
        else:
            # COUNTER-regime direction: penalize more
            _regime_addon = _regime_str * 0.10  # Counter-regime → daha yüksek threshold
            logger.debug(
                f"REGIME_COUNTER_ADDON: {direction} against {_regime_name_for_edge} regime "
                f"(str={_regime_str:.2f}) → addon={_regime_addon:.3f}"
            )

        effective_min_edge = (_base_edge + _regime_addon) * _zone_edge_mult

        # OPT-5: adaptive min-edge gate (CLAUDE.md kural 5 — min edge eşiği).
        # Bu kontrol daha önce kaldırılmıştı (bkz. git blame 9b5fd52); CLAUDE.md'nin
        # "Temel Kurallar (Değiştirme)" bölümü ve v9 OPT-5 spesifikasyonu bu eşiğin
        # aktif olmasını gerektiriyor, o yüzden geri eklendi.
        if edge < effective_min_edge:
            logger.info(
                f"EDGE_REJECT: {question[:40]} | {direction} edge={edge:.3f} < "
                f"effective_min_edge={effective_min_edge:.3f} (base={_base_edge:.3f} "
                f"regime_addon={_regime_addon:.3f})"
            )
            return None

        # ── SYNTHETIC NO PRICE PENALTY ─────────────────────────────────────
        # SYNTHETIC_NO_PENALTY, REGIME_NO_GATE, NEUTRAL_NO_GATE, OPT6_COOLDOWN,
        # CAUTIOUS_HOUR — hepsi kaldırıldı, sinyal neyse o
        _cautious_hour_active = False

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
        # Pass regime_strength for regime-aware Kelly fraction adjustment
        size = self.kelly.position_size(
            edge=edge,
            price=trade_price,
            capital=capital,
            signal_strength=bayes.signal_strength,
            regime_strength=_regime_str,
        )
        # ── SINGLE CONFIDENCE MULTIPLIER (replaces 4x sequential halvings) ──
        # Old system: micro(0.5) × flat-NO(0.5) × NO-dir(0.5) × 2green(0.5) = 0.0625x
        # This created a death spiral where Kelly floor ($3) was always hit.
        # New: compute ONE multiplier from 0.50 to 1.0, apply once.
        _confidence_mult = 1.0
        _confidence_reasons = []

        # Factor 1: Spot move magnitude (weak move = less confidence)
        if abs(change_pct) < 0.10:
            _confidence_mult -= 0.15
            _confidence_reasons.append(f"micro-move({abs(change_pct):.3f}%)")

        # FACTOR2_NO_PENALTY kaldırıldı — sinyal neyse o (bkz. GATE4_NO_PENALTY yorumu yukarıda)

        # Factor 3: 2+ green candles for YES (pullback risk)
        if direction == "YES" and consecutive_bullish >= 3:
            _confidence_mult -= 0.10
            _confidence_reasons.append(f"{consecutive_bullish}green")

        # Factor 4: Regime strength
        if _regime_kelly_multiplier < 1.0:
            _confidence_mult -= (1.0 - _regime_kelly_multiplier) * 0.5
            _confidence_reasons.append(f"regime({_regime_kelly_multiplier:.2f})")

        # Floor at 0.50 — never reduce more than 50%
        _confidence_mult = max(0.50, _confidence_mult)

        if _confidence_mult < 1.0:
            original_conf = size
            size = size * _confidence_mult
            logger.info(
                f"CONFIDENCE_MULT: {question[:40]} | {'+'.join(_confidence_reasons)} "
                f"→ ×{_confidence_mult:.2f} ${original_conf:.2f}→${size:.2f}"
            )
        # FIX-4b: KELLY COMPOUNDING FLOOR — prevent triple reduction from creating micro-bets
        # Streak(0.70) × 2green(0.5) × regime(0.5) = 0.175x → bet too small for CLOB
        # FIX: edge < 0.03 → Kelly "girme" diyor, floor zorlamaz
        _KELLY_MIN_BET = 3.0  # minimum bet $3
        if 0 < size < _KELLY_MIN_BET:
            if edge < 0.03:
                logger.info(
                    f"KELLY_FLOOR_SKIP: {question[:40]} size=${size:.2f} edge={edge:.3f} < 0.03 — "
                    f"Kelly says don't trade, respecting signal"
                )
                return None
            logger.info(
                f"KELLY_FLOOR: {question[:40]} size=${size:.2f} < ${_KELLY_MIN_BET} min — "
                f"raised to ${_KELLY_MIN_BET} (compounding floor)"
            )
            size = _KELLY_MIN_BET

        # ── MAX BET CAP ──────────────────────────────────────────────────
        _MAX_BET = 4.0
        if size > _MAX_BET:
            logger.info(f"MAX_BET_CAP: {question[:40]} ${size:.2f} → ${_MAX_BET:.2f}")
            size = _MAX_BET

        # FNG_YES_BLOCK kaldırıldı — sinyal neyse o

        if size <= 0 or capital < size:
            logger.debug(f"KELLY_REJECT: {question[:40]} {direction} edge={edge:.4f} size=${size:.2f} cap=${capital:.2f}")
            return None

        # ── ML QUALITY SCORE ──────────────────────────────────────────────
        _asset_short = _detect_asset(question) or ""
        _asset_short = _asset_short.replace("USDT", "")
        _now_utc = datetime.now(timezone.utc)
        # FIX-13: GOLDEN HOUR DST FIX — use proper timezone conversion
        try:
            from zoneinfo import ZoneInfo
            _now_et = _now_utc.astimezone(ZoneInfo("America/New_York"))
            _hour_et = _now_et.hour
            _minute_et = _now_et.minute
        except Exception:
            # Fallback to crude -4 offset if ZoneInfo fails
            _hour_et = (_now_utc.hour - 4) % 24
            _minute_et = _now_utc.minute
        ml_score = self.ml.predict({
            "asset": _asset_short, "direction": direction,
            "entry_price": trade_price, "edge": edge,
            "hour_et": _hour_et, "minute_et": _minute_et,
            "window_minutes": _ml_window_minutes(timeframe),
        })
        # ML gate: strong LOSS prediction → reduce size by 50%
        # FIX: thin liquidity (OB_ILLIQUID or ADX<15) → ML boost capped at 50% confidence
        _thin_liquidity = (
            (_ob_analysis is not None and not _ob_analysis.tradeable) or
            adx < 15
        )
        if _thin_liquidity and ml_score > 0.5:
            ml_score = min(ml_score, 0.5)
            logger.info(f"ML_THIN_LIQ_CAP: {question[:40]} ml capped to {ml_score:+.3f} (illiquid/low ADX={adx:.0f})")
        # FIX: ML has no trend data — cap confidence when macro trend contradicts direction
        # ML +0.729 on a dead cat bounce (5 green candles in 15-candle downtrend) = false conviction
        _macro_contradicts = (
            (direction == "YES" and macro_trend_pct < -0.15) or
            (direction == "NO" and macro_trend_pct > 0.15)
        )
        if _macro_contradicts and ml_score > 0.3:
            _old_ml = ml_score
            ml_score = min(ml_score, 0.3)
            logger.info(f"ML_MACRO_CAP: {question[:40]} ml={_old_ml:+.3f}→{ml_score:+.3f} (macro_trend={macro_trend_pct:+.3f}% contradicts {direction})")
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
            # Re-apply the hard cap: the boost above is documented as
            # "capped at max_bet" but multiplying after MAX_BET_CAP already
            # ran can otherwise push size past _MAX_BET (e.g. $4.00 × 1.30 =
            # $5.20), a real oversized live position.
            if size > _MAX_BET:
                logger.info(f"MAX_BET_CAP_POST_BOOST: {question[:40]} ${size:.2f} → ${_MAX_BET:.2f}")
                size = _MAX_BET
        except Exception:
            pass

        # ── FIX: SMART TRADER CONFLICT GATE ─────────────────────────────────
        # Smart money BULLISH → NO trade risky, smart BEARISH → YES trade risky
        # Low edge + smart conflict = skip (edge > 0.05 overrides)
        _smart_conflict = (
            (direction == "NO" and _smart_signal_dir == "BULLISH") or
            (direction == "YES" and _smart_signal_dir == "BEARISH")
        )
        if _smart_conflict and edge < 0.05:
            logger.info(
                f"SMART_CONFLICT_BLOCK: {question[:40]} | {direction} vs smart={_smart_signal_dir} "
                f"edge={edge:.3f}<0.05 → blocked (trading against smart money)"
            )
            return None

        reasoning = (
            f"[{data_source}] {direction} Bayesian={bayesian_prob:.3f} vs YES={yes_price:.3f} | "
            f"Edge={edge:.3f} | chg={change_pct:+.2f}% RSI={rsi:.0f} "
            f"vol={volume_ratio:.1f}x OB={ob_imbalance:+.2f} | Z={z_score:.1f} | "
            f"no_src={no_price_source.value} no_ask={no_price_ask:.3f}"
        )
        logger.info(
            f"ARB [{signal_type}/{timeframe}]: {question[:50]} | "
            f"{direction}@{trade_price:.3f} P={bayesian_prob:.3f} Edge={edge:.3f} ${size:.2f} "
            f"tech={tech_score:+.2f} adx={adx:.0f} ml={ml_score:+.3f} macro={macro_trend_pct:+.3f}%"
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
