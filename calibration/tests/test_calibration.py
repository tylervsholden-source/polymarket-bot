"""
calibration/tests/test_calibration.py

calibrator.py ve probability_mapper.py birim testleri.

Kapsam:
- Ham güven kalibre olasılık değildir (identity bile açıkça proxy)
- Olasılık eşleştirmesi: NORMAL / INVERTED polarity
- NO_TRADE pass-through → REJECT
- Platt kalibratör fit/transform
- Zayıf kalibrasyon → UNKNOWN kalite etiketi
- Tutarsız olasılık → REJECT
- Ambiguous polarity → REJECT
"""
from __future__ import annotations

import math
import pytest
from datetime import datetime, timezone

from calibration.calibrator import (
    IdentityCalibrator,
    PlattCalibrator,
    brier_score,
    expected_calibration_error,
    log_loss_score,
    get_calibrator,
)
from calibration.probability_mapper import map_to_event_probability
from calibration.types import (
    CalibrationMethod,
    CalibrationQuality,
    CalibrationRejectionReason,
    RawSignalOutput,
)

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


# ── Yardımcı ─────────────────────────────────────────────────────────────────

def _raw(
    predicted_class="UP",
    raw_confidence=0.72,
    class_probabilities=None,
):
    return RawSignalOutput(
        asset="BTC",
        horizon_minutes=15,
        timestamp_utc=_NOW,
        predicted_class=predicted_class,
        raw_confidence=raw_confidence,
        class_probabilities=class_probabilities,
    )


# ── IdentityCalibrator ───────────────────────────────────────────────────────

class TestIdentityCalibrator:
    def test_transform_returns_raw_score(self):
        cal = IdentityCalibrator()
        assert cal.transform(0.72) == pytest.approx(0.72)

    def test_transform_does_not_clip(self):
        # identity hiçbir şey yapmaz — değeri olduğu gibi döndürür
        cal = IdentityCalibrator()
        assert cal.transform(0.95) == pytest.approx(0.95)
        assert cal.transform(0.10) == pytest.approx(0.10)

    def test_quality_is_unknown(self):
        # identity kalibrasyon kalitesi bilinmiyor — körlük değil, bilinçli proxy
        assert IdentityCalibrator.quality == CalibrationQuality.UNKNOWN

    def test_fit_is_noop(self):
        cal = IdentityCalibrator()
        cal.fit([0.6, 0.7, 0.8], [0.0, 1.0, 1.0])
        assert cal.transform(0.65) == pytest.approx(0.65)  # değişmedi

    def test_diagnostics_have_no_metrics(self):
        d = IdentityCalibrator().diagnostics()
        assert d["brier_score"] is None
        assert d["log_loss"] is None
        assert d["ece"] is None

    def test_raw_confidence_is_not_same_as_calibrated_probability(self):
        """
        Bu test tasarımsal bir belgelemedir.
        Identity, ham güveni olduğu gibi geçirir — bu bir yaklaşımdır.
        Kalibre edilmiş bir kalibratör farklı değer üretir.
        """
        raw_conf = 0.72
        cal = IdentityCalibrator()
        calibrated = cal.transform(raw_conf)

        # Identity passthrough: kalibre edilmiş değer = ham değer
        assert calibrated == pytest.approx(raw_conf)
        # NOT: calibrated == raw_conf yalnızca identity için geçerli.
        # Gerçek kalibrasyon sonrası bu eşitlik kırılır.


# ── PlattCalibrator ──────────────────────────────────────────────────────────

class TestPlattCalibrator:
    def _synthetic_data(self, n=100, seed=42):
        """Sentetik ikili sınıflandırma verisi."""
        rng = __import__("random")
        rng.seed(seed)
        scores = [rng.uniform(0.3, 0.9) for _ in range(n)]
        y_true = [1.0 if s > 0.55 else 0.0 for s in scores]
        return scores, y_true

    def test_fit_transform_reasonable(self):
        scores, y_true = self._synthetic_data()
        cal = PlattCalibrator()
        cal.fit(scores, y_true)
        # Transform sonucu [0, 1] aralığında
        p = cal.transform(0.7)
        assert 0.0 < p < 1.0

    def test_transform_before_fit_raises(self):
        cal = PlattCalibrator()
        with pytest.raises(RuntimeError, match="fit\\(\\) öncesinde"):
            cal.transform(0.5)

    def test_fit_requires_data(self):
        # Çok az veri ile fit edilse bile hata vermemeli (monotonic değil ama çalışır)
        cal = PlattCalibrator()
        cal.fit([0.3, 0.7], [0.0, 1.0])
        assert 0.0 < cal.transform(0.5) < 1.0

    def test_diagnostics_after_fit(self):
        scores, y_true = self._synthetic_data()
        cal = PlattCalibrator()
        cal.fit(scores, y_true)
        d = cal.diagnostics()
        assert d["brier_score"] is not None
        assert d["log_loss"] is not None
        assert d["ece"] is not None
        assert 0.0 <= d["brier_score"] <= 1.0

    def test_platt_differs_from_identity_after_fit(self):
        """Fit sonrası Platt, identity'den farklı değer üretir (A != 1 veya B != 0 ise)."""
        scores, y_true = self._synthetic_data(n=200)
        cal = PlattCalibrator()
        cal.fit(scores, y_true)
        # En az bir noktada identity ile fark var
        diffs = [abs(cal.transform(s) - s) for s in scores[:20]]
        assert max(diffs) > 1e-6, "Platt identity'den farklılaşmalı"

    def test_get_calibrator_factory(self):
        cal = get_calibrator(CalibrationMethod.PLATT)
        assert isinstance(cal, PlattCalibrator)

    def test_get_calibrator_identity(self):
        cal = get_calibrator(CalibrationMethod.IDENTITY)
        assert isinstance(cal, IdentityCalibrator)

    def test_get_calibrator_unknown_raises(self):
        with pytest.raises((ValueError, AttributeError)):
            get_calibrator("nonexistent")  # type: ignore


# ── Diagnostik fonksiyonlar ──────────────────────────────────────────────────

class TestDiagnosticFunctions:
    def test_brier_score_perfect(self):
        y_true = [1.0, 0.0, 1.0, 0.0]
        y_prob = [1.0, 0.0, 1.0, 0.0]
        assert brier_score(y_true, y_prob) == pytest.approx(0.0)

    def test_brier_score_worst(self):
        y_true = [1.0, 0.0]
        y_prob = [0.0, 1.0]
        assert brier_score(y_true, y_prob) == pytest.approx(1.0)

    def test_brier_score_random(self):
        y_true = [1.0, 0.0, 1.0, 0.0]
        y_prob = [0.5, 0.5, 0.5, 0.5]
        assert brier_score(y_true, y_prob) == pytest.approx(0.25)

    def test_log_loss_reasonable(self):
        y_true = [1.0, 0.0, 1.0]
        y_prob = [0.9, 0.1, 0.8]
        ll = log_loss_score(y_true, y_prob)
        assert 0.0 < ll < 0.5

    def test_ece_perfect_calibration(self):
        # p_pred == p_actual → ECE ≈ 0
        y_prob = [i / 100.0 for i in range(101)]
        y_true = y_prob[:]  # gerçek olaylar tam olasılığa eşit (ideal)
        ece = expected_calibration_error(y_true, y_prob, n_bins=10)
        assert ece < 0.05


# ── ProbabilityMapper ────────────────────────────────────────────────────────

class TestProbabilityMapper:
    def _map(self, predicted_class="UP", confidence=0.72, polarity="NORMAL",
             up=0.72, down=0.18, no_trade=0.10):
        raw = _raw(predicted_class=predicted_class, raw_confidence=confidence)
        return map_to_event_probability(
            raw=raw,
            calibrated_up_prob=up,
            calibrated_down_prob=down,
            calibrated_no_trade_prob=no_trade,
            polarity=polarity,
            calibration_method=CalibrationMethod.IDENTITY,
            calibration_quality=CalibrationQuality.UNKNOWN,
        )

    def test_up_normal_effective_yes_is_up_prob(self):
        cal, err = self._map("UP", polarity="NORMAL", up=0.72, down=0.18)
        assert err is None
        assert cal.effective_yes_prob == pytest.approx(0.72)

    def test_up_normal_effective_no_is_down_prob(self):
        cal, err = self._map("UP", polarity="NORMAL", up=0.72, down=0.18)
        assert err is None
        assert cal.effective_no_prob == pytest.approx(0.18)

    def test_down_normal_effective_yes_is_up_prob(self):
        # NORMAL polarity: effective_yes = up_prob ALWAYS (predicted_class bağımsız)
        cal, err = self._map("DOWN", polarity="NORMAL", up=0.20, down=0.65)
        assert err is None
        assert cal.effective_yes_prob == pytest.approx(0.20)
        assert cal.effective_no_prob  == pytest.approx(0.65)

    def test_up_inverted_effective_yes_is_down_prob(self):
        # INVERTED: UP → NO, DOWN → YES
        # UP signal, INVERTED market → P(YES) = P(DOWN)
        cal, err = self._map("UP", polarity="INVERTED", up=0.72, down=0.18)
        assert err is None
        assert cal.effective_yes_prob == pytest.approx(0.18)
        assert cal.effective_no_prob  == pytest.approx(0.72)

    def test_down_inverted_effective_yes_is_down_prob(self):
        # INVERTED polarity: effective_yes = down_prob ALWAYS (predicted_class bağımsız)
        cal, err = self._map("DOWN", polarity="INVERTED", up=0.20, down=0.65)
        assert err is None
        assert cal.effective_yes_prob == pytest.approx(0.65)
        assert cal.effective_no_prob  == pytest.approx(0.20)

    def test_no_trade_signal_rejected(self):
        cal, err = self._map("NO_TRADE", polarity="NORMAL")
        assert cal is None
        assert err == CalibrationRejectionReason.NO_TRADE_SIGNAL

    def test_ambiguous_polarity_rejected(self):
        cal, err = self._map("UP", polarity="AMBIGUOUS")
        assert cal is None
        assert err == CalibrationRejectionReason.AMBIGUOUS_MAPPING

    def test_unknown_polarity_rejected(self):
        cal, err = self._map("UP", polarity="WEIRD_VALUE")
        assert cal is None
        assert err == CalibrationRejectionReason.AMBIGUOUS_MAPPING

    def test_inconsistent_probabilities_rejected(self):
        # toplam > 1 + tolerans → REJECT
        cal, err = self._map("UP", polarity="NORMAL", up=0.70, down=0.50, no_trade=0.20)
        assert cal is None
        assert err == CalibrationRejectionReason.INCONSISTENT_PROBS

    def test_mapping_context_describes_direction(self):
        cal, err = self._map("UP", polarity="NORMAL")
        assert err is None
        assert "NORMAL" in cal.mapping_context or "YES" in cal.mapping_context

    def test_calibration_method_preserved(self):
        cal, err = self._map("UP", polarity="NORMAL")
        assert err is None
        assert cal.calibration_method == CalibrationMethod.IDENTITY

    def test_calibration_quality_preserved(self):
        cal, err = self._map("UP", polarity="NORMAL")
        assert err is None
        assert cal.calibration_quality == CalibrationQuality.UNKNOWN

    def test_no_silent_fallback_on_invalid_predicted_class(self):
        # Phase 10: RawSignalOutput.__post_init__ now rejects invalid predicted_class
        # at construction time — before mapper runs.
        with pytest.raises(ValueError, match="predicted_class"):
            _raw(predicted_class="SIDEWAYS", raw_confidence=0.72)

    # ── bridge_intent_side ────────────────────────────────────────────────────

    def test_bridge_intent_normal_up_is_yes(self):
        cal, err = self._map("UP", polarity="NORMAL")
        assert err is None
        assert cal.bridge_intent_side == "YES"

    def test_bridge_intent_normal_down_is_no(self):
        cal, err = self._map("DOWN", polarity="NORMAL", up=0.20, down=0.65)
        assert err is None
        assert cal.bridge_intent_side == "NO"

    def test_bridge_intent_inverted_up_is_no(self):
        cal, err = self._map("UP", polarity="INVERTED", up=0.72, down=0.18)
        assert err is None
        assert cal.bridge_intent_side == "NO"

    def test_bridge_intent_inverted_down_is_yes(self):
        cal, err = self._map("DOWN", polarity="INVERTED", up=0.20, down=0.65)
        assert err is None
        assert cal.bridge_intent_side == "YES"

    # ── Strict probability validation ────────────────────────────────────────

    def test_negative_up_probability_rejected(self):
        cal, err = self._map("UP", polarity="NORMAL", up=-0.10, down=0.50, no_trade=0.10)
        assert cal is None
        assert err == CalibrationRejectionReason.INCONSISTENT_PROBS

    def test_probability_greater_than_one_rejected(self):
        cal, err = self._map("UP", polarity="NORMAL", up=1.10, down=0.00, no_trade=0.00)
        assert cal is None
        assert err == CalibrationRejectionReason.INCONSISTENT_PROBS

    def test_negative_down_probability_rejected(self):
        cal, err = self._map("UP", polarity="NORMAL", up=0.70, down=-0.05, no_trade=0.10)
        assert cal is None
        assert err == CalibrationRejectionReason.INCONSISTENT_PROBS
