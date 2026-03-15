"""
crypto_directional/tests/test_labeling.py

Label generator için kapsamlı birim testler.
Odak: sınıflandırma doğruluğu, leakage yokluğu, edge case'ler.
"""
import math

import numpy as np
import pandas as pd
import pytest

from crypto_directional.config.settings import settings
from crypto_directional.labeling.label_generator import (
    _bars_ahead,
    _classify,
    generate_labels,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_df(n: int = 20, mid_prices=None) -> pd.DataFrame:
    """Test için basit DataFrame."""
    if mid_prices is None:
        mid_prices = [100.0 + i * 0.1 for i in range(n)]
    return pd.DataFrame({
        "open_time": [1_000_000 + i * 300_000 for i in range(n)],  # 5m aralık
        "mid_price": mid_prices,
    })


# ── _bars_ahead ───────────────────────────────────────────────────────────────

def test_bars_ahead_5m_on_5m():
    assert _bars_ahead("5m", "5m") == 1


def test_bars_ahead_15m_on_5m():
    assert _bars_ahead("15m", "5m") == 3


def test_bars_ahead_1h_on_5m():
    assert _bars_ahead("1h", "5m") == 12


def test_bars_ahead_15m_on_15m():
    assert _bars_ahead("15m", "15m") == 1


def test_bars_ahead_invalid_division():
    with pytest.raises(ValueError, match="tam bölünmüyor"):
        _bars_ahead("15m", "4h")   # 15m < 4h = 240m


def test_bars_ahead_unknown_horizon():
    with pytest.raises(ValueError, match="Bilinmeyen horizon"):
        _bars_ahead("7m", "5m")


# ── generate_labels — temel şema ──────────────────────────────────────────────

def test_output_columns_present():
    df  = _make_df(10)
    out = generate_labels(df, bar_interval="5m", horizons=["5m"])
    for col in ("open_time", "mid_price_t", "mid_price_t5m",
                "future_return_5m", "label_5m", "threshold_5m"):
        assert col in out.columns, f"Eksik kolon: {col}"


def test_both_horizons_columns():
    df  = _make_df(10)
    out = generate_labels(df, bar_interval="5m", horizons=["5m", "15m"])
    for h in ("5m", "15m"):
        assert f"label_{h}"          in out.columns
        assert f"future_return_{h}"  in out.columns
        assert f"threshold_{h}"      in out.columns


def test_row_count_preserved():
    df  = _make_df(20)
    out = generate_labels(df, bar_interval="5m")
    assert len(out) == len(df)


def test_mid_price_t_equals_input():
    df  = _make_df(10)
    out = generate_labels(df, bar_interval="5m", horizons=["5m"])
    pd.testing.assert_series_equal(
        out["mid_price_t"].reset_index(drop=True),
        df["mid_price"].reset_index(drop=True),
        check_names=False,
    )


def test_no_feat_columns_in_output():
    """Label tablosu hiçbir feat_ kolonu içermemeli."""
    df  = _make_df(10)
    out = generate_labels(df, bar_interval="5m")
    feat_cols = [c for c in out.columns if c.startswith("feat_")]
    assert len(feat_cols) == 0, f"feat_ kolon var: {feat_cols}"


def test_open_time_preserved():
    df  = _make_df(10)
    out = generate_labels(df, bar_interval="5m", horizons=["5m"])
    pd.testing.assert_series_equal(
        out["open_time"].reset_index(drop=True),
        df["open_time"].reset_index(drop=True),
        check_names=False,
    )


# ── NO_DATA (son N bar) ───────────────────────────────────────────────────────

def test_last_bar_nodata_5m():
    """5m horizon, 5m bar → son 1 satır NO_DATA."""
    df  = _make_df(10)
    out = generate_labels(df, bar_interval="5m", horizons=["5m"])
    assert out["label_5m"].iloc[-1] == "NO_DATA"
    assert pd.isna(out["future_return_5m"].iloc[-1])
    assert pd.isna(out["mid_price_t5m"].iloc[-1])


def test_last_3_bars_nodata_15m():
    """15m horizon, 5m bar → son 3 satır NO_DATA."""
    df  = _make_df(10)
    out = generate_labels(df, bar_interval="5m", horizons=["15m"])
    assert (out["label_15m"].iloc[-3:] == "NO_DATA").all()


def test_second_to_last_not_nodata_5m():
    """Son 1 bar NO_DATA; ikinci sondan itibaren geçerli label."""
    df  = _make_df(5)
    out = generate_labels(df, bar_interval="5m", horizons=["5m"])
    assert out["label_5m"].iloc[-2] != "NO_DATA"


# ── Label sınıflandırma ───────────────────────────────────────────────────────

def test_up_label():
    thr    = settings.label_threshold("5m")
    prices = [100.0, 100.0 * (1 + thr * 2)]   # t+1 kesin UP
    df     = _make_df(2, mid_prices=prices)
    out    = generate_labels(df, bar_interval="5m", horizons=["5m"])
    assert out["label_5m"].iloc[0] == "UP"


def test_down_label():
    thr    = settings.label_threshold("5m")
    prices = [100.0, 100.0 * (1 - thr * 2)]   # t+1 kesin DOWN
    df     = _make_df(2, mid_prices=prices)
    out    = generate_labels(df, bar_interval="5m", horizons=["5m"])
    assert out["label_5m"].iloc[0] == "DOWN"


def test_notrade_just_below_threshold():
    """
    future_return < threshold → NO_TRADE
    Tam eşit float hesabı floating point sapması yaratır (bkz. 100*(1+thr) round-trip).
    Bu test threshold'un %0.5 altını kullanır — güvenli bölge.
    """
    thr    = settings.label_threshold("5m")
    prices = [100.0, 100.0 * (1 + thr * 0.995)]   # threshold'un %99.5'i
    df     = _make_df(2, mid_prices=prices)
    out    = generate_labels(df, bar_interval="5m", horizons=["5m"])
    assert out["label_5m"].iloc[0] == "NO_TRADE"


def test_notrade_zero_return():
    prices = [100.0, 100.0]
    df     = _make_df(2, mid_prices=prices)
    out    = generate_labels(df, bar_interval="5m", horizons=["5m"])
    assert out["label_5m"].iloc[0] == "NO_TRADE"


def test_notrade_small_positive():
    thr    = settings.label_threshold("5m")
    prices = [100.0, 100.0 * (1 + thr * 0.5)]   # threshold'un yarısı
    df     = _make_df(2, mid_prices=prices)
    out    = generate_labels(df, bar_interval="5m", horizons=["5m"])
    assert out["label_5m"].iloc[0] == "NO_TRADE"


def test_threshold_from_config():
    """Threshold hardcoded değil, config'den geliyor."""
    df  = _make_df(5)
    out = generate_labels(df, horizons=["5m", "15m"])
    assert out["threshold_5m"].iloc[0]  == settings.label_threshold("5m")
    assert out["threshold_15m"].iloc[0] == settings.label_threshold("15m")


def test_future_return_formula():
    """future_return = (mid_t+1 - mid_t) / mid_t doğruluğu."""
    prices = [100.0, 102.0]   # +2%
    df     = _make_df(2, mid_prices=prices)
    out    = generate_labels(df, bar_interval="5m", horizons=["5m"])
    assert math.isclose(out["future_return_5m"].iloc[0], 0.02, rel_tol=1e-9)


# ── Leakage testleri ──────────────────────────────────────────────────────────

def test_no_leakage_label_t_unaffected_by_t2():
    """
    t+2 fiyatını değiştirmek t'nin label_5m'sini etkilememelidir.
    (5m horizon: t barının label'ı yalnızca t+1'e bakar)
    """
    df   = _make_df(5)
    out1 = generate_labels(df.copy(), bar_interval="5m", horizons=["5m"])

    df_mod = df.copy()
    df_mod.loc[2, "mid_price"] *= 1.50   # t=2 değiştir

    out2 = generate_labels(df_mod, bar_interval="5m", horizons=["5m"])

    # t=0 label ve future_return değişmemeli (t=0 yalnızca t=1'e bakıyor)
    assert out1["label_5m"].iloc[0] == out2["label_5m"].iloc[0]
    assert math.isclose(
        out1["future_return_5m"].iloc[0],
        out2["future_return_5m"].iloc[0],
        rel_tol=1e-9,
    )


def test_no_leakage_15m_label_unaffected_by_t4():
    """
    15m horizon (n_bars=3): t=0 yalnızca t=3'e bakıyor.
    t=4'ü değiştirmek t=0'ı etkilememeli.
    """
    df   = _make_df(10)
    out1 = generate_labels(df.copy(), bar_interval="5m", horizons=["15m"])

    df_mod = df.copy()
    df_mod.loc[4, "mid_price"] *= 2.0

    out2 = generate_labels(df_mod, bar_interval="5m", horizons=["15m"])

    assert out1["label_15m"].iloc[0] == out2["label_15m"].iloc[0]


# ── Hata durumları ────────────────────────────────────────────────────────────

def test_missing_mid_price_raises():
    with pytest.raises(ValueError, match="eksik kolonlar"):
        generate_labels(
            pd.DataFrame({"open_time": [1, 2, 3]}),
            horizons=["5m"],
        )


def test_empty_df_raises():
    with pytest.raises(ValueError):
        generate_labels(
            pd.DataFrame({"open_time": [], "mid_price": []}),
            horizons=["5m"],
        )


def test_unsorted_open_time_raises():
    df = pd.DataFrame({
        "open_time": [3, 1, 2],
        "mid_price": [100.0, 101.0, 102.0],
    })
    with pytest.raises(ValueError, match="artan sırada değil"):
        generate_labels(df, horizons=["5m"])


# ── Fix 1: Geliştirilmiş doğrulama ───────────────────────────────────────────

def test_duplicate_open_time_raises():
    """Duplicate open_time → ValueError."""
    df = pd.DataFrame({
        "open_time": [1_000_000, 1_000_000, 1_300_000],
        "mid_price": [100.0, 101.0, 102.0],
    })
    with pytest.raises(ValueError, match="duplicate open_time"):
        generate_labels(df, bar_interval="5m", horizons=["5m"])


def test_strict_interval_raises_on_gap():
    """strict_interval=True → düzensiz aralık → ValueError."""
    df = pd.DataFrame({
        "open_time": [0, 300_000, 900_000],   # 300k sonra 600k boşluk
        "mid_price": [100.0, 101.0, 102.0],
    })
    with pytest.raises(ValueError, match="strict_interval"):
        generate_labels(df, bar_interval="5m", horizons=["5m"], strict_interval=True)


def test_strict_interval_passes_on_regular():
    """strict_interval=True → düzenli aralık → geçmeli."""
    df = pd.DataFrame({
        "open_time": [0, 300_000, 600_000, 900_000],
        "mid_price": [100.0, 101.0, 102.0, 103.0],
    })
    out = generate_labels(df, bar_interval="5m", horizons=["5m"], strict_interval=True)
    assert len(out) == 4


def test_gap_emits_warning():
    """Gap tespit edildiğinde UserWarning fırlatılmalı."""
    df = pd.DataFrame({
        "open_time": [0, 300_000, 900_000],   # 600k gap var
        "mid_price": [100.0, 101.0, 102.0],
    })
    with pytest.warns(UserWarning, match="gap"):
        generate_labels(df, bar_interval="5m", horizons=["5m"])
