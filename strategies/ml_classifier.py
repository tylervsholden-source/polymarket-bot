"""
ML Trade Classifier — Learns WIN/LOSS patterns from historical trades.

Trains on closed trade data + shadow journal features.
At runtime, provides ml_score (-1 to +1) for trade quality prediction.

Uses: scikit-learn RandomForest + feature engineering from trade metadata.
"""
from __future__ import annotations

import json
import math
import os
import re
import pickle
from datetime import datetime
from pathlib import Path
from loguru import logger

try:
    import numpy as np
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.model_selection import cross_val_score
    from sklearn.preprocessing import StandardScaler
    _ML_AVAILABLE = True
except ImportError:
    _ML_AVAILABLE = False

MODEL_PATH = Path("data/ml_model.pkl")
POSITIONS_PATH = Path("data/positions.json")


class TradeClassifier:
    """Learns from historical trades to predict WIN/LOSS probability."""

    # Asset encoding
    ASSET_MAP = {"BTC": 0, "ETH": 1, "SOL": 2, "XRP": 3, "DOGE": 4, "BNB": 5, "HYPE": 6}

    def __init__(self):
        self.model = None
        self.scaler = None
        self.is_trained = False
        self.feature_names: list[str] = []
        self._load_model()

    def _load_model(self) -> None:
        """Load pre-trained model from disk if available."""
        if not _ML_AVAILABLE:
            return
        if MODEL_PATH.exists():
            try:
                with open(MODEL_PATH, "rb") as f:
                    data = pickle.load(f)
                self.model = data["model"]
                self.scaler = data["scaler"]
                self.feature_names = data.get("feature_names", [])
                self.is_trained = True
                logger.info(f"ML model loaded: {len(self.feature_names)} features")
            except Exception as e:
                logger.warning(f"ML model load failed: {e}")

    def train(self) -> dict:
        """Train classifier from closed trades in positions.json."""
        if not _ML_AVAILABLE:
            return {"error": "scikit-learn not available"}

        if not POSITIONS_PATH.exists():
            return {"error": "No positions data"}

        with open(POSITIONS_PATH) as f:
            data = json.load(f)

        closed = data.get("closed", [])
        if len(closed) < 20:
            return {"error": f"Not enough data: {len(closed)} trades (need 20+)"}

        # Extract features and labels
        X, y = [], []
        for trade in closed:
            features = self._extract_features(trade)
            if features is None:
                continue
            result = 1 if trade.get("result") == "WIN" else 0
            X.append(features)
            y.append(result)

        if len(X) < 20:
            return {"error": f"Not enough valid trades: {len(X)}"}

        X = np.array(X)
        y = np.array(y)

        # Scale features
        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)

        # Train GradientBoosting (better than RF for small datasets)
        self.model = GradientBoostingClassifier(
            n_estimators=100,
            max_depth=3,
            learning_rate=0.1,
            min_samples_leaf=5,
            random_state=42,
        )
        self.model.fit(X_scaled, y)

        # Cross-validation score
        cv_scores = cross_val_score(self.model, X_scaled, y, cv=min(5, len(X) // 10), scoring="accuracy")

        self.is_trained = True
        self.feature_names = self._feature_names()

        # Save model
        with open(MODEL_PATH, "wb") as f:
            pickle.dump({
                "model": self.model,
                "scaler": self.scaler,
                "feature_names": self.feature_names,
                "trained_at": datetime.now().isoformat(),
                "n_samples": len(X),
            }, f)

        # Feature importance
        importances = dict(zip(self.feature_names, self.model.feature_importances_))
        top_features = sorted(importances.items(), key=lambda x: -x[1])[:5]

        result = {
            "n_samples": len(X),
            "win_rate": float(y.mean()),
            "cv_accuracy": float(cv_scores.mean()),
            "cv_std": float(cv_scores.std()),
            "top_features": top_features,
        }
        logger.info(
            f"ML trained: {len(X)} trades, WR={y.mean():.1%}, "
            f"CV={cv_scores.mean():.1%}±{cv_scores.std():.1%} | "
            f"top={top_features[0][0]}({top_features[0][1]:.2f})"
        )
        return result

    def predict(self, trade_params: dict) -> float:
        """
        Predict WIN probability for a potential trade.

        Args:
            trade_params: dict with keys matching _extract_features_live()

        Returns:
            ml_score: -1.0 (strong LOSS signal) to +1.0 (strong WIN signal)
                      0.0 if model not trained
        """
        if not self.is_trained or not _ML_AVAILABLE:
            return 0.0

        try:
            features = self._extract_features_live(trade_params)
            if features is None:
                return 0.0

            X = np.array([features])
            X_scaled = self.scaler.transform(X)

            # Get probability of WIN class
            win_prob = self.model.predict_proba(X_scaled)[0][1]

            # Convert to -1..+1 score: 0.5 = neutral, >0.5 = bullish, <0.5 = bearish
            ml_score = (win_prob - 0.5) * 2.0
            return round(max(-1.0, min(1.0, ml_score)), 4)

        except Exception as e:
            logger.debug(f"ML predict error: {e}")
            return 0.0

    def _feature_names(self) -> list[str]:
        return [
            "asset_id", "is_yes", "entry_price", "edge",
            "hour_et", "minute_et", "window_minutes",
            "price_distance_from_50", "is_favorite",
            "hour_sin", "hour_cos",
        ]

    def _extract_features(self, trade: dict) -> list[float] | None:
        """Extract feature vector from a closed trade record."""
        try:
            question = trade.get("question", "")
            outcome = trade.get("outcome", "").upper()
            entry_price = float(trade.get("entry_price", 0))

            if entry_price <= 0 or entry_price >= 1:
                return None

            # Parse asset from question
            asset = self._parse_asset(question)
            asset_id = self.ASSET_MAP.get(asset, -1)
            if asset_id < 0:
                return None

            # Parse time from question
            hour, minute = self._parse_time(question)
            window = self._parse_window(question)

            # Direction
            is_yes = 1.0 if outcome in ("YES", "UP") else 0.0

            # Edge proxy: distance from 0.5 (how confident the market was)
            price_distance = abs(entry_price - 0.5)

            # Implied edge (for historical: entry vs 0.5)
            edge = abs(entry_price - 0.5)

            # Is this the market favorite? (price > 0.5 = favorite)
            is_favorite = 1.0 if entry_price > 0.5 else 0.0

            # Cyclical time encoding
            hour_sin = math.sin(2 * math.pi * hour / 24)
            hour_cos = math.cos(2 * math.pi * hour / 24)

            return [
                asset_id, is_yes, entry_price, edge,
                hour, minute, window,
                price_distance, is_favorite,
                hour_sin, hour_cos,
            ]
        except Exception:
            return None

    def _extract_features_live(self, params: dict) -> list[float] | None:
        """Extract features from live trade parameters."""
        try:
            asset = params.get("asset", "")
            asset_id = self.ASSET_MAP.get(asset, -1)
            if asset_id < 0:
                return None

            is_yes = 1.0 if params.get("direction", "").upper() in ("YES", "UP") else 0.0
            entry_price = float(params.get("entry_price", 0.5))
            edge = float(params.get("edge", 0.0))
            hour = float(params.get("hour_et", 12))
            minute = float(params.get("minute_et", 0))
            window = float(params.get("window_minutes", 15))
            price_distance = abs(entry_price - 0.5)
            is_favorite = 1.0 if entry_price > 0.5 else 0.0
            hour_sin = math.sin(2 * math.pi * hour / 24)
            hour_cos = math.cos(2 * math.pi * hour / 24)

            return [
                asset_id, is_yes, entry_price, edge,
                hour, minute, window,
                price_distance, is_favorite,
                hour_sin, hour_cos,
            ]
        except Exception:
            return None

    @staticmethod
    def _parse_asset(question: str) -> str:
        """Extract asset name from market question."""
        q = question.lower()
        for name, symbol in [
            ("bitcoin", "BTC"), ("ethereum", "ETH"), ("solana", "SOL"),
            ("xrp", "XRP"), ("dogecoin", "DOGE"), ("bnb", "BNB"), ("hype", "HYPE"),
        ]:
            if name in q:
                return symbol
        return ""

    @staticmethod
    def _parse_time(question: str) -> tuple[float, float]:
        """Extract hour and minute (ET) from question like '12:30PM'."""
        match = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)", question, re.IGNORECASE)
        if match:
            h, m, ampm = int(match.group(1)), int(match.group(2)), match.group(3).upper()
            if ampm == "PM" and h != 12:
                h += 12
            elif ampm == "AM" and h == 12:
                h = 0
            return float(h), float(m)
        return 12.0, 0.0

    @staticmethod
    def _parse_window(question: str) -> float:
        """Parse time window in minutes from question."""
        matches = re.findall(r"(\d{1,2}):(\d{2})\s*(AM|PM)", question, re.IGNORECASE)
        if len(matches) >= 2:
            def to_min(h, m, ap):
                h = int(h)
                m = int(m)
                if ap.upper() == "PM" and h != 12:
                    h += 12
                elif ap.upper() == "AM" and h == 12:
                    h = 0
                return h * 60 + m
            start = to_min(*matches[0])
            end = to_min(*matches[1])
            diff = end - start
            if diff > 0:
                return float(diff)
        return 15.0
