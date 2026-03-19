"""
calibration/calibrator.py

Kalibrasyon yardımcıları: identity passthrough, Platt scaling, isotonic regresyon.

LEAKAGE KURALI:
    fit() yalnızca validation seti üzerinde çağrılır.
    Test verisi veya canlı veri fit'e dahil edilemez.

Yöntemler
---------
IdentityCalibrator   : ham güven = kalibre olasılık (veri yok, Phase 4 varsayılan)
PlattCalibrator      : sigmoid uyumu A*x + B (≥50 örnek)
IsotonicCalibrator   : monotonic regresyon, sklearn gerekli (≥200 örnek)

Tanısal metrikler
-----------------
brier_score(y_true, y_prob) → float
log_loss_score(y_true, y_prob) → float
expected_calibration_error(y_true, y_prob, n_bins=10) → float
calibration_curve_buckets(y_true, y_prob, n_bins=10) → list[dict]
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from calibration.types import CalibrationMethod, CalibrationQuality


# ── Diagnostik fonksiyonlar ───────────────────────────────────────────────────

def brier_score(y_true: list[float] | np.ndarray, y_prob: list[float] | np.ndarray) -> float:
    """Ortalama kare hata: BS = mean((p - y)^2). Düşük = iyi. ≤ 0.25 makul."""
    yt = np.asarray(y_true, dtype=float)
    yp = np.asarray(y_prob, dtype=float)
    return float(np.mean((yp - yt) ** 2))


def log_loss_score(
    y_true: list[float] | np.ndarray,
    y_prob: list[float] | np.ndarray,
    eps: float = 1e-15,
) -> float:
    """Negatif log likelihood. Düşük = iyi. ≤ 0.50 makul."""
    yt = np.asarray(y_true, dtype=float)
    yp = np.clip(np.asarray(y_prob, dtype=float), eps, 1 - eps)
    return float(-np.mean(yt * np.log(yp) + (1 - yt) * np.log(1 - yp)))


def expected_calibration_error(
    y_true: list[float] | np.ndarray,
    y_prob: list[float] | np.ndarray,
    n_bins: int = 10,
) -> float:
    """
    Beklenen kalibrasyon hatası (ECE).
    Her bucket'ta |ortalama_tahmin - ortalama_gerçek| × bucket_oranı.
    ≤ 0.10 kabul edilebilir.
    """
    yt = np.asarray(y_true, dtype=float)
    yp = np.asarray(y_prob, dtype=float)
    n = len(yt)
    if n == 0:
        return float("nan")

    bins = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    for lo, hi in zip(bins[:-1], bins[1:]):
        mask = (yp >= lo) & (yp < hi)
        if not mask.any():
            continue
        n_b = mask.sum()
        mean_pred   = float(yp[mask].mean())
        mean_actual = float(yt[mask].mean())
        ece += (n_b / n) * abs(mean_pred - mean_actual)
    return float(ece)


def calibration_curve_buckets(
    y_true: list[float] | np.ndarray,
    y_prob: list[float] | np.ndarray,
    n_bins: int = 10,
) -> list[dict]:
    """
    Kalibrasyon eğrisi için bucket verileri.
    Her bucket: {"bin_low", "bin_high", "mean_pred", "mean_actual", "count"}
    """
    yt = np.asarray(y_true, dtype=float)
    yp = np.asarray(y_prob, dtype=float)
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    result = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        mask = (yp >= lo) & (yp < hi)
        result.append({
            "bin_low":     round(float(lo), 3),
            "bin_high":    round(float(hi), 3),
            "mean_pred":   float(yp[mask].mean()) if mask.any() else None,
            "mean_actual": float(yt[mask].mean()) if mask.any() else None,
            "count":       int(mask.sum()),
        })
    return result


def _quality_from_ece(ece: float, n_samples: int) -> CalibrationQuality:
    """ECE ve örnek sayısına göre kalibrasyon kalitesi etiketler."""
    if n_samples < 50:
        return CalibrationQuality.WEAK
    if ece < 0.05 and n_samples >= 200:
        return CalibrationQuality.STRONG
    if ece >= 0.10:
        return CalibrationQuality.WEAK
    return CalibrationQuality.WEAK  # 0.05–0.10 arası: yeterince kanıtlanmamış → WEAK


# ── Kalibratörler ─────────────────────────────────────────────────────────────

class IdentityCalibrator:
    """
    Passthrough kalibratör: ham güven = kalibre olasılık.

    Veri gerektirmez. Phase 4 varsayılanı.
    quality = UNKNOWN (kalibre edildiğinin kanıtı yok).

    Tasarım notu: identity kullanımı bilinçli bir varsayımdır.
    "Kalibre etmeden geç" anlamına gelmez — eksikliği belgeler.
    """
    method = CalibrationMethod.IDENTITY
    quality = CalibrationQuality.UNKNOWN
    n_samples_fit = 0

    def fit(self, raw_scores: list[float], y_true: list[float]) -> "IdentityCalibrator":
        """Identity'de fit yapılmaz — bu bir no-op'tur."""
        return self

    def transform(self, raw_score: float) -> float:
        """Ham güveni olduğu gibi döndürür."""
        return float(raw_score)

    def diagnostics(self) -> dict:
        return {
            "method": self.method.value,
            "quality": self.quality.value,
            "n_samples": 0,
            "brier_score": None,
            "log_loss": None,
            "ece": None,
        }


class PlattCalibrator:
    """
    Platt scaling: sigmoid uyumu f(x) = 1 / (1 + exp(-(A*x + B)))

    Parametreler gradient descent ile fit edilir.
    Leakage önleme: fit() YALNIZCA validation seti üzerinde çağrılmalı.

    Gereksinim: ≥50 örnek.
    """
    method = CalibrationMethod.PLATT

    def __init__(self) -> None:
        self._A: float = 1.0   # başlangıç değeri
        self._B: float = 0.0
        self._fitted = False
        self._n_samples = 0
        self._quality = CalibrationQuality.UNKNOWN
        self._brier: Optional[float] = None
        self._logloss: Optional[float] = None
        self._ece: Optional[float] = None

    def fit(
        self,
        raw_scores: list[float],
        y_true: list[float],
        lr: float = 0.01,
        max_iter: int = 1000,
        tol: float = 1e-6,
    ) -> "PlattCalibrator":
        """
        Gradient descent ile A, B parametrelerini fit eder.

        raw_scores : ham sınıflandırıcı çıktıları (0-1)
        y_true     : gerçek sınıflar (0.0 veya 1.0)
        """
        x = np.asarray(raw_scores, dtype=float)
        y = np.asarray(y_true, dtype=float)
        self._n_samples = len(x)

        A, B = 1.0, 0.0
        prev_loss = float("inf")

        for _ in range(max_iter):
            # sigmoid çıktıları
            z = A * x + B
            # numerik kararlılık için clip
            z = np.clip(z, -500, 500)
            p = 1.0 / (1.0 + np.exp(-z))
            p = np.clip(p, 1e-15, 1 - 1e-15)

            # log loss
            loss = -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))

            if abs(prev_loss - loss) < tol:
                break
            prev_loss = loss

            # gradyanlar
            err   = p - y
            dA    = float(np.mean(err * x))
            dB    = float(np.mean(err))
            A    -= lr * dA
            B    -= lr * dB

        self._A = float(A)
        self._B = float(B)
        self._fitted = True

        # diagnostikler
        p_cal = self._transform_array(x)
        self._brier   = brier_score(y, p_cal)
        self._logloss = log_loss_score(y, p_cal)
        self._ece     = expected_calibration_error(y, p_cal)
        self._quality = _quality_from_ece(self._ece, self._n_samples)
        return self

    def transform(self, raw_score: float) -> float:
        if not self._fitted:
            raise RuntimeError("PlattCalibrator.transform() fit() öncesinde çağrıldı.")
        z = self._A * raw_score + self._B
        z = max(-500.0, min(500.0, z))
        return float(1.0 / (1.0 + math.exp(-z)))

    def _transform_array(self, x: np.ndarray) -> np.ndarray:
        z = np.clip(self._A * x + self._B, -500, 500)
        return 1.0 / (1.0 + np.exp(-z))

    @property
    def quality(self) -> CalibrationQuality:
        return self._quality

    def diagnostics(self) -> dict:
        return {
            "method": self.method.value,
            "quality": self._quality.value,
            "n_samples": self._n_samples,
            "A": self._A,
            "B": self._B,
            "brier_score": self._brier,
            "log_loss": self._logloss,
            "ece": self._ece,
        }


class IsotonicCalibrator:
    """
    Isotonic (monotonic) regresyon kalibratörü.

    sklearn gerektirir. Yoksa ImportError fırlatır — sessiz fallback yapılmaz.
    Gereksinim: ≥200 örnek.

    Leakage önleme: fit() YALNIZCA validation seti üzerinde çağrılmalı.
    """
    method = CalibrationMethod.ISOTONIC

    def __init__(self) -> None:
        try:
            from sklearn.isotonic import IsotonicRegression as _IR
            self._model = _IR(out_of_bounds="clip")
        except ImportError as e:
            raise ImportError(
                "IsotonicCalibrator için scikit-learn gereklidir. "
                "pip install scikit-learn"
            ) from e
        self._fitted = False
        self._n_samples = 0
        self._quality = CalibrationQuality.UNKNOWN
        self._brier: Optional[float] = None
        self._logloss: Optional[float] = None
        self._ece: Optional[float] = None

    def fit(self, raw_scores: list[float], y_true: list[float]) -> "IsotonicCalibrator":
        x = np.asarray(raw_scores, dtype=float)
        y = np.asarray(y_true, dtype=float)
        self._n_samples = len(x)
        self._model.fit(x, y)
        self._fitted = True

        p_cal = self._model.predict(x)
        self._brier   = brier_score(y, p_cal)
        self._logloss = log_loss_score(y, p_cal)
        self._ece     = expected_calibration_error(y, p_cal)
        self._quality = _quality_from_ece(self._ece, self._n_samples)
        return self

    def transform(self, raw_score: float) -> float:
        if not self._fitted:
            raise RuntimeError("IsotonicCalibrator.transform() fit() öncesinde çağrıldı.")
        return float(self._model.predict([raw_score])[0])

    @property
    def quality(self) -> CalibrationQuality:
        return self._quality

    def diagnostics(self) -> dict:
        return {
            "method": self.method.value,
            "quality": self._quality.value,
            "n_samples": self._n_samples,
            "brier_score": self._brier,
            "log_loss": self._logloss,
            "ece": self._ece,
        }


def get_calibrator(method: CalibrationMethod = CalibrationMethod.IDENTITY):
    """Yönteme göre kalibratör instance'ı döndürür."""
    if method == CalibrationMethod.IDENTITY:
        return IdentityCalibrator()
    if method == CalibrationMethod.PLATT:
        return PlattCalibrator()
    if method == CalibrationMethod.ISOTONIC:
        return IsotonicCalibrator()
    raise ValueError(f"Bilinmeyen kalibrasyon yöntemi: {method!r}")
