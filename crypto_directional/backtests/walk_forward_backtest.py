"""
crypto_directional/backtests/walk_forward_backtest.py

Expanding-window walk-forward backtest.

Purge + embargo (SPEC.md §6.3):
    embargo_bars = n_bars_ahead + 1
    n_bars_ahead = _bars_ahead(horizon, bar_interval)

Her fold:
    - train : [0, train_end)               — expanding
    - gap   : [train_end, val_start)       — embargo (n_bars_ahead+1 bar)
    - val   : [val_start, val_end)

shuffle = False (daima) — SPEC.md validation kuralı.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from crypto_directional.backtests.metrics import aggregate_metrics, compute_metrics
from crypto_directional.config.settings import settings
from crypto_directional.labeling.label_generator import _bars_ahead
from crypto_directional.models.baselines import apply_confidence_filter


@dataclass
class FoldResult:
    fold: int
    train_size: int
    val_size: int
    train_end_idx: int   # exclusive — train = clean_df[:train_end_idx]
    val_start_idx: int   # inclusive — val   = clean_df[val_start_idx:val_end_idx]
    val_end_idx: int
    metrics: dict[str, float]
    oof_df: pd.DataFrame  # open_time, y_true, y_pred, future_return, proba_{up,down,no_trade}


@dataclass
class WalkForwardResult:
    horizon: str
    model_name: str
    n_folds_run: int
    fold_results: list[FoldResult] = field(default_factory=list)
    oof_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    summary: dict[str, float] = field(default_factory=dict)


def walk_forward_backtest(
    df: pd.DataFrame,
    horizon: str,
    feature_cols: list[str],
    model_factory: Callable,
    bar_interval: str = "5m",
    n_folds: int | None = None,
    val_pct: float | None = None,
    min_train_bars: int | None = None,
    min_confidence: float | None = None,
) -> WalkForwardResult:
    """
    Expanding-window walk-forward backtest.

    Parametreler
    ------------
    df : DataFrame (time-sorted, artan open_time)
        Zorunlu kolonlar:
          - open_time               int64
          - label_{horizon}         str   (NO_DATA satırlar otomatik atılır)
          - future_return_{horizon} float
          - tüm feature_cols        float
    horizon : str
        Tahmin horizonu: "5m", "15m", ...
    feature_cols : list[str]
        Kullanılacak feat_* sütun adları.
    model_factory : Callable[[], model]
        Her fold için yeni model döner. Duck-type: .fit/.predict/.predict_proba/.name/.classes_
    bar_interval : str
        Bar granülaritesi (embargo hesabı).
    n_folds, val_pct, min_train_bars : config override (None → defaults.yaml).
    min_confidence : float | None
        predict_proba güven eşiği (None → model.min_confidence config).

    Güvenceler
    ----------
    - val_start_idx > train_end_idx (her fold — zaman sırası)
    - val_start_idx - train_end_idx == embargo_bars (tam boşluk)
    - shuffle = False (hiçbir zaman)
    - NO_DATA ve NaN-feature satırlar train ve val'dan çıkarılır
    """
    _n_folds        = n_folds        or int(settings.get("validation.n_folds",        5))
    _val_pct        = val_pct        or float(settings.get("validation.val_pct",      0.15))
    _min_train_bars = min_train_bars or int(settings.get("validation.min_train_bars", 2000))

    label_col         = f"label_{horizon}"
    future_return_col = f"future_return_{horizon}"

    _validate_df(df, label_col, future_return_col, feature_cols)

    # 1. Temizle: NO_DATA ve NaN feature satırlarını at
    clean = _prepare(df, label_col, future_return_col, feature_cols)
    N = len(clean)
    if N == 0:
        raise ValueError("walk_forward_backtest: temizlendikten sonra satır kalmadı.")

    # 2. Embargo
    embargo = _embargo_bars(horizon, bar_interval)

    # 3. Val büyüklüğü (per fold)
    val_size_per_fold = max(1, int(N * _val_pct) // _n_folds)

    # 4. Split indeksleri
    splits = _make_splits(N, _n_folds, val_size_per_fold, embargo, _min_train_bars)
    if not splits:
        raise ValueError(
            f"walk_forward_backtest: geçerli fold yok. "
            f"N={N}, min_train={_min_train_bars}, embargo={embargo}, "
            f"val_size={val_size_per_fold}, n_folds={_n_folds}"
        )

    # 5. Numpy array'lere çevir
    X_all    = clean[feature_cols].values.astype(float)
    y_all    = clean[label_col].values
    ret_all  = clean[future_return_col].values.astype(float)
    time_all = clean["open_time"].values

    # Model adını bir probe instance'la al
    _probe     = model_factory()
    model_name = getattr(_probe, "name", "unknown")
    del _probe

    fold_results: list[FoldResult] = []
    oof_dfs:      list[pd.DataFrame] = []

    for k, (train_end, val_start, val_end) in enumerate(splits):
        X_train = X_all[:train_end]
        y_train = y_all[:train_end]

        X_val    = X_all[val_start:val_end]
        y_val    = y_all[val_start:val_end]
        ret_val  = ret_all[val_start:val_end]
        time_val = time_all[val_start:val_end]

        if len(X_train) == 0 or len(X_val) == 0:
            continue

        model = model_factory()
        model.fit(X_train, y_train)

        y_pred  = model.predict(X_val)
        y_proba = model.predict_proba(X_val)

        y_pred = apply_confidence_filter(y_pred, y_proba, min_confidence)

        mets = compute_metrics(y_val, y_pred, ret_val)

        # OOF DataFrame
        classes_ = list(model.classes_)
        oof = pd.DataFrame({
            "open_time":    time_val,
            "y_true":       y_val,
            "y_pred":       y_pred,
            "future_return": ret_val,
        })
        for cls in ("DOWN", "NO_TRADE", "UP"):
            if cls in classes_:
                oof[f"proba_{cls.lower()}"] = y_proba[:, classes_.index(cls)]
            else:
                oof[f"proba_{cls.lower()}"] = float("nan")

        fold_results.append(FoldResult(
            fold=k,
            train_size=train_end,
            val_size=len(X_val),
            train_end_idx=train_end,
            val_start_idx=val_start,
            val_end_idx=val_end,
            metrics=mets,
            oof_df=oof,
        ))
        oof_dfs.append(oof)

    summary     = aggregate_metrics([fr.metrics for fr in fold_results]) if fold_results else {}
    oof_combined = pd.concat(oof_dfs, ignore_index=True) if oof_dfs else pd.DataFrame()

    return WalkForwardResult(
        horizon=horizon,
        model_name=model_name,
        n_folds_run=len(fold_results),
        fold_results=fold_results,
        oof_df=oof_combined,
        summary=summary,
    )


# ── Private helpers ────────────────────────────────────────────────────────────

def _embargo_bars(horizon: str, bar_interval: str) -> int:
    """SPEC.md §6.3: embargo_bars = n_bars_ahead + 1."""
    return _bars_ahead(horizon, bar_interval) + 1


def _make_splits(
    N: int,
    n_folds: int,
    val_size: int,
    embargo: int,
    min_train: int,
) -> list[tuple[int, int, int]]:
    """
    Expanding walk-forward split indekslerini döner.

    Her tuple: (train_end, val_start, val_end)
        train : [0, train_end)
        val   : [val_start, val_end)
        gap   : train_end..val_start == embargo bars
    """
    splits = []
    for k in range(n_folds):
        val_end   = N - (n_folds - 1 - k) * val_size
        val_start = val_end - val_size
        train_end = val_start - embargo

        if train_end < min_train:
            continue
        if val_start <= 0 or val_end > N:
            continue

        splits.append((train_end, val_start, val_end))

    return splits


def _prepare(
    df: pd.DataFrame,
    label_col: str,
    future_return_col: str,
    feature_cols: list[str],
) -> pd.DataFrame:
    """
    NO_DATA satırları ve NaN feature içeren satırları atar.
    Index sıfırlanır; zaman sırası korunur.
    """
    mask   = df[label_col] != "NO_DATA"
    subset = df[mask].copy()

    nan_mask = subset[feature_cols].isna().any(axis=1)
    subset   = subset[~nan_mask]

    return subset.reset_index(drop=True)


def _validate_df(
    df: pd.DataFrame,
    label_col: str,
    future_return_col: str,
    feature_cols: list[str],
) -> None:
    required = {"open_time", label_col, future_return_col} | set(feature_cols)
    missing  = required - set(df.columns)
    if missing:
        raise ValueError(f"walk_forward_backtest: eksik kolonlar: {missing}")
    if df.empty:
        raise ValueError("walk_forward_backtest: boş DataFrame.")
    diffs = df["open_time"].diff().dropna()
    if not (diffs >= 0).all():
        raise ValueError("walk_forward_backtest: open_time artan sırada değil.")
