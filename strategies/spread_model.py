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
        """Record spread and return current z-score."""
        key = self._pair_key(market1_id, market2_id)
        if key not in self._history:
            self._history[key] = deque(maxlen=self.window)

        spread = price1 - price2
        self._history[key].append(spread)
        return self.z_score(market1_id, price1, market2_id, price2)

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
        sigma = math.sqrt(variance) if variance > 1e-8 else 0.001
        return round((current - mu) / sigma, 3)

    def find_dislocations(self, markets: list[dict]) -> list[dict]:
        """
        Group related markets by asset, compare pairs, return dislocated ones.
        Returns list sorted by |z_score| descending.
        """
        # Group by asset
        by_asset: dict[str, list] = {}
        for m in markets:
            q = m.get("question", "").lower()
            for asset in ["bitcoin", "ethereum", "solana", "xrp", "dogecoin", "bnb"]:
                if asset in q or (asset == "bitcoin" and "btc" in q):
                    by_asset.setdefault(asset, []).append(m)
                    break

        dislocations = []
        for asset, asset_markets in by_asset.items():
            if len(asset_markets) < 2:
                continue

            # Compare all pairs within the same asset
            for i, m1 in enumerate(asset_markets):
                for m2 in asset_markets[i + 1:]:
                    p1 = float(m1.get("best_ask", 0.5) or 0.5)
                    p2 = float(m2.get("best_ask", 0.5) or 0.5)

                    z = self.record(m1["condition_id"], p1, m2["condition_id"], p2)

                    if abs(z) >= self.min_z:
                        # Positive z: m1 is overpriced vs m2 → buy m2 (underpriced)
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
