"""
crypto_directional/features/momentum.py

Momentum feature'ları — tümü OHLCV tabanlı.

Feature listesi:
    feat_return_1b         M001  P1  lookback=1
    feat_return_3b         M002  P1  lookback=3
    feat_return_5b         M002  P1  lookback=5
    feat_return_10b        M002  P1  lookback=10
    feat_return_20b        M002  P1  lookback=20
    feat_ema_cross_5_20    M003  P1  lookback=20
    feat_ema_cross_9_21    M003  P1  lookback=21
    feat_rsi_14            M004  P1  lookback=14  warmup=14
    feat_macd_hist_norm    M005  P2  lookback=34
    feat_ema_slope_9       M006  P2  lookback=12

Leakage: NONE — tüm hesaplar [t-N, t] aralığı; shift(-) kullanılmaz.
Warmup: ilk period bar NaN (RSI), EMA başlangıçta partial (EWM davranışı).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def add_momentum(df: pd.DataFrame) -> pd.DataFrame:
    """
    Momentum feature'larını hesaplar ve df'e ekler.

    Giriş kolonları: open, high, low, close, volume
    Çıkış: feat_* kolonları eklenir, orijinal kolonlar korunur.
    """
    close = df["close"]
    open_ = df["open"]

    # M001 — tek bar içi getiri: kapanış / açılış
    df["feat_return_1b"] = (close - open_) / open_

    # M002 — N bar geriye kapanış getirisi
    for n in (3, 5, 10, 20):
        df[f"feat_return_{n}b"] = (close - close.shift(n)) / close.shift(n)

    # M003 — EMA çaprazları (normalize)
    ema5  = close.ewm(span=5,  adjust=False).mean()
    ema9  = close.ewm(span=9,  adjust=False).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()

    df["feat_ema_cross_5_20"] = (ema5  - ema20) / close
    df["feat_ema_cross_9_21"] = (ema9  - ema21) / close

    # M004 — RSI 14 (Wilder EWM tabanlı)
    df["feat_rsi_14"] = _rsi(close, period=14)

    # M005 — MACD histogram (normalized)
    _, _, hist = _macd(close, fast=12, slow=26, signal=9)
    df["feat_macd_hist_norm"] = hist / close

    # M006 — EMA9 slope: (EMA9[t] - EMA9[t-3]) / (EMA9[t-3] * 3)
    ema9_lag3 = ema9.shift(3)
    df["feat_ema_slope_9"] = (ema9 - ema9_lag3) / (ema9_lag3 * 3)

    return df


# ── Private helpers ───────────────────────────────────────────────────────────

def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """
    Wilder RSI: EWM alpha=1/period (adjust=False).
    İlk `period` bar NaN döner (warmup).
    avg_loss == 0 → RSI = 100 (tam overbought).
    """
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)

    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()

    rs  = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))

    # Warmup maskesi: ilk `period` bar güvenilmez
    rsi.iloc[:period] = float("nan")
    return rsi


def _macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD line, signal line, histogram."""
    ema_fast    = close.ewm(span=fast,   adjust=False).mean()
    ema_slow    = close.ewm(span=slow,   adjust=False).mean()
    macd_line   = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram   = macd_line - signal_line
    return macd_line, signal_line, histogram
