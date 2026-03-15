"""
crypto_directional/features/feature_pipeline.py

Ana feature pipeline: tüm feature modüllerini sırayla çağırır.

Kullanım:
    from crypto_directional.features.feature_pipeline import build_features
    features_df = build_features(aligned_df, symbol="BTCUSDT")

Giriş DataFrame'i DATA_SCHEMA.md §1'deki timestamp alignment pseudocode ile
üretilmiş, bar open_time üzerinde hizalanmış olmalı.

Leakage kuralı: tüm feat_* kolonları t anında veya öncesinde bilgiye dayanır.
future_return / label kolonları bu pipeline'dan ASLA çıkmaz.
"""
from __future__ import annotations

import pandas as pd

from crypto_directional.config.settings import settings
from crypto_directional.features.momentum       import add_momentum
from crypto_directional.features.mean_reversion import add_mean_reversion
from crypto_directional.features.volatility     import add_volatility
from crypto_directional.features.microstructure import add_microstructure
from crypto_directional.features.derivatives    import add_derivatives

# P1 feature kolonları — ilk modele zorunlu dahil
P1_FEATURES = [
    "feat_return_1b",
    "feat_return_3b", "feat_return_5b", "feat_return_10b", "feat_return_20b",
    "feat_ema_cross_5_20", "feat_ema_cross_9_21",
    "feat_rsi_14",
    "feat_zscore_close_20", "feat_bb_position_20",
    "feat_realized_vol_10", "feat_realized_vol_20", "feat_realized_vol_50",
    "feat_vol_ratio_5_20",
    "feat_ob_imbalance_5", "feat_spread_pct",
    "feat_taker_buy_ratio", "feat_volume_ratio_20",
    "feat_funding_rate", "feat_oi_change_3b",
    "feat_vol_regime",
]

# P2 feature kolonları — ablation testine tabi
P2_FEATURES = [
    "feat_macd_hist_norm", "feat_ema_slope_9",
    "feat_price_dev_ema_21", "feat_price_vs_vwap",
    "feat_atr_14_norm", "feat_intrabar_range",
    "feat_ob_imbalance_10", "feat_spread_zscore_20",
    "feat_funding_8h_sum",
    "feat_oi_zscore_20", "feat_liq_imbalance",
    "feat_adx_14", "feat_di_plus_14", "feat_di_minus_14",
    "feat_btc_return_1b",
]

ALL_FEATURE_COLS = P1_FEATURES + P2_FEATURES


def build_features(
    df: pd.DataFrame,
    symbol: str = "",
    bar_interval: str = "5m",
) -> pd.DataFrame:
    """
    Tüm feature'ları hesaplar.

    Parametreler
    ------------
    df : DataFrame
        Bar open_time üzerinde hizalanmış veri.
        Zorunlu: open_time, open, high, low, close, volume
        Opsiyonel: bid_depth_5/10, ask_depth_5/10, best_bid, best_ask,
                   mid_price, taker_buy_volume, funding_rate,
                   open_interest_usdt, long_liq_volume, short_liq_volume,
                   btc_close (ETH için)
    symbol : str
        Sembol (log/debug amaçlı)
    bar_interval : str
        Bar granülaritesi — derivatives.add_derivatives'e iletilir.
        "5m" (varsayılan), "1m", "15m", "1h"

    Döndürür
    --------
    DataFrame:
        - open_time kolon korunur
        - feat_* kolonları eklenir
        - Ham veri kolonları korunur
        - future_return / label kolonları eklenmez — bu pipeline'ın işi değil

    NaN Politikası (downstream model için)
    ----------------------------------------
    1. Disabled feature (toggle=False): tüm satırlar NaN
       → get_feature_columns(enabled_only=True) ile hariç tut
    2. Warmup NaN: rolling pencere warmup süresi boyunca baştaki satırlar
       → trim_warmup(df, feature_cols) ile kırp
    3. Data gap NaN: eksik bar nedeniyle ortada oluşan NaN
       → walk_forward_backtest._prepare() içinde satır bazlı drop
    Imputation yapılmaz. NaN → drop/exclude politikası.

    Güvence: feat_ prefix'li hiçbir kolon t+1 veya sonrası bilgiye dayanmaz.
    """
    _validate_input(df)

    # Çalışma kopyası — orijinali değiştirme
    out = df.copy()

    # Feature modülleri sırayla — volatility önce gelir (vol_ratio vol20'ye bağlı)
    out = add_momentum(out)
    out = add_mean_reversion(out)
    out = add_volatility(out)                        # vol20 burada hesaplanır
    out = add_microstructure(out)
    out = add_derivatives(out, bar_interval=bar_interval)

    # Config feature toggle'ları uygula
    _apply_feature_flags(out)

    return out


def get_feature_columns(df: pd.DataFrame, enabled_only: bool = True) -> list[str]:
    """
    feat_* kolonlarını döner.

    enabled_only=True (varsayılan):
        Tüm değerleri NaN olan kolonlar (disabled toggle) hariç tutulur.
        Bu kolonlar model feature vektörüne dahil edilmemelidir.

    enabled_only=False:
        Tüm feat_* kolonlarını döner (şema denetimi için).
    """
    cols = [c for c in df.columns if c.startswith("feat_")]
    if enabled_only:
        cols = [c for c in cols if not df[c].isna().all()]
    return cols


def trim_warmup(df: pd.DataFrame, feature_cols: list[str]) -> pd.DataFrame:
    """
    Feature warmup nedeniyle NaN olan baştaki satırları kırpar.

    İlk geçerli satır: tüm feature_cols'un non-NaN olduğu ilk satır.
    Ortada kalan NaN'ler (data gap) dokunulmaz — walk_forward_backtest
    tarafından satır bazlı handle edilir.

    Döndürür: index sıfırlanmış DataFrame.
    """
    if not feature_cols:
        return df.reset_index(drop=True)
    valid = ~df[feature_cols].isna().any(axis=1)
    if not valid.any():
        return df.iloc[0:0].reset_index(drop=True)
    first_valid = int(valid.values.argmax())    # numpy argmax — ilk True pozisyonu
    return df.iloc[first_valid:].reset_index(drop=True)


# ── Private helpers ───────────────────────────────────────────────────────────

def _apply_feature_flags(df: pd.DataFrame) -> None:
    """
    defaults.yaml'daki feature toggle'ları uygular.
    False olan feature kolonu NaN yapılır (silinmez — şema bütünlüğü için).
    """
    feature_cfg = settings.get("features", {})
    if not isinstance(feature_cfg, dict):
        return
    for _category, feats in feature_cfg.items():
        if not isinstance(feats, dict):
            continue
        for feat_name, enabled in feats.items():
            col = f"feat_{feat_name}"
            if not enabled and col in df.columns:
                df[col] = float("nan")


def _validate_input(df: pd.DataFrame) -> None:
    required = {"open_time", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"feature_pipeline: eksik zorunlu kolonlar: {missing}")
    if df.empty:
        raise ValueError("feature_pipeline: boş DataFrame.")
    diffs = df["open_time"].diff().dropna()
    if not (diffs >= 0).all():
        raise ValueError("feature_pipeline: open_time artan sırada değil.")
