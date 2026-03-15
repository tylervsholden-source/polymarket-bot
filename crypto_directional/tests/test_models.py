"""
crypto_directional/tests/test_models.py

Baseline modeller için birim testler.
Odak: fit/predict arayüzü, proba şekli, confidence filter, hata durumları.
"""
import numpy as np
import pytest

from crypto_directional.models.baselines import (
    LogisticRegressionBaseline,
    RandomForestBaseline,
    apply_confidence_filter,
    get_model,
)

# LightGBM opsiyonel
try:
    import lightgbm as _lgbm_mod  # noqa: F401
    from crypto_directional.models.baselines import LightGBMBaseline
    _lgbm_available = True
except ImportError:
    _lgbm_available = False


# ── Fixture ───────────────────────────────────────────────────────────────────

def _make_3class(n: int = 300, seed: int = 42):
    """Sentetik 3-sınıf eğitim verisi."""
    rng = np.random.RandomState(seed)
    X = rng.randn(n, 5)
    chunk = n // 3
    y = np.array(
        ["DOWN"] * chunk + ["NO_TRADE"] * chunk + ["UP"] * (n - 2 * chunk)
    )
    rng.shuffle(y)
    return X, y


# ── LogisticRegressionBaseline ────────────────────────────────────────────────

def test_lr_name():
    assert LogisticRegressionBaseline.name == "logistic_regression"


def test_lr_fit_predict():
    X, y = _make_3class()
    m = LogisticRegressionBaseline()
    m.fit(X, y)
    preds = m.predict(X[:10])
    assert preds.shape == (10,)
    assert set(preds).issubset({"DOWN", "NO_TRADE", "UP"})


def test_lr_predict_proba_shape():
    X, y = _make_3class()
    m = LogisticRegressionBaseline()
    m.fit(X, y)
    proba = m.predict_proba(X[:10])
    assert proba.shape == (10, 3)
    np.testing.assert_allclose(proba.sum(axis=1), 1.0, atol=1e-6)


def test_lr_classes_():
    X, y = _make_3class()
    m = LogisticRegressionBaseline()
    m.fit(X, y)
    assert set(m.classes_) == {"DOWN", "NO_TRADE", "UP"}


# ── RandomForestBaseline ──────────────────────────────────────────────────────

def test_rf_name():
    assert RandomForestBaseline.name == "random_forest"


def test_rf_fit_predict():
    X, y = _make_3class()
    m = RandomForestBaseline()
    m.fit(X, y)
    preds = m.predict(X[:10])
    assert preds.shape == (10,)
    assert set(preds).issubset({"DOWN", "NO_TRADE", "UP"})


def test_rf_predict_proba_shape():
    X, y = _make_3class()
    m = RandomForestBaseline()
    m.fit(X, y)
    proba = m.predict_proba(X[:10])
    assert proba.shape == (10, 3)
    np.testing.assert_allclose(proba.sum(axis=1), 1.0, atol=1e-6)


def test_rf_classes_():
    X, y = _make_3class()
    m = RandomForestBaseline()
    m.fit(X, y)
    assert set(m.classes_) == {"DOWN", "NO_TRADE", "UP"}


# ── LightGBM (opsiyonel) ──────────────────────────────────────────────────────

@pytest.mark.skipif(not _lgbm_available, reason="lightgbm kurulu değil")
def test_lgbm_fit_predict():
    X, y = _make_3class()
    m = LightGBMBaseline()
    m.fit(X, y)
    preds = m.predict(X[:10])
    assert set(preds).issubset({"DOWN", "NO_TRADE", "UP"})


@pytest.mark.skipif(not _lgbm_available, reason="lightgbm kurulu değil")
def test_lgbm_proba_shape():
    X, y = _make_3class()
    m = LightGBMBaseline()
    m.fit(X, y)
    proba = m.predict_proba(X[:10])
    assert proba.shape == (10, 3)


# ── get_model ─────────────────────────────────────────────────────────────────

def test_get_model_lr():
    m = get_model("logistic_regression")
    assert isinstance(m, LogisticRegressionBaseline)


def test_get_model_rf():
    m = get_model("random_forest")
    assert isinstance(m, RandomForestBaseline)


def test_get_model_unknown_raises():
    with pytest.raises(ValueError, match="Bilinmeyen model"):
        get_model("nonexistent_model")


# ── apply_confidence_filter ───────────────────────────────────────────────────

def test_filter_low_confidence_becomes_notrade():
    y_pred  = np.array(["UP", "DOWN", "UP"])
    y_proba = np.array([
        [0.35, 0.30, 0.35],   # max=0.35 < 0.55 → NO_TRADE
        [0.40, 0.30, 0.30],   # max=0.40 < 0.55 → NO_TRADE
        [0.10, 0.10, 0.80],   # max=0.80 >= 0.55 → korunur
    ])
    filtered = apply_confidence_filter(y_pred, y_proba, min_confidence=0.55)
    assert filtered[0] == "NO_TRADE"
    assert filtered[1] == "NO_TRADE"
    assert filtered[2] == "UP"


def test_filter_all_pass():
    y_pred  = np.array(["UP", "DOWN"])
    y_proba = np.array([
        [0.05, 0.05, 0.90],
        [0.80, 0.10, 0.10],
    ])
    filtered = apply_confidence_filter(y_pred, y_proba, min_confidence=0.55)
    assert filtered[0] == "UP"
    assert filtered[1] == "DOWN"


def test_filter_zero_threshold_keeps_all():
    """min_confidence=0 → hiçbir şey filtrelenmez."""
    y_pred  = np.array(["UP", "DOWN", "UP"])
    y_proba = np.array([
        [0.34, 0.33, 0.33],
        [0.33, 0.34, 0.33],
        [0.33, 0.33, 0.34],
    ])
    filtered = apply_confidence_filter(y_pred, y_proba, min_confidence=0.0)
    assert list(filtered) == ["UP", "DOWN", "UP"]


def test_filter_output_dtype_str():
    """Çıktı str dtype olmalı."""
    y_pred  = np.array(["UP"])
    y_proba = np.array([[0.1, 0.1, 0.8]])
    result  = apply_confidence_filter(y_pred, y_proba, min_confidence=0.55)
    assert result.dtype.kind == "U"  # Unicode str
