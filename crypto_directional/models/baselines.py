"""
crypto_directional/models/baselines.py

Baseline sınıflandırıcılar: LogisticRegression, RandomForest, LightGBM (opsiyonel).

Ortak duck-type arayüz:
    .name          : str
    .fit(X, y)     : None
    .predict(X)    : ndarray[str]
    .predict_proba(X) : ndarray[float] shape (n, 3)
    .classes_      : ndarray[str]  — sklearn sırasıyla: DOWN, NO_TRADE, UP

Kullanım:
    from crypto_directional.models.baselines import get_model
    model = get_model("logistic_regression")
    model.fit(X_train, y_train)
    y_pred = model.predict(X_val)
"""
from __future__ import annotations

import numpy as np

from crypto_directional.config.settings import settings


class LogisticRegressionBaseline:
    name = "logistic_regression"

    def __init__(self) -> None:
        from sklearn.linear_model import LogisticRegression

        cfg = settings.get("model.logistic_regression") or {}
        self._model = LogisticRegression(
            C=float(cfg.get("C", 1.0)),
            max_iter=int(cfg.get("max_iter", 1000)),
            solver=str(cfg.get("solver", "lbfgs")),
            class_weight=str(settings.get("model.class_weight", "balanced")),
            random_state=int(settings.get("model.random_seed", 42)),
        )

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        self._model.fit(X, y)

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self._model.predict(X)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return self._model.predict_proba(X)

    @property
    def classes_(self) -> np.ndarray:
        return self._model.classes_


class RandomForestBaseline:
    name = "random_forest"

    def __init__(self) -> None:
        from sklearn.ensemble import RandomForestClassifier

        cfg = settings.get("model.random_forest") or {}
        self._model = RandomForestClassifier(
            n_estimators=int(cfg.get("n_estimators", 200)),
            max_depth=int(cfg.get("max_depth", 10)),
            min_samples_leaf=int(cfg.get("min_samples_leaf", 20)),
            class_weight=str(settings.get("model.class_weight", "balanced")),
            random_state=int(settings.get("model.random_seed", 42)),
            n_jobs=-1,
        )

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        self._model.fit(X, y)

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self._model.predict(X)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return self._model.predict_proba(X)

    @property
    def classes_(self) -> np.ndarray:
        return self._model.classes_


class LightGBMBaseline:
    name = "lightgbm"

    def __init__(self) -> None:
        try:
            from lightgbm import LGBMClassifier
        except ImportError:
            raise ImportError("LightGBM kurulu değil: pip install lightgbm")

        cfg = settings.get("model.lightgbm") or {}
        self._model = LGBMClassifier(
            n_estimators=int(cfg.get("n_estimators", 300)),
            learning_rate=float(cfg.get("learning_rate", 0.05)),
            num_leaves=int(cfg.get("num_leaves", 31)),
            min_child_samples=int(cfg.get("min_child_samples", 20)),
            class_weight=str(settings.get("model.class_weight", "balanced")),
            random_state=int(settings.get("model.random_seed", 42)),
            n_jobs=-1,
            verbosity=-1,
        )

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        self._model.fit(X, y)

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self._model.predict(X)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return self._model.predict_proba(X)

    @property
    def classes_(self) -> np.ndarray:
        return self._model.classes_


def get_model(
    name: str,
) -> LogisticRegressionBaseline | RandomForestBaseline | LightGBMBaseline:
    """Model adıyla yeni instance döner."""
    registry = {
        "logistic_regression": LogisticRegressionBaseline,
        "random_forest":       RandomForestBaseline,
        "lightgbm":            LightGBMBaseline,
    }
    if name not in registry:
        raise ValueError(
            f"Bilinmeyen model: {name!r}. Mevcut: {sorted(registry)}"
        )
    return registry[name]()


def apply_confidence_filter(
    y_pred: np.ndarray,
    y_proba: np.ndarray,
    min_confidence: float | None = None,
) -> np.ndarray:
    """
    max(proba) < min_confidence olan tahminleri NO_TRADE'e çevirir.

    Parametreler
    ------------
    y_pred         : str ndarray — model tahminleri
    y_proba        : float ndarray shape (n, n_classes) — predict_proba çıktısı
    min_confidence : None → config'den okur (model.min_confidence = 0.55)

    Döndürür
    --------
    str ndarray — filtrelenmiş tahminler
    """
    if min_confidence is None:
        min_confidence = float(settings.get("model.min_confidence", 0.55))

    result   = np.array(y_pred, dtype=object)
    max_prob = y_proba.max(axis=1)
    result[max_prob < min_confidence] = "NO_TRADE"
    return result.astype(str)
