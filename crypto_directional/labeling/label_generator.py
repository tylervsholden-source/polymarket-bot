"""
crypto_directional/labeling/label_generator.py

Leakage-free label üretimi: UP / DOWN / NO_TRADE

KURAL:
  - future_return ve mid_price_t{H} kolonları ASLA feature vektörüne dahil edilmez.
  - Bu dosya yalnızca label tablosu üretir; feature hesabı ayrıdır.
  - Son N bar NO_DATA döner (gelecek henüz gerçekleşmemiş).
  - Execution timing (SPEC.md §5.3): giriş t_close anında; label da bu anı referans alır.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np
import pandas as pd

from crypto_directional.config.settings import settings

_INTERVAL_MINUTES: dict[str, int] = {
    "1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240,
}


def generate_labels(
    df: pd.DataFrame,
    bar_interval: str = "5m",
    horizons: Sequence[str] = ("5m", "15m"),
    strict_interval: bool = False,
) -> pd.DataFrame:
    """
    Label tablosu üretir.

    Parametreler
    ------------
    df : DataFrame
        Zorunlu kolonlar: open_time (int64 ms UTC), mid_price (float64)
        Satırlar kronolojik sıraya göre artan open_time ile sıralı olmalı.
        mid_price = (best_bid + best_ask) / 2 — bar kapanış anındaki değer.
    bar_interval : str
        Bar granülaritesi: "1m", "5m", "15m", ...
    horizons : sequence of str
        Tahmin horizonları, örn. ["5m", "15m"]

    Döndürür
    --------
    DataFrame kolonları:
        open_time          int64
        mid_price_t        float64    — t anındaki giriş referans fiyatı
        mid_price_t{H}     float64    — t+H anındaki çıkış referans fiyatı
        future_return_{H}  float64    — (mid_t+H - mid_t) / mid_t  [ASLA feature'a dahil etme]
        label_{H}          str        — UP / DOWN / NO_TRADE / NO_DATA
        threshold_{H}      float64    — kullanılan eşik (config'den gelir)

    ÖNEMLİ: future_return_{H} ve mid_price_t{H} kolonları hiçbir zaman
    feature vektörüne dahil edilmez. Sadece label üretimi ve analiz içindir.
    """
    _validate(df, bar_interval=bar_interval, strict_interval=strict_interval)

    mid = df["mid_price"].reset_index(drop=True)

    out = pd.DataFrame()
    out["open_time"]  = df["open_time"].values
    out["mid_price_t"] = mid.values

    for horizon in horizons:
        n_bars    = _bars_ahead(horizon, bar_interval)
        threshold = settings.label_threshold(horizon)

        # shift(-n): satır t'de t+n'in değerini verir.
        # Bu leakage DEĞİLDİR — yalnızca label hesabı içindir; feature'a sızdırılmaz.
        future_mid    = mid.shift(-n_bars)
        future_return = (future_mid - mid) / mid

        out[f"mid_price_t{horizon}"]    = future_mid.values
        out[f"future_return_{horizon}"] = future_return.values
        out[f"label_{horizon}"]         = _classify(future_return, threshold).values
        out[f"threshold_{horizon}"]     = threshold

    return out


def _classify(returns: pd.Series, threshold: float) -> pd.Series:
    """
    Vektörize sınıflandırma.
    NaN (son N bar — gelecek yok) → "NO_DATA"
    > +threshold                  → "UP"
    < -threshold                  → "DOWN"
    aksi                          → "NO_TRADE"

    Sınır: future_return == threshold tam eşit → NO_TRADE (> değil, >=).
    """
    result = np.where(
        returns.isna(),   "NO_DATA",
        np.where(returns > threshold,  "UP",
        np.where(returns < -threshold, "DOWN", "NO_TRADE")),
    )
    return pd.Series(result, index=returns.index, dtype=str)


def _bars_ahead(horizon: str, bar_interval: str) -> int:
    """Kaç bar ilerisi gerektiğini hesaplar."""
    h = _INTERVAL_MINUTES.get(horizon)
    b = _INTERVAL_MINUTES.get(bar_interval)
    if h is None:
        raise ValueError(f"Bilinmeyen horizon: {horizon!r}")
    if b is None:
        raise ValueError(f"Bilinmeyen bar_interval: {bar_interval!r}")
    if h % b != 0:
        raise ValueError(
            f"Horizon {horizon} ({h}m), bar_interval {bar_interval} ({b}m) ile "
            f"tam bölünmüyor."
        )
    return h // b


def _validate(
    df: pd.DataFrame,
    bar_interval: str = "5m",
    strict_interval: bool = False,
) -> None:
    import warnings

    required = {"open_time", "mid_price"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"label_generator: eksik kolonlar: {missing}")
    if df.empty:
        raise ValueError("label_generator: boş DataFrame.")

    diffs = df["open_time"].diff().dropna()
    if not (diffs >= 0).all():
        raise ValueError(
            "label_generator: open_time artan sırada değil — önce sırala."
        )

    # Duplicate open_time — daima hata
    n_dup = int(df["open_time"].duplicated().sum())
    if n_dup > 0:
        raise ValueError(
            f"label_generator: {n_dup} adet duplicate open_time var. "
            f"Önce tekilleştir (drop_duplicates)."
        )

    # Bar aralığı kontrolü (biliniyorsa)
    bar_min = _INTERVAL_MINUTES.get(bar_interval)
    if bar_min is not None:
        expected_ms = bar_min * 60_000

        # Gap detection — daima uyarı
        gaps = int((diffs > expected_ms * 1.5).sum())
        if gaps > 0:
            warnings.warn(
                f"label_generator: {gaps} adet gap tespit edildi "
                f"(beklenen={expected_ms}ms, bar_interval={bar_interval!r}). "
                f"Eksik barlar shift(-n) horizon hesabını etkileyebilir.",
                UserWarning,
                stacklevel=4,
            )

        # Strict interval — tüm diffs tam eşit olmalı
        if strict_interval:
            bad = int((diffs != expected_ms).sum())
            if bad > 0:
                raise ValueError(
                    f"label_generator: strict_interval=True — "
                    f"{bad} adet düzensiz aralık "
                    f"(beklenen={expected_ms}ms, bar_interval={bar_interval!r})."
                )
