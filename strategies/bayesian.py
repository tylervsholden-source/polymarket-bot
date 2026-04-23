"""
Bayesian probability estimator for crypto up/down markets.

MOMENTUM-FIRST approach (v2 — Mart 2026):
  5dk crypto marketlerinde contrarian sinyaller (RSI reversal, S/R bounce,
  Fibonacci) momentum'u cancel ediyordu → prob ~0.50 → sahte edge → %25 WR.

  Yeni strateji: Momentum takip et, contrarian kullanma.
  Tek gerçek edge = Polymarket'in spot hareketi fiyatlamadaki 30-120sn gecikmesi.

  Sinyal hiyerarşisi:
    1. Spot momentum (change_pct) — son 1 mum, %20 ağırlık
    2. Multi-candle trend (trend_pct) — son 4 mum trendi, %15
    3. Order book imbalance — alıcı/satıcı baskısı, %15
    4. MACD — kısa vadeli trend, %10
    5. EMA cross — orta vadeli trend teyidi, %10
    6. VWAP deviation — kurumsal yön, %10
    7. Leader bias (BTC+ETH 4h) — makro trend, %10
    8. Bollinger position — trend-following (contrarian DEĞİL), %10

  Kaldırılan sinyaller (contrarian, 5dk'da zararlı):
    - RSI overbought/oversold reversal
    - Support/Resistance bounce
    - Fibonacci retracement
    - Stochastic RSI reversal

P(H|D) = P(D|H) * P(H) / P(D)
H = "UP wins" hypothesis
D = observed spot data
"""
import math
from dataclasses import dataclass


@dataclass
class BayesianEstimate:
    probability: float      # P(H|D) — updated fair probability
    prior: float            # P(H) — bias-corrected market price used as prior
    signal_strength: float  # How strong the data signal is (0-1)
    direction: str          # "UP" | "DOWN" | "NEUTRAL"


def _longshot_bias_correction(market_price: float) -> float:
    p = market_price
    if p < 0.05:
        return p * 0.70
    elif p < 0.10:
        correction = 0.30 - (p - 0.05) * (0.15 / 0.05)
        return p * (1.0 - correction)
    elif p < 0.20:
        correction = 0.15 - (p - 0.10) * (0.10 / 0.10)
        return p * (1.0 - correction)
    elif p > 0.90:
        return min(0.95, p * 1.02)
    elif p > 0.80:
        correction = 0.02 * ((p - 0.80) / 0.10)
        return min(0.95, p * (1.0 + correction))
    else:
        return p


def _volume_multiplier(volume_ratio: float) -> float:
    if volume_ratio < 0.1:   return 0.15
    elif volume_ratio < 0.3: return 0.50
    elif volume_ratio < 0.6: return 0.75
    elif volume_ratio < 1.2: return 1.00
    elif volume_ratio < 2.0: return 1.30
    elif volume_ratio < 4.0: return 1.60
    elif volume_ratio < 8.0: return 2.00
    else:                    return 2.40


def _news_lag_multiplier(spot_change_pct: float) -> float:
    abs_change = abs(spot_change_pct)
    if abs_change < 0.20:  return 1.0    # çok küçük hareket: boost yok
    elif abs_change < 0.4: return 1.15   # hafif hareket
    elif abs_change < 0.8: return 1.35   # belirgin hareket
    elif abs_change < 1.5: return 1.60   # büyük hareket
    else:                  return 1.90   # aşırı hareket: max boost


class BayesianEstimator:
    def estimate(
        self,
        market_price: float,
        spot_change_pct: float,
        volatility: float,
        order_book_imbalance: float,
        rsi: float = 50.0,
        volume_ratio: float = 1.0,
        related_market_delta: float = 0.0,
        macd_hist: float = 0.0,
        leader_bias: float = 0.0,
        bb_pos: float = 0.5,
        bb_width: float = 0.0,
        bb_squeeze: bool = False,
        bb_breakout: float = 0.0,
        ema_cross: float = 0.0,
        atr_pct: float = 0.0,
        stoch_k: float = 50.0,
        sr_position: float = 0.5,
        vwap_dev: float = 0.0,
        ichi_signal: float = 0.0,
        ichi_tk_cross: float = 0.0,
        fib_level: float = 0.5,
        cross_exchange_boost: float = 0.0,
        regime_strength: float = 0.0,
        regime_direction: str = "NEUTRAL",
        trend_pct: float = 0.0,
    ) -> BayesianEstimate:

        # ── 1. Prior: longshot bias correction ───────────────────────────────
        prior = _longshot_bias_correction(max(0.03, min(0.97, market_price)))
        prior = max(0.05, min(0.95, prior))

        # ── 2. LAG-DETECTION MODEL ──────────────────────────────────────────
        # The ONLY reliable edge in 5min crypto binaries is Polymarket's
        # 30-120s price lag behind spot. Focus on:
        #   (a) Spot momentum — the actual price move (primary signal)
        #   (b) Order book imbalance — buying/selling pressure (secondary)
        #   (c) Trend confirmation — multi-candle direction (tertiary)
        # Lagging indicators (MACD, EMA, BB, VWAP, leader) are DEMOTED
        # because they add noise on 5-minute timeframes.

        # Spot momentum: AMPLIFIED (×5 instead of ×3) — this IS the signal
        spot_signal = math.tanh(spot_change_pct * 5.0)

        # Multi-candle trend (son 4 mum)
        trend_signal = math.tanh(trend_pct * 3.5)

        # Order book imbalance — real-time buying/selling pressure
        ob_signal = math.tanh(order_book_imbalance * 1.5)

        # VWAP deviation — institutional flow (kept, moderately useful)
        vwap_signal = math.tanh(vwap_dev * 3.0)

        # Lagging indicators — DEMOTED to 5% each (was 10%)
        macd_signal = math.tanh(macd_hist * 12.0)
        ema_signal = math.tanh(ema_cross * 6.0)
        leader_signal = math.tanh(leader_bias * 2.5)
        bb_signal = (bb_pos - 0.5) * 0.8
        if bb_breakout != 0.0:
            bb_signal = bb_breakout * (1.5 if bb_squeeze else 1.0)
        elif bb_squeeze:
            bb_signal *= 0.2

        # ── LAG-FIRST WEIGHTS (toplam = 1.0) ────────────────────────────────
        #   Spot momentum:    0.35  (THE signal — price lag detection)
        #   Order book:       0.20  (real-time pressure)
        #   Trend (4 mum):    0.15  (direction confirmation)
        #   VWAP:             0.10  (institutional)
        #   MACD:             0.05  (demoted lagging)
        #   EMA cross:        0.05  (demoted lagging)
        #   Leader:           0.05  (demoted lagging)
        #   Bollinger:        0.05  (demoted lagging)
        # Total: 0.35+0.20+0.15+0.10+0.05+0.05+0.05+0.05 = 1.00 ✓
        raw_signal = (
            spot_signal    * 0.35 +
            ob_signal      * 0.20 +
            trend_signal   * 0.15 +
            vwap_signal    * 0.10 +
            macd_signal    * 0.05 +
            ema_signal     * 0.05 +
            leader_signal  * 0.05 +
            bb_signal      * 0.05
        )
        raw_signal = max(-1.0, min(1.0, raw_signal))

        # ── 9. Cross-Exchange Lead-Lag Boost (Binance → Bitstamp) ────────────
        # Binance leads Bitstamp by 10-50ms; apply -0.03 to +0.03 boost
        raw_signal += cross_exchange_boost * 0.8

        # ── 10. Regime-Based Signal Dampening ──────────────────────────────
        # Strong regime (str > 0.70): crowd effect → counter-trend tendency
        # Weak regime: NO DAMPENING — choppy 0.7x kaldırıldı.
        # Sebebi: regime_str genelde 0.10-0.20 arası, bu her sinyali %30 bastırıyordu
        # ve V-recovery gibi gerçek momentum sinyallerini öldürüyordu.
        if regime_strength > 0.70:
            # Dampen signals aligned with regime direction (bounce risk)
            if (regime_direction in ("UP", "BULLISH") and raw_signal > 0) or \
               (regime_direction in ("DOWN", "BEARISH") and raw_signal < 0):
                raw_signal *= 0.5

        raw_signal = max(-1.0, min(1.0, raw_signal))

        # ── 11. Volume multiplier ─────────────────────────────────────────────
        vol_mult = _volume_multiplier(volume_ratio)

        # ── 12. News lag boost ───────────────────────────────────────────────
        lag_mult = _news_lag_multiplier(spot_change_pct)

        # ── 13. ATR volatility dampening ─────────────────────────────────────
        effective_vol = atr_pct / 100.0 if atr_pct > 0 else volatility
        # FIX: dampening 8.0→5.0 — trend sinyalini çok bastırıyordu
        confidence = 1.0 / (1.0 + effective_vol * 5.0)
        adjusted_signal = raw_signal * confidence * vol_mult * lag_mult

        adjusted_signal = max(-1.5, min(1.5, adjusted_signal))

        # ── 14. Bayesian update (log-odds) ───────────────────────────────────
        # Güçlendirilmiş update (2.0 vs eski 1.2) — sinyal güçlüyse
        # probability 0.50'den net olarak uzaklaşsın
        log_odds_prior = math.log(prior / (1.0 - prior))
        # Log-odds multiplier history:
        #   1.2 (original) → 2.0 (too aggressive, P=0.524 from +0.05% → false conviction)
        #   → 1.4 (overcorrection, 30% signal loss, YES WR dropped)
        #   → 1.6 (compromise: blocks weak signals, preserves strong edge)
        # Validation: +0.05% move → adjusted_signal≈0.03 → log_odds_update=0.048 → P≈0.512 (OK)
        #             +0.30% move → adjusted_signal≈0.20 → log_odds_update=0.320 → P≈0.579 (good edge)
        # With lag-detection model, spot momentum is 35% weight.
        # Amplify log-odds to move probability decisively away from 0.50.
        # +0.30% move → adjusted_signal≈0.35 → log_odds=0.77 → P≈0.68 (strong edge)
        # +0.10% move → adjusted_signal≈0.12 → log_odds=0.26 → P≈0.56 (mild edge)
        _LOG_ODDS_MULT = 2.2
        log_odds_update = adjusted_signal * _LOG_ODDS_MULT
        log_odds_post = log_odds_prior + log_odds_update
        updated_prob = 1.0 / (1.0 + math.exp(-log_odds_post))
        updated_prob = max(0.05, min(0.95, updated_prob))

        signal_strength = min(1.0, abs(adjusted_signal) / 1.5)
        direction = (
            "UP"   if adjusted_signal > 0.08 else
            "DOWN" if adjusted_signal < -0.08 else
            "NEUTRAL"
        )

        return BayesianEstimate(
            probability=round(updated_prob, 4),
            prior=round(prior, 4),
            signal_strength=round(signal_strength, 3),
            direction=direction,
        )
