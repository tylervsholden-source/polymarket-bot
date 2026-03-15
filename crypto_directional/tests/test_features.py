"""
crypto_directional/tests/test_features.py

Feature modülleri için birim testler.
Odak: leakage yokluğu, warmup NaN, determinizm, output şeması.
"""
import numpy as np
import pandas as pd
import pytest

from crypto_directional.features.derivatives    import add_derivatives
from crypto_directional.features.mean_reversion import add_mean_reversion
from crypto_directional.features.microstructure import add_microstructure
from crypto_directional.features.momentum       import add_momentum
from crypto_directional.features.volatility     import add_volatility
from crypto_directional.features.feature_pipeline import (
    build_features,
    get_feature_columns,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_ohlcv(n: int = 200, seed: int = 42) -> pd.DataFrame:
    """Deterministik OHLCV verisi."""
    rng   = np.random.default_rng(seed)
    close = np.abs(100.0 + np.cumsum(rng.normal(0, 0.5, n))) + 1.0
    noise = rng.uniform(0.001, 0.005, (n, 2))
    high  = close * (1 + noise[:, 0])
    low   = close * (1 - noise[:, 1])
    open_ = np.abs(close * (1 + rng.normal(0, 0.002, n))) + 0.01
    vol   = rng.uniform(100, 10_000, n)
    return pd.DataFrame({
        "open_time": [1_000_000 + i * 300_000 for i in range(n)],
        "open":   open_,
        "high":   high,
        "low":    low,
        "close":  close,
        "volume": vol,
    })


def _make_full(n: int = 200) -> pd.DataFrame:
    """OB + taker + derivatives dahil tam DataFrame."""
    df  = _make_ohlcv(n)
    rng = np.random.default_rng(99)
    df["best_bid"]  = df["close"] * (1 - rng.uniform(0.00005, 0.0002, n))
    df["best_ask"]  = df["close"] * (1 + rng.uniform(0.00005, 0.0002, n))
    df["mid_price"] = (df["best_bid"] + df["best_ask"]) / 2
    df["bid_depth_5"]        = rng.uniform(10_000, 500_000, n)
    df["ask_depth_5"]        = rng.uniform(10_000, 500_000, n)
    df["bid_depth_10"]       = df["bid_depth_5"] * rng.uniform(1.5, 2.5, n)
    df["ask_depth_10"]       = df["ask_depth_5"] * rng.uniform(1.5, 2.5, n)
    df["taker_buy_volume"]   = df["volume"] * rng.uniform(0.3, 0.7, n)
    df["funding_rate"]       = rng.uniform(-0.001, 0.001, n)
    df["open_interest_usdt"] = rng.uniform(1e8, 1e9, n)
    df["long_liq_volume"]    = rng.uniform(0, 1e5, n)
    df["short_liq_volume"]   = rng.uniform(0, 1e5, n)
    df["btc_close"]          = np.abs(80_000 + np.cumsum(np.random.default_rng(7).normal(0, 100, n)))
    return df


# ── Pipeline şema testleri ────────────────────────────────────────────────────

def test_build_features_produces_feat_columns():
    df  = _make_full()
    out = build_features(df)
    assert len(get_feature_columns(out)) > 0


def test_no_label_columns_in_output():
    """Feature pipeline label veya future_return üretmemeli."""
    df  = _make_full()
    out = build_features(df)
    for col in out.columns:
        assert not col.startswith("label_"),        f"label kolon: {col}"
        assert not col.startswith("future_return_"), f"future_return kolon: {col}"


def test_open_time_preserved():
    df  = _make_full()
    out = build_features(df)
    pd.testing.assert_series_equal(
        out["open_time"].reset_index(drop=True),
        df["open_time"].reset_index(drop=True),
        check_names=False,
    )


def test_deterministic():
    df   = _make_full()
    out1 = build_features(df.copy())
    out2 = build_features(df.copy())
    cols = get_feature_columns(out1)
    pd.testing.assert_frame_equal(out1[cols], out2[cols])


def test_row_count_preserved():
    df  = _make_full(150)
    out = build_features(df)
    assert len(out) == 150


def test_all_new_cols_have_feat_prefix():
    """build_features'den gelen yeni tüm kolonlar feat_ ile başlamalı."""
    df_before   = _make_full(50)
    original    = set(df_before.columns)
    out         = build_features(df_before.copy())
    new_cols    = set(out.columns) - original
    for col in new_cols:
        assert col.startswith("feat_"), f"feat_ prefix'siz yeni kolon: {col}"


def test_validate_missing_column_raises():
    df = pd.DataFrame({"open_time": [1, 2], "close": [100, 101]})
    with pytest.raises(ValueError, match="eksik zorunlu kolonlar"):
        build_features(df)


# ── Momentum leakage testleri ─────────────────────────────────────────────────

def test_no_leakage_feat_return_1b():
    """t+1 kapanışını değiştirmek t'nin feat_return_1b'sini etkilememeli."""
    df   = _make_ohlcv(100)
    out1 = add_momentum(df.copy())

    df_mod = df.copy()
    df_mod.loc[51, "close"] *= 1.10    # t=51 değişti
    out2 = add_momentum(df_mod)

    assert np.isclose(
        out1["feat_return_1b"].iloc[50],
        out2["feat_return_1b"].iloc[50],
        rtol=1e-9,
    )


def test_no_leakage_feat_return_20b():
    """t+1 kapanışı t'nin 20b getirisini etkilememeli."""
    df   = _make_ohlcv(100)
    out1 = add_momentum(df.copy())

    df_mod = df.copy()
    df_mod.loc[51, "close"] *= 1.10
    out2 = add_momentum(df_mod)

    assert np.isclose(
        out1["feat_return_20b"].iloc[50],
        out2["feat_return_20b"].iloc[50],
        rtol=1e-9,
    )


def test_no_leakage_feat_rsi_14():
    """t+1 kapanışı t'nin RSI'ını etkilememeli."""
    df   = _make_ohlcv(100)
    out1 = add_momentum(df.copy())

    df_mod = df.copy()
    df_mod.loc[51, "close"] *= 0.90
    out2 = add_momentum(df_mod)

    assert np.isclose(
        out1["feat_rsi_14"].iloc[50],
        out2["feat_rsi_14"].iloc[50],
        rtol=1e-9,
    )


def test_no_leakage_ema_cross():
    """t+1 kapanışı t'nin EMA cross'unu etkilememeli."""
    df   = _make_ohlcv(100)
    out1 = add_momentum(df.copy())

    df_mod = df.copy()
    df_mod.loc[51, "close"] *= 1.50
    out2 = add_momentum(df_mod)

    assert np.isclose(
        out1["feat_ema_cross_5_20"].iloc[50],
        out2["feat_ema_cross_5_20"].iloc[50],
        rtol=1e-9,
    )


# ── Mean reversion leakage testleri ──────────────────────────────────────────

def test_no_leakage_zscore_close_20():
    """t+1 kapanışı t'nin z-score'unu etkilememeli."""
    df   = _make_ohlcv(100)
    out1 = add_mean_reversion(df.copy())

    df_mod = df.copy()
    df_mod.loc[51, "close"] *= 2.0
    out2 = add_mean_reversion(df_mod)

    assert np.isclose(
        out1["feat_zscore_close_20"].iloc[50],
        out2["feat_zscore_close_20"].iloc[50],
        rtol=1e-9,
    )


# ── Volatility leakage testleri ───────────────────────────────────────────────

def test_no_leakage_realized_vol_20():
    """t+1 kapanışı t'nin realized vol'unu etkilememeli."""
    df   = _make_ohlcv(100)
    out1 = add_volatility(df.copy())

    df_mod = df.copy()
    df_mod.loc[51, "close"] *= 1.20
    out2 = add_volatility(df_mod)

    assert np.isclose(
        out1["feat_realized_vol_20"].iloc[50],
        out2["feat_realized_vol_20"].iloc[50],
        rtol=1e-9,
    )


def test_no_leakage_vol_ratio_5_20():
    df   = _make_ohlcv(100)
    out1 = add_volatility(df.copy())

    df_mod = df.copy()
    df_mod.loc[51, "close"] *= 1.30
    out2 = add_volatility(df_mod)

    assert np.isclose(
        out1["feat_vol_ratio_5_20"].iloc[50],
        out2["feat_vol_ratio_5_20"].iloc[50],
        rtol=1e-9,
    )


# ── Microstructure leakage testleri ──────────────────────────────────────────

def test_no_leakage_volume_ratio_20():
    """t+1 hacmini değiştirmek t'nin volume_ratio'sunu etkilememeli."""
    df   = _make_full(100)
    out1 = add_microstructure(df.copy())

    df_mod = df.copy()
    df_mod.loc[51, "volume"] *= 100   # t=51 patlat
    out2 = add_microstructure(df_mod)

    # t=50'nin değeri: shift(1) + rolling(20) → t=51 bu pencerede değil
    assert np.isclose(
        out1["feat_volume_ratio_20"].iloc[50],
        out2["feat_volume_ratio_20"].iloc[50],
        rtol=1e-9,
    )


def test_no_leakage_ob_imbalance_5():
    """t+1 OB depth değişimi t'yi etkilememeli."""
    df   = _make_full(100)
    out1 = add_microstructure(df.copy())

    df_mod = df.copy()
    df_mod.loc[51, "bid_depth_5"] *= 10
    out2 = add_microstructure(df_mod)

    assert np.isclose(
        out1["feat_ob_imbalance_5"].iloc[50],
        out2["feat_ob_imbalance_5"].iloc[50],
        rtol=1e-9,
    )


def test_no_leakage_spread_pct():
    df   = _make_full(100)
    out1 = add_microstructure(df.copy())

    df_mod = df.copy()
    df_mod.loc[51, "best_ask"] *= 1.01
    out2 = add_microstructure(df_mod)

    assert np.isclose(
        out1["feat_spread_pct"].iloc[50],
        out2["feat_spread_pct"].iloc[50],
        rtol=1e-9,
    )


# ── Warmup NaN testleri ───────────────────────────────────────────────────────

def test_rsi_warmup_nan():
    """İlk 14 bar RSI NaN olmalı."""
    df  = _make_ohlcv(50)
    out = add_momentum(df)
    assert out["feat_rsi_14"].iloc[:14].isna().all()
    assert pd.notna(out["feat_rsi_14"].iloc[20])


def test_zscore_warmup_nan():
    """İlk 19 bar z-score NaN olmalı (rolling 20, ilk tam pencere index 19)."""
    df  = _make_ohlcv(50)
    out = add_mean_reversion(df)
    assert out["feat_zscore_close_20"].iloc[:19].isna().all()
    assert pd.notna(out["feat_zscore_close_20"].iloc[19])


def test_realized_vol_20_warmup():
    """
    log_ret'in index 0 NaN; rolling(20) first valid = index 20.
    İlk 20 bar NaN olmalı.
    """
    df  = _make_ohlcv(100)
    out = add_volatility(df)
    assert out["feat_realized_vol_20"].iloc[:20].isna().all()
    assert pd.notna(out["feat_realized_vol_20"].iloc[20])


def test_realized_vol_50_warmup():
    """İlk 50 bar NaN olmalı."""
    df  = _make_ohlcv(100)
    out = add_volatility(df)
    assert out["feat_realized_vol_50"].iloc[:50].isna().all()
    assert pd.notna(out["feat_realized_vol_50"].iloc[50])


def test_vol_regime_warmup():
    """İlk 99 bar vol_regime NaN olmalı (rolling 100, min_periods=100)."""
    df  = _make_ohlcv(200)
    out = add_volatility(df)
    assert pd.isna(out["feat_vol_regime"].iloc[98])
    # index 99: rolling(100) covers 0..99 — vol_20 first valid at 20
    # vol_regime starts being non-NaN only when rolling(100) of vol20 has 100 values
    # vol20 first valid = 20 → vol_regime first valid = 20 + 99 = 119
    assert pd.isna(out["feat_vol_regime"].iloc[118])
    assert pd.notna(out["feat_vol_regime"].iloc[119])


def test_volume_ratio_20_warmup():
    """shift(1) + rolling(20) → ilk 20 bar NaN."""
    df  = _make_full(50)
    out = add_microstructure(df)
    assert out["feat_volume_ratio_20"].iloc[:20].isna().all()


# ── Edge case testleri ────────────────────────────────────────────────────────

def test_zero_volume_taker_ratio_no_inf():
    """Sıfır hacimde ZeroDivisionError değil, NaN veya 0 bekliyoruz."""
    df = _make_full(10)
    df["volume"]           = 0.0
    df["taker_buy_volume"] = 0.0
    out = add_microstructure(df)
    vals = out["feat_taker_buy_ratio"]
    assert not vals.isin([float("inf"), float("-inf")]).any()


def test_equal_bid_ask_ob_imbalance_zero():
    """bid == ask → imbalance = 0."""
    df = _make_full(10)
    df["bid_depth_5"] = 100.0
    df["ask_depth_5"] = 100.0
    out = add_microstructure(df)
    assert (out["feat_ob_imbalance_5"].dropna() == 0.0).all()


def test_missing_ob_columns_graceful():
    """OB kolonları yoksa NaN dönmeli, exception atmamalı."""
    df = _make_ohlcv(10)
    out = add_microstructure(df)
    assert out["feat_ob_imbalance_5"].isna().all()
    assert out["feat_spread_pct"].isna().all()


def test_missing_funding_graceful():
    df = _make_ohlcv(10)
    out = add_derivatives(df)
    assert out["feat_funding_rate"].isna().all()
    assert out["feat_oi_change_3b"].isna().all()


def test_missing_oi_graceful():
    df = _make_ohlcv(10)
    out = add_derivatives(df)
    assert out["feat_oi_zscore_20"].isna().all()


def test_missing_btc_close_graceful():
    """btc_close yokken feat_btc_return_1b NaN olmalı."""
    df  = _make_ohlcv(10)
    out = add_derivatives(df)
    assert out["feat_btc_return_1b"].isna().all()


def test_oi_change_3b_formula():
    """(OI[t] - OI[t-3]) / OI[t-3] doğruluğu."""
    n   = 10
    df  = _make_ohlcv(n)
    df["open_interest_usdt"] = [100.0 + i for i in range(n)]
    out = add_derivatives(df)
    # t=5: OI[5]=105, OI[2]=102 → (105-102)/102 = 3/102
    expected = (105.0 - 102.0) / 102.0
    assert np.isclose(out["feat_oi_change_3b"].iloc[5], expected, rtol=1e-6)


def test_liq_imbalance_formula():
    """(long - short) / (long + short + eps) doğruluğu."""
    df = _make_ohlcv(5)
    df["long_liq_volume"]  = [10.0, 20.0, 0.0, 5.0, 15.0]
    df["short_liq_volume"] = [10.0, 10.0, 0.0, 5.0,  5.0]
    out = add_derivatives(df)
    # t=1: (20-10)/(20+10+eps) = 10/30 ≈ 0.333
    assert np.isclose(out["feat_liq_imbalance"].iloc[1], 10 / 30, rtol=1e-4)
    # t=0: 0
    assert np.isclose(out["feat_liq_imbalance"].iloc[0], 0.0, atol=1e-6)


# ── Fix 2: derivatives bar_interval ──────────────────────────────────────────

def test_funding_8h_sum_5m_96bars():
    """5m baz → bars_per_8h=96; 96. bar'daki shift geçerli olmalı."""
    n  = 300
    df = _make_ohlcv(n)
    df["funding_rate"] = 0.0001
    out = add_derivatives(df, bar_interval="5m")
    # bars_per_8h=96 → shift(96) ilk geçerli: index 96
    assert pd.isna(out["feat_funding_8h_sum"].iloc[95])
    assert pd.notna(out["feat_funding_8h_sum"].iloc[192])   # shift(192) gerekli


def test_funding_8h_sum_15m_32bars():
    """15m baz → bars_per_8h=32; 5m varsayımından (96) farklı olmalı."""
    n  = 200
    df = _make_ohlcv(n)
    df["funding_rate"] = 0.0001
    out_5m  = add_derivatives(df.copy(), bar_interval="5m")
    out_15m = add_derivatives(df.copy(), bar_interval="15m")
    # 15m'de shift(32) kullanılıyor, 5m'de shift(96) — index 64'te 15m geçerli, 5m hâlâ NaN
    assert pd.notna(out_15m["feat_funding_8h_sum"].iloc[64])
    assert pd.isna(out_5m["feat_funding_8h_sum"].iloc[64])


def test_build_features_passes_bar_interval():
    """build_features bar_interval='15m' → add_derivatives 32 bars kullanmalı."""
    n  = 200
    df = _make_ohlcv(n)
    df["funding_rate"] = 0.0002
    out = build_features(df, bar_interval="15m")
    # bars_per_8h=32 → shift(64) gerekli (2×32); index 64'te NaN değil
    assert pd.notna(out["feat_funding_8h_sum"].iloc[64])


# ── Fix 3: NaN politikası ─────────────────────────────────────────────────────

def test_get_feature_columns_enabled_only_excludes_all_nan():
    """Tüm satırları NaN olan (disabled) feature'lar enabled_only=True ile hariç."""
    df = _make_ohlcv(10)
    out = build_features(df)
    # Disabled feature yoksa tüm feat_ kolonları dönmeli
    all_cols     = get_feature_columns(out, enabled_only=False)
    enabled_cols = get_feature_columns(out, enabled_only=True)
    # Disabled feature varsa enabled < all; yoksa eşit
    assert set(enabled_cols).issubset(set(all_cols))
    # enabled_only=True'da all-NaN kolon yok
    for col in enabled_cols:
        assert not out[col].isna().all(), f"{col} tümü NaN ama enabled sayıldı"


def test_get_feature_columns_detects_disabled():
    """Manüel olarak NaN yapılmış kolon enabled_only=True'da hariç tutulmalı."""
    df  = _make_ohlcv(50)
    out = build_features(df)
    # Bir kolonu manually NaN yap
    out["feat_rsi_14"] = float("nan")
    enabled = get_feature_columns(out, enabled_only=True)
    assert "feat_rsi_14" not in enabled


def test_trim_warmup_removes_leading_nan():
    """trim_warmup baştaki NaN satırları kırpmalı."""
    from crypto_directional.features.feature_pipeline import trim_warmup
    df = _make_ohlcv(200)
    out = build_features(df)
    feat_cols = get_feature_columns(out, enabled_only=True)
    trimmed = trim_warmup(out, feat_cols)
    # Kırpılmış DataFrame'de enabled feature'ların ilk satırı NaN içermemeli
    assert not trimmed[feat_cols].iloc[0].isna().any()


def test_trim_warmup_empty_feature_cols():
    """feature_cols boş → orijinal döner."""
    from crypto_directional.features.feature_pipeline import trim_warmup
    df = _make_ohlcv(10)
    result = trim_warmup(df, [])
    assert len(result) == len(df)
