"""
crypto_directional/features/volatility.py

Volatility + Regime feature'ları.

Feature listesi:
    feat_realized_vol_10    V001  P1  lookback=10
    feat_realized_vol_20    V001  P1  lookback=20
    feat_realized_vol_50    V001  P1  lookback=50
    feat_vol_ratio_5_20     V002  P1  lookback=20
    feat_atr_14_norm        V003  P2  lookback=14
    feat_intrabar_range     V004  P2  lookback=1
    feat_vol_regime         R001  P1  lookback=100  (percentile rank of realized_vol_20)
    feat_adx_14             R002  P2  lookback=28

Leakage: NONE — tüm hesaplar [t-N, t] aralığı.
Warmup:
    - realized_vol_N: ilk N bar NaN (log_ret'in ilk bar'ı da NaN)
    - vol_regime: ilk 99 bar NaN (rolling 100, min_periods=100)
    - adx_14: EWM warmup ≈ 28 bar
"""
from __future__ import annotations

import numpy as np
import pandas as pd

_EPS = 1e-10


def add_volatility(df: pd.DataFrame) -> pd.DataFrame:
    """
    Giriş kolonları: close, high, low
    """
    close = df["close"]
    high  = df["high"]
    low   = df["low"]

    log_ret = np.log(close / close.shift(1))

    # V001 — realized volatility (log return std)
    for n in (10, 20, 50):
        df[f"feat_realized_vol_{n}"] = log_ret.rolling(n).std(ddof=1)

    # V002 — volatility ratio short/long
    vol5  = log_ret.rolling(5).std(ddof=1)
    vol20 = df["feat_realized_vol_20"]
    df["feat_vol_ratio_5_20"] = vol5 / (vol20 + _EPS)

    # V003 — ATR(14) normalized
    df["feat_atr_14_norm"] = _atr(high, low, close, period=14) / (close + _EPS)

    # V004 — intrabar range
    df["feat_intrabar_range"] = (high - low) / (close + _EPS)

    # R001 — volatility regime (percentile rank, window=100)
    # 0 = low (<33p), 1 = mid (33-66p), 2 = high (>66p)
    # min_periods=100: ilk 99 bar NaN
    pct_rank = vol20.rolling(100, min_periods=100).rank(pct=True)
    df["feat_vol_regime"] = np.where(
        pct_rank.isna(),   float("nan"),
        np.where(pct_rank < 0.33, 0,
        np.where(pct_rank < 0.66, 1, 2)),
    )

    # R002 — ADX(14)
    adx, di_plus, di_minus = _adx(high, low, close, period=14)
    df["feat_adx_14"]      = adx
    df["feat_di_plus_14"]  = di_plus
    df["feat_di_minus_14"] = di_minus

    return df


# ── Private helpers ───────────────────────────────────────────────────────────

def _atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int,
) -> pd.Series:
    """ATR via Wilder EWM (alpha = 1/period)."""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    ADX, DI+, DI- — Wilder EWM smoothing.
    ADX > 25 = trending, < 20 = choppy market.
    """
    prev_high  = high.shift(1)
    prev_low   = low.shift(1)
    prev_close = close.shift(1)

    # True Range
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    # Directional movement
    up_move   = high - prev_high
    down_move = prev_low - low

    dm_plus  = pd.Series(
        np.where((up_move > down_move) & (up_move > 0),   up_move,   0.0),
        index=high.index,
    )
    dm_minus = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=high.index,
    )

    alpha = 1 / period
    atr_s    = tr.ewm(alpha=alpha, adjust=False).mean()
    di_plus  = 100 * dm_plus.ewm(alpha=alpha, adjust=False).mean()  / (atr_s + _EPS)
    di_minus = 100 * dm_minus.ewm(alpha=alpha, adjust=False).mean() / (atr_s + _EPS)

    dx  = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus + _EPS)
    adx = dx.ewm(alpha=alpha, adjust=False).mean()

    return adx, di_plus, di_minus
