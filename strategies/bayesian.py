"""
Bayesian probability estimator for crypto up/down markets.

MOMENTUM-FIRST approach (v2 — Mart 2026):
  5dk crypto marketlerinde contrarian sinyaller (RSI reversal, S/R bounce,
  Fibonacci) momentum'u cancel ediyordu → prob ~0.50 → sahte edge → %25 WR.

  Yeni strateji: Momentum takip et, contrarian kullanma.
  Tek gerçek edge = Polymarket'in spot hareketi fiyatlamadaki 30-120sn gecikmesi.

  Sinyal hiyerarşisi:
    1. Spot momentum (change_pct) — ana sinyal, %25 ağırlık
    2. Order book imbalance — alıcı/satıcı baskısı, %20
    3. MACD — kısa vadeli trend, %15
    4. EMA cross — orta vadeli trend teyidi, %10
    5. VWAP deviation — kurumsal yön, %10
    6. Leader bias (BTC+ETH 4h) — makro trend, %10
    7. Bollinger position — trend-following (contrarian DEĞİL), %10

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
    ) -> BayesianEstimate:

        # ── 1. Prior: longshot bias correction ───────────────────────────────
        prior = _longshot_bias_correction(max(0.03, min(0.97, market_price)))
        prior = max(0.05, min(0.95, prior))

        # ── 2. Spot momentum — ANA SİNYAL ───────────────────────────────────
        # 5dk crypto'da spot hareket en güvenilir sinyal.
        # tanh(x*4) → ±0.5% hareket = ±0.96 sinyal (güçlü)
        spot_signal = math.tanh(spot_change_pct * 4.0)

        # ── 3. Order book imbalance — alıcı/satıcı baskısı ──────────────────
        ob_signal = order_book_imbalance * 0.7

        # ── 4. MACD(3,15,3) — kısa vadeli trend ─────────────────────────────
        macd_signal = math.tanh(macd_hist * 12.0)

        # ── 5. EMA 9/21 cross — orta vadeli trend teyidi ────────────────────
        ema_signal = math.tanh(ema_cross * 6.0)

        # ── 6. VWAP deviation — kurumsal yön ────────────────────────────────
        vwap_signal = math.tanh(vwap_dev * 3.0)

        # ── 7. Leader bias (BTC+ETH 4h) — makro trend ───────────────────────
        leader_signal = math.tanh(leader_bias * 2.5)

        # ── 8. Bollinger Bands — SQUEEZE/BREAKOUT enhanced ──────────────────
        # bb_pos > 0.5 = fiyat ortalamanın üstünde = momentum devam eder
        bb_signal = (bb_pos - 0.5) * 0.8

        if bb_breakout != 0.0:
            # BREAKOUT: Band dışına çıkış = güçlü yön sinyali
            # Squeeze sonrası breakout özellikle güçlü (enerji birikimi)
            breakout_strength = abs(bb_breakout)
            if bb_squeeze:
                # Squeeze → breakout: enerji patlaması, max sinyal
                bb_signal = bb_breakout * 1.5
            else:
                # Normal breakout: güçlü ama squeeze kadar değil
                bb_signal = bb_breakout * 1.0
        elif bb_squeeze:
            # Squeeze devam ediyor, breakout yok — bekle
            bb_signal *= 0.2

        # ── MOMENTUM-FIRST ağırlıklar (toplam = 1.0) ────────────────────────
        # BB breakout varsa ağırlığını artır (squeeze→breakout = güçlü sinyal)
        _bb_has_breakout = abs(bb_breakout) > 0.3
        _bb_weight = 0.20 if _bb_has_breakout else 0.10
        _spot_weight = 0.20 if _bb_has_breakout else 0.25  # BB güçlüyse spot'tan al
        #   Spot momentum:  0.25/0.20 (ana sinyal, BB breakout varsa azalır)
        #   Order book:     0.20  (alıcı/satıcı)
        #   MACD:           0.15  (kısa trend)
        #   EMA cross:      0.10  (orta trend)
        #   VWAP:           0.10  (kurumsal)
        #   Leader:         0.05  (makro — BB breakout'a yer aç)
        #   Bollinger:      0.10/0.20 (breakout varsa güçlü)
        raw_signal = (
            spot_signal    * _spot_weight +
            ob_signal      * 0.20 +
            macd_signal    * 0.15 +
            ema_signal     * 0.10 +
            vwap_signal    * 0.10 +
            leader_signal  * 0.05 +
            bb_signal      * _bb_weight
        )
        raw_signal = max(-1.0, min(1.0, raw_signal))

        # ── 9. Volume multiplier ─────────────────────────────────────────────
        vol_mult = _volume_multiplier(volume_ratio)

        # ── 10. News lag boost ───────────────────────────────────────────────
        lag_mult = _news_lag_multiplier(spot_change_pct)

        # ── 11. ATR volatility dampening ─────────────────────────────────────
        effective_vol = atr_pct / 100.0 if atr_pct > 0 else volatility
        confidence = 1.0 / (1.0 + effective_vol * 8.0)
        adjusted_signal = raw_signal * confidence * vol_mult * lag_mult

        adjusted_signal = max(-1.5, min(1.5, adjusted_signal))

        # ── 12. Bayesian update (log-odds) ───────────────────────────────────
        # Güçlendirilmiş update (2.0 vs eski 1.2) — sinyal güçlüyse
        # probability 0.50'den net olarak uzaklaşsın
        log_odds_prior = math.log(prior / (1.0 - prior))
        # Reduced from 2.0 → 1.4: prevents overconfidence on thin momentum.
        # Deep analysis: 2.0x created P=0.524 from +0.05% move → false conviction.
        # 1.4x: still pushes probability away from 0.50, but requires stronger signal.
        log_odds_update = adjusted_signal * 1.4
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
