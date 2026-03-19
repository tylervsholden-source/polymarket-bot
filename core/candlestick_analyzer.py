"""
Candlestick Analyzer — Comprehensive OHLC pattern recognition.

Patterns from professional candlestick cheat sheets:
  Single: Doji, Hammer, Inverted Hammer, Shooting Star, Hanging Man,
          Marubozu, Spinning Top, Dragonfly Doji, Gravestone Doji
  Double: Engulfing, Harami, Tweezer Top/Bottom
  Triple: Morning/Evening Star, Three White Soldiers, Three Black Crows,
          Three Inside Up/Down, Three Outside Up/Down, Three Line Strike
  Continuation: Rising/Falling Three Methods

Also provides:
  - Candle body strength (0-1 scale: doji→marubozu)
  - Multi-candle trend analysis (bull/bear run detection)
"""
from __future__ import annotations


class CandlestickAnalyzer:

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _c(k):
        """Parse a single kline into floats: [ts, open, high, low, close, vol]."""
        return [float(x) for x in k]

    @staticmethod
    def _is_bullish(c): return c[4] > c[1]

    @staticmethod
    def _is_bearish(c): return c[4] < c[1]

    @staticmethod
    def _body(c): return abs(c[4] - c[1])

    @staticmethod
    def _upper_wick(c): return c[2] - max(c[4], c[1])

    @staticmethod
    def _lower_wick(c): return min(c[4], c[1]) - c[3]

    @staticmethod
    def _range(c): return c[2] - c[3]

    @staticmethod
    def _mid(c): return (c[1] + c[4]) / 2

    @staticmethod
    def _body_top(c): return max(c[1], c[4])

    @staticmethod
    def _body_bot(c): return min(c[1], c[4])

    # ── Body Strength ────────────────────────────────────────────────────

    @staticmethod
    def body_strength(kline) -> float:
        """
        Candle body strength: 0.0 (doji) → 1.0 (marubozu).
        Measures how much of the total range is body vs wicks.
        """
        c = CandlestickAnalyzer._c(kline)
        r = CandlestickAnalyzer._range(c)
        if r <= 0:
            return 0.0
        return min(1.0, CandlestickAnalyzer._body(c) / r)

    @staticmethod
    def candle_score(kline) -> float:
        """
        Directional candle score: -1.0 (strong bearish) → +1.0 (strong bullish).
        Combines direction with body strength.
        """
        c = CandlestickAnalyzer._c(kline)
        r = CandlestickAnalyzer._range(c)
        if r <= 0:
            return 0.0
        strength = CandlestickAnalyzer._body(c) / r
        return strength if c[4] > c[1] else -strength if c[4] < c[1] else 0.0

    # ── Multi-Candle Trend Analysis ──────────────────────────────────────

    @staticmethod
    def trend_analysis(klines: list[list], lookback: int = 6) -> dict:
        """
        Analyze trend over last N candles.
        Returns:
            bull_count, bear_count, doji_count, avg_body_strength,
            trend_direction (-1/0/+1), consecutive_same_dir
        """
        n = min(lookback, len(klines))
        if n < 2:
            return {"bull_count": 0, "bear_count": 0, "doji_count": 0,
                    "avg_body_strength": 0.0, "trend_direction": 0,
                    "consecutive_same_dir": 0, "trend_score": 0.0}

        CA = CandlestickAnalyzer
        candles = [CA._c(k) for k in klines[-n:]]

        bull = sum(1 for c in candles if CA._is_bullish(c))
        bear = sum(1 for c in candles if CA._is_bearish(c))
        doji = n - bull - bear

        strengths = [CA._body(c) / CA._range(c) if CA._range(c) > 0 else 0.0
                     for c in candles]
        avg_str = sum(strengths) / len(strengths) if strengths else 0.0

        # Consecutive same direction from last candle
        consec = 1
        last_dir = 1 if CA._is_bullish(candles[-1]) else -1 if CA._is_bearish(candles[-1]) else 0
        for c in reversed(candles[:-1]):
            d = 1 if CA._is_bullish(c) else -1 if CA._is_bearish(c) else 0
            if d == last_dir and d != 0:
                consec += 1
            else:
                break

        # Trend direction
        if bull >= n * 0.67:
            direction = 1
        elif bear >= n * 0.67:
            direction = -1
        else:
            direction = 0

        # Trend score: -1.0 to +1.0 (considers both count and strength)
        score_sum = sum(CA.candle_score(k) for k in klines[-n:])
        trend_score = max(-1.0, min(1.0, score_sum / n))

        return {
            "bull_count": bull,
            "bear_count": bear,
            "doji_count": doji,
            "avg_body_strength": round(avg_str, 3),
            "trend_direction": direction,
            "consecutive_same_dir": consec,
            "trend_score": round(trend_score, 4),
        }

    # ── Main Pattern Recognition ─────────────────────────────────────────

    @staticmethod
    def analyze(klines: list[list]) -> list[str]:
        """
        Comprehensive candlestick pattern recognition.
        Klines format: [[timestamp, open, high, low, close, volume], ...]
        Returns list of detected pattern names.
        """
        if len(klines) < 3:
            return []

        CA = CandlestickAnalyzer
        patterns = []

        # Parse last candles
        c1 = CA._c(klines[-1])   # Current
        c2 = CA._c(klines[-2])   # Previous
        c3 = CA._c(klines[-3])   # Two back

        body1, body2, body3 = CA._body(c1), CA._body(c2), CA._body(c3)
        range1, range2 = CA._range(c1), CA._range(c2)
        uw1, lw1 = CA._upper_wick(c1), CA._lower_wick(c1)

        # ═══════════════════════════════════════════════════════════════════
        # SINGLE CANDLE PATTERNS
        # ═══════════════════════════════════════════════════════════════════

        # 1. DOJI — body < 10% of range
        is_doji = range1 > 0 and body1 <= range1 * 0.1
        if is_doji:
            # Sub-classify doji
            if lw1 > range1 * 0.6 and uw1 < range1 * 0.1:
                patterns.append("DRAGONFLY_DOJI")       # Long lower wick
            elif uw1 > range1 * 0.6 and lw1 < range1 * 0.1:
                patterns.append("GRAVESTONE_DOJI")      # Long upper wick
            else:
                patterns.append("DOJI")                  # Standard doji

        # 2. SPINNING TOP — small body (10-35% of range), both wicks significant
        if not is_doji and range1 > 0 and body1 > range1 * 0.1 and body1 <= range1 * 0.35:
            if uw1 > body1 * 0.4 and lw1 > body1 * 0.4:
                if CA._is_bullish(c1):
                    patterns.append("BULLISH_SPINNING_TOP")
                elif CA._is_bearish(c1):
                    patterns.append("BEARISH_SPINNING_TOP")

        # 3. HAMMER — small body at top, long lower wick (>2x body)
        if body1 > 0 and lw1 >= body1 * 2 and uw1 <= body1 * 0.5:
            patterns.append("HAMMER")

        # 4. INVERTED HAMMER / SHOOTING STAR — small body at bottom, long upper wick
        # Requires meaningful body (not doji) to distinguish from gravestone doji
        if not is_doji and body1 > 0 and uw1 >= body1 * 2 and lw1 <= body1 * 0.5:
            if CA._is_bearish(c2):
                patterns.append("INVERTED_HAMMER")
            if CA._is_bullish(c2):
                patterns.append("SHOOTING_STAR")

        # 5. HANGING MAN — hammer shape after uptrend (bearish signal)
        if not is_doji and body1 > 0 and lw1 >= body1 * 2 and uw1 <= body1 * 0.5:
            if CA._is_bullish(c2):
                patterns.append("HANGING_MAN")

        # 7. MARUBOZU — body >= 90% of range (strong conviction)
        if range1 > 0 and body1 >= range1 * 0.9:
            patterns.append("BULLISH_MARUBOZU" if CA._is_bullish(c1) else "BEARISH_MARUBOZU")

        # ═══════════════════════════════════════════════════════════════════
        # DOUBLE CANDLE PATTERNS
        # ═══════════════════════════════════════════════════════════════════

        # 8. BULLISH ENGULFING — bearish c2 fully engulfed by bullish c1
        if CA._is_bearish(c2) and CA._is_bullish(c1):
            if c1[4] > c2[1] and c1[1] < c2[4]:
                patterns.append("BULLISH_ENGULFING")

        # 9. BEARISH ENGULFING — bullish c2 fully engulfed by bearish c1
        if CA._is_bullish(c2) and CA._is_bearish(c1):
            if c1[4] < c2[1] and c1[1] > c2[4]:
                patterns.append("BEARISH_ENGULFING")

        # 10. BULLISH HARAMI — large bearish c2, small bullish c1 inside c2's body
        if CA._is_bearish(c2) and CA._is_bullish(c1):
            if body1 < body2 * 0.5 and CA._body_top(c1) < c2[1] and CA._body_bot(c1) > c2[4]:
                patterns.append("BULLISH_HARAMI")

        # 11. BEARISH HARAMI — large bullish c2, small bearish c1 inside c2's body
        if CA._is_bullish(c2) and CA._is_bearish(c1):
            if body1 < body2 * 0.5 and CA._body_top(c1) < c2[4] and CA._body_bot(c1) > c2[1]:
                patterns.append("BEARISH_HARAMI")

        # 12. TWEEZER BOTTOM — two candles with similar lows after downtrend
        if range1 > 0 and range2 > 0:
            low_diff = abs(c1[3] - c2[3])
            avg_range = (range1 + range2) / 2
            if low_diff < avg_range * 0.05 and CA._is_bearish(c2) and CA._is_bullish(c1):
                patterns.append("TWEEZER_BOTTOM")

        # 13. TWEEZER TOP — two candles with similar highs after uptrend
        if range1 > 0 and range2 > 0:
            high_diff = abs(c1[2] - c2[2])
            avg_range = (range1 + range2) / 2
            if high_diff < avg_range * 0.05 and CA._is_bullish(c2) and CA._is_bearish(c1):
                patterns.append("TWEEZER_TOP")

        # ═══════════════════════════════════════════════════════════════════
        # TRIPLE CANDLE PATTERNS
        # ═══════════════════════════════════════════════════════════════════

        # 14. MORNING STAR — bearish + small body + bullish (reversal up)
        if (CA._is_bearish(c3) and body2 < body3 * 0.3 and
                CA._is_bullish(c1) and c1[4] > (c3[1] + c3[4]) / 2):
            patterns.append("MORNING_STAR")

        # 15. EVENING STAR — bullish + small body + bearish (reversal down)
        if (CA._is_bullish(c3) and body2 < body3 * 0.3 and
                CA._is_bearish(c1) and c1[4] < (c3[1] + c3[4]) / 2):
            patterns.append("EVENING_STAR")

        # 16. THREE WHITE SOLDIERS — three consecutive bullish with higher closes
        if (CA._is_bullish(c3) and CA._is_bullish(c2) and CA._is_bullish(c1) and
                c2[4] > c3[4] and c1[4] > c2[4] and
                body3 > range1 * 0.3 and body2 > range1 * 0.3 and body1 > range1 * 0.3):
            patterns.append("THREE_WHITE_SOLDIERS")

        # 17. THREE BLACK CROWS — three consecutive bearish with lower closes
        if (CA._is_bearish(c3) and CA._is_bearish(c2) and CA._is_bearish(c1) and
                c2[4] < c3[4] and c1[4] < c2[4] and
                body3 > range1 * 0.3 and body2 > range1 * 0.3 and body1 > range1 * 0.3):
            patterns.append("THREE_BLACK_CROWS")

        # 18. THREE INSIDE UP — bearish + bullish harami + bullish continuation
        if (CA._is_bearish(c3) and CA._is_bullish(c2) and CA._is_bullish(c1) and
                body2 < body3 * 0.5 and CA._body_top(c2) < c3[1] and
                c1[4] > c3[1]):
            patterns.append("THREE_INSIDE_UP")

        # 19. THREE INSIDE DOWN — bullish + bearish harami + bearish continuation
        if (CA._is_bullish(c3) and CA._is_bearish(c2) and CA._is_bearish(c1) and
                body2 < body3 * 0.5 and CA._body_bot(c2) > c3[1] and
                c1[4] < c3[1]):
            patterns.append("THREE_INSIDE_DOWN")

        # For 4+ candle patterns, need more history
        if len(klines) >= 4:
            c4 = CA._c(klines[-4])
            body4 = CA._body(c4)

            # 20. THREE OUTSIDE UP — bullish engulfing + bullish continuation
            if (CA._is_bearish(c4) and CA._is_bullish(c3) and
                    c3[4] > c4[1] and c3[1] < c4[4] and
                    CA._is_bullish(c2) and c2[4] > c3[4] and
                    CA._is_bullish(c1)):
                patterns.append("THREE_OUTSIDE_UP")

            # 21. THREE OUTSIDE DOWN — bearish engulfing + bearish continuation
            if (CA._is_bullish(c4) and CA._is_bearish(c3) and
                    c3[4] < c4[1] and c3[1] > c4[4] and
                    CA._is_bearish(c2) and c2[4] < c3[4] and
                    CA._is_bearish(c1)):
                patterns.append("THREE_OUTSIDE_DOWN")

            # 22. BULLISH THREE LINE STRIKE — 3 bearish + 1 bullish engulfs all
            if (CA._is_bearish(c4) and CA._is_bearish(c3) and CA._is_bearish(c2) and
                    c3[4] < c4[4] and c2[4] < c3[4] and
                    CA._is_bullish(c1) and c1[4] > c4[1] and c1[1] < c2[4]):
                patterns.append("BULLISH_THREE_LINE_STRIKE")

            # 23. BEARISH THREE LINE STRIKE — 3 bullish + 1 bearish engulfs all
            if (CA._is_bullish(c4) and CA._is_bullish(c3) and CA._is_bullish(c2) and
                    c3[4] > c4[4] and c2[4] > c3[4] and
                    CA._is_bearish(c1) and c1[4] < c4[1] and c1[1] > c2[4]):
                patterns.append("BEARISH_THREE_LINE_STRIKE")

        # For 5+ candle patterns
        if len(klines) >= 5:
            c4 = CA._c(klines[-4])
            c5 = CA._c(klines[-5])

            # 24. RISING THREE METHODS — bullish continuation
            # Big bullish + 3 small bearish inside range + big bullish
            if (CA._is_bullish(c5) and CA._body(c5) > CA._range(c5) * 0.5 and
                    CA._is_bearish(c4) and CA._is_bearish(c3) and CA._is_bearish(c2) and
                    c4[3] > c5[3] and c3[3] > c5[3] and c2[3] > c5[3] and
                    c4[2] < c5[2] and c3[2] < c5[2] and c2[2] < c5[2] and
                    CA._is_bullish(c1) and c1[4] > c5[4]):
                patterns.append("RISING_THREE_METHODS")

            # 25. FALLING THREE METHODS — bearish continuation
            if (CA._is_bearish(c5) and CA._body(c5) > CA._range(c5) * 0.5 and
                    CA._is_bullish(c4) and CA._is_bullish(c3) and CA._is_bullish(c2) and
                    c4[2] < c5[2] and c3[2] < c5[2] and c2[2] < c5[2] and
                    c4[3] > c5[3] and c3[3] > c5[3] and c2[3] > c5[3] and
                    CA._is_bearish(c1) and c1[4] < c5[4]):
                patterns.append("FALLING_THREE_METHODS")

        # Deduplicate (e.g. both SHOOTING_STAR detected from different rules)
        return list(dict.fromkeys(patterns))

    # ── Pattern Scoring ──────────────────────────────────────────────────

    # Bullish patterns → positive score, Bearish → negative
    _PATTERN_SCORES: dict[str, float] = {
        # Strong bullish reversal
        "MORNING_STAR":             +0.8,
        "THREE_WHITE_SOLDIERS":     +0.9,
        "BULLISH_ENGULFING":        +0.7,
        "THREE_INSIDE_UP":          +0.7,
        "THREE_OUTSIDE_UP":         +0.8,
        "BULLISH_THREE_LINE_STRIKE":+0.85,
        "RISING_THREE_METHODS":     +0.6,
        # Moderate bullish
        "HAMMER":                   +0.5,
        "INVERTED_HAMMER":          +0.4,
        "BULLISH_HARAMI":           +0.4,
        "TWEEZER_BOTTOM":           +0.5,
        "DRAGONFLY_DOJI":           +0.3,
        "BULLISH_MARUBOZU":         +0.6,
        "BULLISH_SPINNING_TOP":     +0.1,
        # Strong bearish reversal
        "EVENING_STAR":             -0.8,
        "THREE_BLACK_CROWS":        -0.9,
        "BEARISH_ENGULFING":        -0.7,
        "THREE_INSIDE_DOWN":        -0.7,
        "THREE_OUTSIDE_DOWN":       -0.8,
        "BEARISH_THREE_LINE_STRIKE":-0.85,
        "FALLING_THREE_METHODS":    -0.6,
        # Moderate bearish
        "SHOOTING_STAR":            -0.5,
        "HANGING_MAN":              -0.5,
        "BEARISH_HARAMI":           -0.4,
        "TWEEZER_TOP":              -0.5,
        "GRAVESTONE_DOJI":          -0.3,
        "BEARISH_MARUBOZU":         -0.6,
        "BEARISH_SPINNING_TOP":     -0.1,
        # Neutral
        "DOJI":                      0.0,
    }

    @classmethod
    def pattern_score(cls, patterns: list[str]) -> float:
        """
        Aggregate score from detected patterns.
        Returns: -1.0 (max bearish) to +1.0 (max bullish).
        """
        if not patterns:
            return 0.0
        total = sum(cls._PATTERN_SCORES.get(p, 0.0) for p in patterns)
        return max(-1.0, min(1.0, total))

    # ── Multi-Timeframe Aggregation ──────────────────────────────────────

    @classmethod
    def multi_tf_score(cls, tf_patterns: dict[str, list[str]],
                       tf_trends: dict[str, dict]) -> dict:
        """
        Aggregate candlestick analysis across multiple timeframes.

        Args:
            tf_patterns: {"4h": ["HAMMER", ...], "1h": [...], "15m": [...], "5m": [...]}
            tf_trends: {"4h": trend_analysis_dict, ...}

        Returns:
            {
                "candle_bias": float,      # -1 to +1 weighted signal
                "candle_strength": float,  # 0-1 confidence
                "dominant_tf": str,        # which TF dominates
                "pattern_count": int,
                "alignment": float,        # 0-1 how aligned TFs are
            }
        """
        # Timeframe weights: higher TF = more weight
        tf_weights = {"4h": 0.35, "1h": 0.30, "15m": 0.20, "5m": 0.15}

        weighted_score = 0.0
        total_weight = 0.0
        tf_scores = {}
        pattern_count = 0

        for tf, weight in tf_weights.items():
            pats = tf_patterns.get(tf, [])
            trend = tf_trends.get(tf, {})
            pattern_count += len(pats)

            # Pattern score
            p_score = cls.pattern_score(pats)
            # Trend score from trend_analysis
            t_score = trend.get("trend_score", 0.0)
            # Body strength (strong bodies = more conviction)
            avg_strength = trend.get("avg_body_strength", 0.5)

            # Combine: patterns weighted higher than general trend
            combined = p_score * 0.6 + t_score * 0.4
            # Scale by body strength (strong candles = more reliable)
            combined *= (0.5 + avg_strength * 0.5)

            tf_scores[tf] = combined
            weighted_score += combined * weight
            total_weight += weight

        if total_weight > 0:
            weighted_score /= total_weight

        # Alignment: do all TFs agree on direction?
        directions = [1 if s > 0.1 else -1 if s < -0.1 else 0
                      for s in tf_scores.values() if s != 0]
        if directions:
            dominant = max(set(directions), key=directions.count)
            alignment = sum(1 for d in directions if d == dominant) / len(directions)
        else:
            alignment = 0.0

        # Find dominant TF
        dominant_tf = max(tf_scores, key=lambda k: abs(tf_scores[k])) if tf_scores else "1h"

        # Overall strength
        candle_strength = min(1.0, abs(weighted_score) * alignment)

        return {
            "candle_bias": round(max(-1.0, min(1.0, weighted_score)), 4),
            "candle_strength": round(candle_strength, 3),
            "dominant_tf": dominant_tf,
            "pattern_count": pattern_count,
            "alignment": round(alignment, 2),
            "tf_scores": {k: round(v, 4) for k, v in tf_scores.items()},
        }
