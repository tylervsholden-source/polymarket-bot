"""
crypto_directional/features/mean_reversion.py

Mean reversion feature'ları.

Feature listesi:
    feat_zscore_close_20    MR001  P1  lookback=20  leakage=NONE
    feat_bb_position_20     MR002  P1  lookback=20  leakage=NONE
    feat_price_dev_ema_21   MR003  P2  lookback=21  leakage=NONE
    feat_price_vs_vwap      MR004  P2  lookback=20  leakage=LOW (rolling window, oturum değil)

Warmup:
    - zscore, BB: ilk 19 bar NaN (rolling 20, ilk tam pencere index 19)
    - EMA21: EWM partial — başlangıçta güvenilmezlik düşük
    - VWAP: ilk 19 bar NaN (rolling 20)
"""
from __future__ import annotations

import pandas as pd

_BB_WINDOW   = 20
_VWAP_WINDOW = 20
_EMA21_SPAN  = 21
_EPS = 1e-10


def add_mean_reversion(df: pd.DataFrame) -> pd.DataFrame:
    """
    Giriş kolonları: close, high, low, volume
    """
    close  = df["close"]
    high   = df.get("high",   close)   # yedek: high yoksa close kullan (test kolaylığı)
    low    = df.get("low",    close)
    volume = df.get("volume", pd.Series(1.0, index=close.index))

    # MR001 — z-score (rolling 20)
    roll_mean = close.rolling(_BB_WINDOW).mean()
    roll_std  = close.rolling(_BB_WINDOW).std(ddof=1)
    df["feat_zscore_close_20"] = (close - roll_mean) / (roll_std + _EPS)

    # MR002 — Bollinger Band pozisyonu [0, 1]
    bb_upper   = roll_mean + 2 * roll_std
    bb_lower   = roll_mean - 2 * roll_std
    band_width = bb_upper - bb_lower
    df["feat_bb_position_20"] = (close - bb_lower) / (band_width + _EPS)

    # MR003 — fiyat / EMA21 sapması
    ema21 = close.ewm(span=_EMA21_SPAN, adjust=False).mean()
    df["feat_price_dev_ema_21"] = (close - ema21) / (ema21 + _EPS)

    # MR004 — fiyat / rolling VWAP sapması
    # Rolling VWAP: [t-N, t] aralığı; oturum VWAP DEĞİL
    typical_price = (high + low + close) / 3
    tp_vol = typical_price * volume
    vwap = (
        tp_vol.rolling(_VWAP_WINDOW).sum()
        / (volume.rolling(_VWAP_WINDOW).sum() + _EPS)
    )
    df["feat_price_vs_vwap"] = (close - vwap) / (vwap + _EPS)

    return df
