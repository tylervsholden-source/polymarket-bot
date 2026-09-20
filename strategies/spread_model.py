"""
Spread model: detects cross-market dislocations using z-score analysis.

When two related markets (e.g. BTC 5m and BTC 15m) diverge abnormally,
one is likely mispriced. The one with higher z-score deviation is the entry.

z = (S - mu_S) / sigma_S
S = current spread between market pair
mu_S = historical mean spread
sigma_S = historical std of spread
"""
import math
from collections import deque


class SpreadModel:
    def __init__(self, window: int = 30, min_z: float = 1.8):
        self.window = window
        self.min_z = min_z
        self._history: dict[str, deque] = {}  # pair_key → spread history

    def _pair_key(self, id1: str, id2: str) -> str:
        return f"{min(id1, id2)}|{max(id1, id2)}"

    def record(self, market1_id: str, price1: float, market2_id: str, price2: float) -> float:
        """Record spread and return current z-score.

        z_score() must run BEFORE the current spread is appended to history.
        Appending first (the previous order) made z_score()'s own mu/sigma
        include the very point being tested against them — for a genuine
        outlier this pulls mu toward it and inflates sigma, systematically
        understating the z-score (e.g. a spread that should score z≈11.5
        against its prior history scored just z≈2.2 once folded into its
        own baseline) and letting real dislocations slip under
        find_dislocations()'s min_z threshold.
        """
        key = self._pair_key(market1_id, market2_id)
        if key not in self._history:
            self._history[key] = deque(maxlen=self.window)

        z = self.z_score(market1_id, price1, market2_id, price2)
        self._history[key].append(price1 - price2)
        return z

    def z_score(self, market1_id: str, price1: float, market2_id: str, price2: float) -> float:
        """
        z = (current_spread - mean_spread) / std_spread
        High |z| = abnormal dislocation = potential arbitrage.
        """
        key = self._pair_key(market1_id, market2_id)
        history = self._history.get(key)
        if not history or len(history) < 5:
            return 0.0

        spreads = list(history)
        current = price1 - price2
        mu = sum(spreads) / len(spreads)
        variance = sum((s - mu) ** 2 for s in spreads) / len(spreads)
        if variance <= 1e-8:
            return 0.0  # Stabil spread — dislokasyon sinyali yok
        sigma = math.sqrt(variance)
        return round((current - mu) / sigma, 3)

    @staticmethod
    def _detect_horizon(question: str) -> str:
        """Market sorusundan horizon çıkar (gruplama için)."""
        import re
        m = re.search(
            r'(\d{1,2}):(\d{2})\s*(AM|PM)\s*[-–]\s*(\d{1,2}):(\d{2})\s*(AM|PM)',
            question, re.IGNORECASE,
        )
        if not m:
            return "unknown"
        t1 = ((int(m.group(1)) % 12) + (12 if m.group(3).upper() == "PM" else 0)) * 60 + int(m.group(2))
        t2 = ((int(m.group(4)) % 12) + (12 if m.group(6).upper() == "PM" else 0)) * 60 + int(m.group(5))
        diff = t2 - t1
        if diff <= 0:
            diff += 24 * 60
        if diff <= 7:
            return "5m"
        elif diff <= 20:
            return "15m"
        elif diff <= 90:
            return "1h"
        return "4h"

    def find_dislocations(self, markets: list[dict]) -> list[dict]:
        """
        Group related markets by asset AND horizon, compare pairs, return dislocated ones.
        Only same-horizon markets are compared — different horizons naturally diverge.
        Returns list sorted by |z_score| descending.
        """
        # Group by (asset, horizon) — farklı horizon'lar karşılaştırılmaz
        by_group: dict[str, list] = {}
        _ALIASES = {
            "bitcoin": ["btc"], "ethereum": ["eth"], "solana": ["sol"],
            "dogecoin": ["doge"], "hyperliquid": ["hype"],
        }
        for m in markets:
            q = m.get("question", "").lower()
            for asset in ["bitcoin", "ethereum", "solana", "xrp", "dogecoin", "bnb", "hyperliquid"]:
                if asset in q or any(alias in q for alias in _ALIASES.get(asset, [])):
                    horizon = self._detect_horizon(m.get("question", ""))
                    key = f"{asset}|{horizon}"
                    by_group.setdefault(key, []).append(m)
                    break

        dislocations = []
        for group_key, group_markets in by_group.items():
            if len(group_markets) < 2:
                continue
            asset = group_key.split("|")[0]

            for i, m1 in enumerate(group_markets):
                for m2 in group_markets[i + 1:]:
                    p1 = float(m1.get("best_ask", 0.5) or 0.5)
                    p2 = float(m2.get("best_ask", 0.5) or 0.5)

                    z = self.record(m1["condition_id"], p1, m2["condition_id"], p2)

                    if abs(z) >= self.min_z:
                        underpriced = m2 if z > 0 else m1
                        overpriced = m1 if z > 0 else m2
                        dislocations.append({
                            "underpriced_market": underpriced,
                            "overpriced_market": overpriced,
                            "z_score": z,
                            "asset": asset,
                            "spread": p1 - p2,
                        })

        return sorted(dislocations, key=lambda x: abs(x["z_score"]), reverse=True)
