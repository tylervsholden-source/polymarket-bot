"""
crypto_directional/features/derivatives.py

Derivatives + Regime feature'ları — futures-specific.

Feature listesi:
    feat_funding_rate       D001  P1  lookback=instant   leakage=NONE
    feat_funding_8h_sum     D002  P2  lookback=~24h      leakage=NONE (approx)
    feat_oi_change_3b       D003  P1  lookback=3         leakage=NONE
    feat_oi_zscore_20       D004  P2  lookback=20        leakage=NONE
    feat_liq_imbalance      D005  P2  lookback=1 bar     leakage=NONE
    feat_btc_return_1b      R004  P2  lookback=1 bar     leakage=LOW (ETH modeli)

Giriş: ilgili kolonlar bar open_time üzerinden hizalanmış olmalı
(DATA_SCHEMA.md §1 alignment kuralı).

Warmup: 20 bar (oi_zscore)
Eksik kolon → ilgili feature NaN — exception değil.

NOT — feat_funding_8h_sum yaklaşımı:
    Binance funding 8 saatte bir güncellenir (96 adet 5m bar).
    Forward-fill edilmiş funding_rate üzerinden son 3 farklı periyot:
        shift(0) + shift(96) + shift(192)
    Bu bir yaklaşımdır; kesin hesap için funding_time pivot tablosu gerekir (Faz 3).
"""
from __future__ import annotations

import pandas as pd

_EPS              = 1e-10
_OI_ZSCORE_WINDOW = 20
_OI_CHANGE_BARS   = 3
_INTERVAL_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240}


def add_derivatives(df: pd.DataFrame, bar_interval: str = "5m") -> pd.DataFrame:
    """
    Tüm derivative ve regime feature'larını hesaplar.
    Eksik kolon → ilgili feature NaN.

    bar_interval : str
        Bar granülaritesi ("1m", "5m", "15m", ...).
        feat_funding_8h_sum hesabında kullanılır — hardcoded 5m varsayımı yok.
    """
    bar_min     = _INTERVAL_MINUTES.get(bar_interval, 5)
    bars_per_8h = (8 * 60) // bar_min   # 8h / bar uzunluğu

    # D001 — funding rate (forward-fill ile bar bazına indirilmiş olmalı)
    if "funding_rate" in df.columns:
        df["feat_funding_rate"] = df["funding_rate"]
    else:
        df["feat_funding_rate"] = float("nan")

    # D002 — 8h funding sum (son 3 periyot ≈ 24h, yaklaşık)
    # bars_per_8h bar_interval'e göre dinamik hesaplanır.
    if "funding_rate" in df.columns:
        fr = df["funding_rate"]
        df["feat_funding_8h_sum"] = (
            fr
            + fr.shift(bars_per_8h)
            + fr.shift(2 * bars_per_8h)
        )
    else:
        df["feat_funding_8h_sum"] = float("nan")

    # D003 — OI change (3 bar)
    if "open_interest_usdt" in df.columns:
        oi = df["open_interest_usdt"]
        df["feat_oi_change_3b"] = (
            (oi - oi.shift(_OI_CHANGE_BARS)) / (oi.shift(_OI_CHANGE_BARS) + _EPS)
        )
    else:
        df["feat_oi_change_3b"] = float("nan")

    # D004 — OI z-score
    if "open_interest_usdt" in df.columns:
        oi        = df["open_interest_usdt"]
        roll_mean = oi.rolling(_OI_ZSCORE_WINDOW).mean()
        roll_std  = oi.rolling(_OI_ZSCORE_WINDOW).std(ddof=1)
        df["feat_oi_zscore_20"] = (oi - roll_mean) / (roll_std + _EPS)
    else:
        df["feat_oi_zscore_20"] = float("nan")

    # D005 — liquidation imbalance
    if {"long_liq_volume", "short_liq_volume"}.issubset(df.columns):
        ll = df["long_liq_volume"]
        sl = df["short_liq_volume"]
        df["feat_liq_imbalance"] = (ll - sl) / (ll + sl + _EPS)
    else:
        df["feat_liq_imbalance"] = float("nan")

    # R004 — BTC return 1b (sadece ETH modeli için anlamlı)
    # Dikkat: kapanmış barın BTC fiyatı kullanılır; t+1 dahil değil
    if "btc_close" in df.columns:
        btc = df["btc_close"]
        df["feat_btc_return_1b"] = (btc - btc.shift(1)) / (btc.shift(1) + _EPS)
    else:
        df["feat_btc_return_1b"] = float("nan")

    return df
