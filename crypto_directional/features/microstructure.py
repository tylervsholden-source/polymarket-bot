"""
crypto_directional/features/microstructure.py

Microstructure feature'ları — OB snapshot + taker flow tabanlı.

Feature listesi:
    feat_ob_imbalance_5     MS001  P1  lookback=instant  leakage=NONE
    feat_ob_imbalance_10    MS001  P1  lookback=instant  leakage=NONE
    feat_spread_pct         MS002  P1  lookback=instant  leakage=NONE
    feat_spread_zscore_20   MS003  P2  lookback=20       leakage=NONE
    feat_taker_buy_ratio    MS004  P1  lookback=1 bar    leakage=NONE
    feat_volume_ratio_20    MS005  P1  lookback=20       leakage=NONE

Giriş: OB ve taker veri, bar open_time üzerinden OHLCV ile hizalanmış olmalı
(DATA_SCHEMA.md §1 timestamp alignment kuralı).

Warmup: 20 bar (spread_zscore, volume_ratio)
Eksik kolon → ilgili feature NaN — exception değil.
"""
from __future__ import annotations

import pandas as pd

_EPS            = 1e-10
_ZSCORE_WINDOW  = 20
_VOL_RATIO_WINDOW = 20


def add_microstructure(df: pd.DataFrame) -> pd.DataFrame:
    """
    Giriş kolonları (opsiyonel — yoksa feature NaN):
        bid_depth_5, ask_depth_5    → feat_ob_imbalance_5
        bid_depth_10, ask_depth_10  → feat_ob_imbalance_10
        best_bid, best_ask, mid_price → feat_spread_pct, feat_spread_zscore_20
        taker_buy_volume, volume    → feat_taker_buy_ratio
        volume                      → feat_volume_ratio_20
    """
    # MS001 — OB imbalance (5 level)
    if {"bid_depth_5", "ask_depth_5"}.issubset(df.columns):
        bid5 = df["bid_depth_5"]
        ask5 = df["ask_depth_5"]
        df["feat_ob_imbalance_5"] = (bid5 - ask5) / (bid5 + ask5 + _EPS)
    else:
        df["feat_ob_imbalance_5"] = float("nan")

    # MS001 — OB imbalance (10 level)
    if {"bid_depth_10", "ask_depth_10"}.issubset(df.columns):
        bid10 = df["bid_depth_10"]
        ask10 = df["ask_depth_10"]
        df["feat_ob_imbalance_10"] = (bid10 - ask10) / (bid10 + ask10 + _EPS)
    else:
        df["feat_ob_imbalance_10"] = float("nan")

    # MS002 — spread pct + MS003 — spread z-score
    if {"best_bid", "best_ask", "mid_price"}.issubset(df.columns):
        spread_abs = df["best_ask"] - df["best_bid"]
        mid        = df["mid_price"]
        spread_pct = spread_abs / (mid + _EPS)
        df["feat_spread_pct"] = spread_pct

        roll_mean = spread_pct.rolling(_ZSCORE_WINDOW).mean()
        roll_std  = spread_pct.rolling(_ZSCORE_WINDOW).std(ddof=1)
        df["feat_spread_zscore_20"] = (spread_pct - roll_mean) / (roll_std + _EPS)
    else:
        df["feat_spread_pct"]       = float("nan")
        df["feat_spread_zscore_20"] = float("nan")

    # MS004 — taker buy ratio
    if {"taker_buy_volume", "volume"}.issubset(df.columns):
        df["feat_taker_buy_ratio"] = df["taker_buy_volume"] / (df["volume"] + _EPS)
    else:
        df["feat_taker_buy_ratio"] = float("nan")

    # MS005 — volume ratio: current bar vs rolling mean of PREVIOUS bars
    # Kritik: shift(1) ile t'yi ortalamadan dışarıda bırak (FEATURE_CATALOG MS005)
    if "volume" in df.columns:
        prev_vol_mean = df["volume"].shift(1).rolling(_VOL_RATIO_WINDOW).mean()
        df["feat_volume_ratio_20"] = df["volume"] / (prev_vol_mean + _EPS)
    else:
        df["feat_volume_ratio_20"] = float("nan")

    return df
