"""
calibration/probability_mapper.py

Directional model çıktısını Polymarket olay olasılığına dönüştürür.

Eşleştirme tablosu
------------------
effective_yes_prob ve effective_no_prob YALNIZCA polarity'ye bağlıdır.
predicted_class (UP/DOWN) bu eşleştirmeyi ETKİLEMEZ.

Polarity    | effective_yes_prob    | effective_no_prob
------------+----------------------+-------------------
NORMAL      | calibrated_up_prob   | calibrated_down_prob
INVERTED    | calibrated_down_prob | calibrated_up_prob

AMBIGUOUS polarity → REJECT.
NO_TRADE predicted_class → REJECT.

Bu dosya polarity'yi string olarak alır — signal_bridge.types'a bağımlı değildir.
Geçerli polarity stringleri: "NORMAL", "INVERTED", "AMBIGUOUS"
"""
from __future__ import annotations

from calibration.types import (
    CalibratedSignal,
    CalibrationMethod,
    CalibrationQuality,
    CalibrationRejectionReason,
    PROB_MAX_OVER,
    PROB_MIN_SUM,
    RawSignalOutput,
)


def map_to_event_probability(
    raw: RawSignalOutput,
    calibrated_up_prob: float,
    calibrated_down_prob: float,
    calibrated_no_trade_prob: float,
    polarity: str,
    calibration_method: CalibrationMethod,
    calibration_quality: CalibrationQuality,
    brier_score: float | None = None,
    log_loss_val: float | None = None,
    ece: float | None = None,
) -> tuple[CalibratedSignal | None, CalibrationRejectionReason | None]:
    """
    Kalibre edilmiş yön olasılıklarını Polymarket olay olasılığına dönüştürür.

    Parametreler
    ------------
    raw                  : ham sinyal (RawSignalOutput)
    calibrated_up_prob   : P(UP) kalibre
    calibrated_down_prob : P(DOWN) kalibre
    calibrated_no_trade_prob : P(NO_TRADE) kalibre
    polarity             : "NORMAL" | "INVERTED" | "AMBIGUOUS"
    calibration_method   : kullanılan yöntem
    calibration_quality  : kalite etiketi
    brier_score / log_loss_val / ece : opsiyonel kalibrasyon metrikleri

    Döndürür
    --------
    (CalibratedSignal, None)  — başarılı
    (None, RejectionReason)   — reddedildi
    """
    # 1. NO_TRADE → REJECT
    if raw.predicted_class == "NO_TRADE":
        return None, CalibrationRejectionReason.NO_TRADE_SIGNAL

    # 2. Belirsiz polarity → REJECT
    if polarity == "AMBIGUOUS":
        return None, CalibrationRejectionReason.AMBIGUOUS_MAPPING

    # 3. Desteklenmeyen polarity değeri
    if polarity not in ("NORMAL", "INVERTED"):
        return None, CalibrationRejectionReason.AMBIGUOUS_MAPPING

    # 4. Desteklenmeyen predicted_class (UP veya DOWN dışında)
    if raw.predicted_class not in ("UP", "DOWN"):
        return None, CalibrationRejectionReason.NO_TRADE_SIGNAL

    # 5. Olasılık katı doğrulaması
    for p in (calibrated_up_prob, calibrated_down_prob, calibrated_no_trade_prob):
        if p < 0.0 or p > 1.0:
            return None, CalibrationRejectionReason.INCONSISTENT_PROBS
    total = calibrated_up_prob + calibrated_down_prob + calibrated_no_trade_prob
    if total > 1.0 + PROB_MAX_OVER:
        return None, CalibrationRejectionReason.INCONSISTENT_PROBS
    # Eksik tanımlı olasılık uzayı: toplam < PROB_MIN_SUM → normalize etme, reject et.
    # (örn. up=0.20, down=0.10, no_trade=0.05 → toplam=0.35; anlamlı edge hesabı yapılamaz)
    if total < PROB_MIN_SUM:
        return None, CalibrationRejectionReason.INCONSISTENT_PROBS

    # 6. Polarity eşleştirmesi
    #    NORMAL: YES = fiyat yükseldi (UP), NO = fiyat düştü (DOWN)
    #    INVERTED: YES = fiyat düştü (DOWN), NO = fiyat yükseldi (UP)
    #
    # bridge_intent_side: karar katmanının YALNIZCA değerlendireceği taraf.
    #   NORMAL  + UP   → YES,  NORMAL  + DOWN → NO
    #   INVERTED + DOWN → YES, INVERTED + UP   → NO
    if polarity == "NORMAL":
        effective_yes_prob  = calibrated_up_prob
        effective_no_prob   = calibrated_down_prob
        bridge_intent_side  = "YES" if raw.predicted_class == "UP" else "NO"
        mapping_context = (
            f"{raw.predicted_class}→{bridge_intent_side} "
            f"(NORMAL polarity) | "
            f"eff_yes={calibrated_up_prob:.3f} eff_no={calibrated_down_prob:.3f}"
        )
    else:  # INVERTED
        effective_yes_prob  = calibrated_down_prob
        effective_no_prob   = calibrated_up_prob
        bridge_intent_side  = "YES" if raw.predicted_class == "DOWN" else "NO"
        mapping_context = (
            f"{raw.predicted_class}→{bridge_intent_side} "
            f"(INVERTED polarity) | "
            f"eff_yes={calibrated_down_prob:.3f} eff_no={calibrated_up_prob:.3f}"
        )

    cal_signal = CalibratedSignal(
        raw=raw,
        calibrated_up_prob=calibrated_up_prob,
        calibrated_down_prob=calibrated_down_prob,
        calibrated_no_trade_prob=calibrated_no_trade_prob,
        calibration_method=calibration_method,
        calibration_quality=calibration_quality,
        effective_yes_prob=effective_yes_prob,
        effective_no_prob=effective_no_prob,
        mapping_context=mapping_context,
        bridge_intent_side=bridge_intent_side,
        brier_score=brier_score,
        log_loss_val=log_loss_val,
        ece=ece,
    )
    return cal_signal, None
