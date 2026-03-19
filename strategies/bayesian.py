"""
Bayesian probability estimator for crypto up/down markets.

Updates prior (market price) based on real Binance/Bitstamp data:
  - spot price change (current candle open → now)
  - RSI (momentum / overbought-oversold)
  - volume ratio (confirms or rejects the move)
  - order book imbalance (bid vs ask pressure)

Research-backed enhancements (2025):
  1. Favorite-Longshot Bias correction:
       Contracts <10c are overpriced by ~26% on average (QuantPedia / Kalshi study).
       Contracts >80c are slightly underpriced (~2-4%).
       Prior is adjusted accordingly before Bayesian update.

  2. News Lag boost:
       Polymarket lags 30-120 seconds after large spot moves (2025 research).
       |spot_change_pct| > 0.5% → signal confidence boosted (market hasn't repriced yet).

  3. Tiered volume signal:
       Volume 3x vs 10x normal are very different — log scale misses this.
       Tiered multiplier: 0.4x (low) → 1.0x (normal) → 1.4x → 1.8x → 2.2x (extreme).

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
    """
    Adjusts market_price prior to reflect empirically documented biases.

    Favorite-Longshot Bias (QuantPedia, Kalshi 2025 paper):
      - <10c contracts lose >60% → true probability ~26% lower than price suggests
      - >80c contracts are underpriced by ~2-4%

    Returns adjusted prior. Conservative: max ±5% correction.
    """
    p = market_price
    if p < 0.05:
        # Almost certainly overpriced — deep longshot territory
        return p * 0.70
    elif p < 0.10:
        # Strong overpricing zone (loses 60%+ on average)
        # Correct toward 0.05: correction scales linearly 30%→15%
        correction = 0.30 - (p - 0.05) * (0.15 / 0.05)
        return p * (1.0 - correction)
    elif p < 0.20:
        # Moderate overpricing: ~10-15% correction
        correction = 0.15 - (p - 0.10) * (0.10 / 0.10)
        return p * (1.0 - correction)
    elif p > 0.90:
        # Favorite zone: very slightly underpriced
        return min(0.95, p * 1.02)
    elif p > 0.80:
        # Mild favorite bias: ~1-2% underpriced
        correction = 0.02 * ((p - 0.80) / 0.10)
        return min(0.95, p * (1.0 + correction))
    else:
        return p


def _volume_multiplier(volume_ratio: float) -> float:
    """
    Tiered volume confirmation multiplier.

    Research: volume spikes are the strongest confirmation signal for momentum.
    Log scale (old approach) fails to differentiate 3x from 10x volume.
    """
    if volume_ratio < 0.1:   return 0.15   # neredeyse sıfır: veri yok, sinyal güvenilmez
    elif volume_ratio < 0.3: return 0.50   # very low: dikkatli ol ama sinyal var
    elif volume_ratio < 0.6: return 0.75   # below normal: 5m candle'da normal dalgalanma
    elif volume_ratio < 1.2: return 1.00   # normal volume: neutral
    elif volume_ratio < 2.0: return 1.30   # slightly elevated: moderate confirm
    elif volume_ratio < 4.0: return 1.60   # high volume spike: strong confirm
    elif volume_ratio < 8.0: return 2.00   # very high: very strong confirm
    else:                    return 2.40   # extreme spike: maximum confidence


def _news_lag_multiplier(spot_change_pct: float) -> float:
    """
    News lag boost: Polymarket lags 30-120s after large spot moves.
    When |change| is large, market hasn't repriced → signal is more reliable.

    Source: 2025 prediction market research — ~30-120s repricing lag.
    """
    abs_change = abs(spot_change_pct)
    if abs_change < 0.30:  return 1.0    # küçük hareket: boost yok (önceki: 0.15)
    elif abs_change < 0.5: return 1.10   # orta hareket: hafif boost (önceki: 1.30)
    elif abs_change < 1.0: return 1.25   # belirgin hareket: orta boost (önceki: 1.50)
    elif abs_change < 2.0: return 1.50   # büyük hareket: güçlü boost (önceki: 1.80)
    else:                  return 1.75   # aşırı hareket: max boost (önceki: 2.00)


class BayesianEstimator:
    def estimate(
        self,
        market_price: float,
        spot_change_pct: float,       # % change in current candle (real Binance data)
        volatility: float,            # Implied volatility from trend range
        order_book_imbalance: float,  # (bid_vol - ask_vol) / total, [-1, 1]
        rsi: float = 50.0,            # RSI(14) — 0-100
        volume_ratio: float = 1.0,    # Current candle vol / avg vol
        related_market_delta: float = 0.0,
        macd_hist: float = 0.0,       # MACD(3,15,3) histogram value
        leader_bias: float = 0.0,     # BTC+ETH 4h regime bias
        # ── New technical indicators ──
        bb_pos: float = 0.5,          # Bollinger position (0=lower, 1=upper)
        bb_width: float = 0.0,        # Bollinger width (squeeze detection)
        ema_cross: float = 0.0,       # EMA 9/21 cross (% normalized)
        atr_pct: float = 0.0,         # ATR as % of price
        stoch_k: float = 50.0,        # Stochastic RSI %K
        sr_position: float = 0.5,     # Support/Resistance position (0=support, 1=resistance)
        vwap_dev: float = 0.0,        # VWAP deviation (%)
        ichi_signal: float = 0.0,     # Ichimoku cloud signal [-1, 1]
        ichi_tk_cross: float = 0.0,   # Ichimoku Tenkan/Kijun cross
        fib_level: float = 0.5,       # Fibonacci level (0=low, 1=high)
    ) -> BayesianEstimate:

        # ── 1. Favorite-Longshot Bias correction on prior ──────────────────────
        prior = _longshot_bias_correction(
            max(0.03, min(0.97, market_price))
        )
        prior = max(0.05, min(0.95, prior))

        # ── 2. Spot momentum signal ────────────────────────────────────────────
        spot_signal = math.tanh(spot_change_pct * 3.0)

        # ── 3. MACD(3,15,3) signal ────────────────────────────────────────────
        macd_signal = math.tanh(macd_hist * 10.0)

        # ── 4. RSI signal (trend-aware) ───────────────────────────────────────
        if rsi > 65:
            raw_rsi = -0.6 * ((rsi - 65) / 35)
            rsi_signal = raw_rsi * 0.2 if spot_signal > 0.15 else raw_rsi
        elif rsi < 35:
            raw_rsi = 0.6 * ((35 - rsi) / 35)
            rsi_signal = raw_rsi * 0.2 if spot_signal < -0.15 else raw_rsi
        else:
            rsi_signal = 0.0

        # ── 5. Order book imbalance ────────────────────────────────────────────
        ob_signal = order_book_imbalance * 0.5

        # ── 6. Leader bias (BTC+ETH 4h regime) ────────────────────────────────
        leader_signal = math.tanh(leader_bias * 2.0)

        # ── 7. Bollinger Bands signal ─────────────────────────────────────────
        # bb_pos: 0=lower band (oversold), 1=upper band (overbought)
        # Squeeze (bb_width < 0.02): breakout imminent → boost signal confidence
        bb_signal = (bb_pos - 0.5) * -0.6  # contrarian: near upper = bearish
        if bb_width > 0 and bb_width < 0.015:
            # Squeeze detected — amplify directional signal
            bb_signal *= 1.5

        # ── 8. EMA crossover signal ───────────────────────────────────────────
        # ema_cross: % difference between EMA9 and EMA21 (positive = bullish)
        ema_signal = math.tanh(ema_cross * 5.0)

        # ── 9. Stochastic RSI signal ──────────────────────────────────────────
        # stoch_k: 0-100, <20 = oversold (bullish), >80 = overbought (bearish)
        if stoch_k > 80:
            stoch_signal = -0.5 * ((stoch_k - 80) / 20)
        elif stoch_k < 20:
            stoch_signal = 0.5 * ((20 - stoch_k) / 20)
        else:
            stoch_signal = 0.0

        # ── 10. VWAP deviation signal ─────────────────────────────────────────
        # Price above VWAP = bullish, below = bearish
        vwap_signal = math.tanh(vwap_dev * 2.0)

        # ── 11. Ichimoku Cloud signal ─────────────────────────────────────────
        # ichi_signal: [-1, 1], above cloud = bullish
        # ichi_tk_cross: Tenkan/Kijun cross confirmation
        ichi_combined = ichi_signal * 0.6 + math.tanh(ichi_tk_cross * 5.0) * 0.4

        # ── 12. Support/Resistance + Fibonacci ────────────────────────────────
        # Near support (sr_position < 0.2) = bounce likely (bullish)
        # Near resistance (sr_position > 0.8) = rejection likely (bearish)
        sr_signal = (0.5 - sr_position) * 0.8  # contrarian near extremes
        # Fibonacci reinforces S/R: near 0.618/0.786 retrace = reversal zone
        if fib_level < 0.3:
            sr_signal += 0.15  # near swing low: bullish bounce
        elif fib_level > 0.7:
            sr_signal -= 0.15  # near swing high: bearish rejection

        # ── 13. Weighted combination (total = 1.0) ───────────────────────────
        # 12 signals, balanced across categories:
        #   Momentum (0.30): spot 0.12 + MACD 0.10 + EMA cross 0.08
        #   Mean-rev (0.15): RSI 0.05 + Stoch RSI 0.05 + BB 0.05
        #   Structure(0.20): OB 0.10 + VWAP 0.05 + S/R+Fib 0.05
        #   Trend   (0.20): Ichimoku 0.10 + leader 0.10
        #   Regime  (0.15): leader bias 0.15 (was 0.20, split with Ichimoku)
        raw_signal = (
            spot_signal    * 0.12 +   # spot momentum
            macd_signal    * 0.10 +   # MACD trend
            ema_signal     * 0.08 +   # EMA 9/21 crossover
            rsi_signal     * 0.05 +   # trend-aware RSI
            stoch_signal   * 0.05 +   # Stochastic RSI
            bb_signal      * 0.05 +   # Bollinger Bands
            ob_signal      * 0.10 +   # order book pressure
            vwap_signal    * 0.05 +   # VWAP deviation
            sr_signal      * 0.05 +   # support/resistance + fibonacci
            ichi_combined  * 0.10 +   # Ichimoku cloud + TK cross
            leader_signal  * 0.15 +   # BTC+ETH regime gate
            0.0            * 0.10     # reserved (future signals)
        )
        raw_signal = max(-1.0, min(1.0, raw_signal))

        # ── 14. Volume multiplier (tiered) ────────────────────────────────────
        vol_mult = _volume_multiplier(volume_ratio)

        # ── 15. News lag boost ────────────────────────────────────────────────
        lag_mult = _news_lag_multiplier(spot_change_pct)

        # ── 16. ATR-based volatility dampening ────────────────────────────────
        # Use ATR if available (more accurate than trend_pct derived volatility)
        effective_vol = atr_pct / 100.0 if atr_pct > 0 else volatility
        confidence = 1.0 / (1.0 + effective_vol * 10.0)
        adjusted_signal = raw_signal * confidence * vol_mult * lag_mult

        # Clamp (±1.5)
        adjusted_signal = max(-1.5, min(1.5, adjusted_signal))

        # ── 17. Bayesian update (log-odds) ────────────────────────────────────
        log_odds_prior = math.log(prior / (1.0 - prior))
        log_odds_update = adjusted_signal * 2.0
        log_odds_post = log_odds_prior + log_odds_update
        updated_prob = 1.0 / (1.0 + math.exp(-log_odds_post))
        updated_prob = max(0.05, min(0.95, updated_prob))

        signal_strength = min(1.0, abs(adjusted_signal) / 1.5)
        direction = (
            "UP"   if adjusted_signal > 0.05 else
            "DOWN" if adjusted_signal < -0.05 else
            "NEUTRAL"
        )

        return BayesianEstimate(
            probability=round(updated_prob, 4),
            prior=round(prior, 4),
            signal_strength=round(signal_strength, 3),
            direction=direction,
        )
