"""
crypto_directional/tests/test_backtest.py

Walk-forward backtest ve metrik testleri.
Odak: leakage yokluğu, embargo doğruluğu, metrik tutarlılığı, hata durumları.
"""
import math

import numpy as np
import pandas as pd
import pytest

from crypto_directional.backtests.metrics import aggregate_metrics, compute_metrics
from crypto_directional.backtests.walk_forward_backtest import (
    WalkForwardResult,
    _embargo_bars,
    _make_splits,
    walk_forward_backtest,
)
from crypto_directional.models.baselines import LogisticRegressionBaseline


# ── Fixture ───────────────────────────────────────────────────────────────────

_FEAT_COLS = ["feat_a", "feat_b", "feat_c"]


def _make_wf_df(n: int = 600, seed: int = 0) -> pd.DataFrame:
    """
    Walk-forward backtest için sentetik DataFrame.
    Label feat_a'ya göre deterministik — LR öğrenebilir.
    """
    rng = np.random.RandomState(seed)

    feat_a = rng.randn(n)
    feat_b = rng.randn(n)
    feat_c = rng.randn(n)

    labels  = []
    returns = []
    for i in range(n):
        a = feat_a[i]
        if i >= n - 3:              # son 3 bar NO_DATA (15m embargo gösterimi)
            labels.append("NO_DATA")
            returns.append(float("nan"))
        elif a > 0.5:
            labels.append("UP")
            returns.append(abs(rng.randn()) * 0.002)
        elif a < -0.5:
            labels.append("DOWN")
            returns.append(-abs(rng.randn()) * 0.002)
        else:
            labels.append("NO_TRADE")
            returns.append(rng.randn() * 0.0005)

    return pd.DataFrame({
        "open_time":        [1_000_000 + i * 300_000 for i in range(n)],
        "feat_a":           feat_a,
        "feat_b":           feat_b,
        "feat_c":           feat_c,
        "label_5m":         labels,
        "future_return_5m": returns,
    })


# ── compute_metrics testleri ──────────────────────────────────────────────────

def test_compute_metrics_required_keys():
    y_true = np.array(["UP", "DOWN", "NO_TRADE", "UP", "DOWN"])
    y_pred = np.array(["UP", "UP",   "NO_TRADE", "UP", "DOWN"])
    rets   = np.array([0.002, 0.001, 0.0, 0.003, -0.002])
    mets   = compute_metrics(y_true, y_pred, rets)
    for key in (
        "precision_up", "recall_up", "f1_up",
        "precision_down", "recall_down", "f1_down",
        "directional_accuracy", "expectancy_pct",
        "sharpe", "n_trades", "n_val_bars", "no_trade_rate",
    ):
        assert key in mets, f"Eksik metrik: {key}"


def test_compute_metrics_perfect_directional():
    y = np.array(["UP", "DOWN", "UP", "DOWN"])
    r = np.array([0.002, -0.001, 0.003, -0.002])
    mets = compute_metrics(y, y, r)
    assert math.isclose(mets["directional_accuracy"], 1.0)
    assert mets["expectancy_pct"] > 0   # long UP: +r; short DOWN: +|r|


def test_compute_metrics_no_trades():
    y_true = np.array(["UP", "DOWN"])
    y_pred = np.array(["NO_TRADE", "NO_TRADE"])
    rets   = np.array([0.002, -0.002])
    mets   = compute_metrics(y_true, y_pred, rets)
    assert mets["n_trades"] == 0
    assert math.isnan(mets["expectancy_pct"])
    assert math.isnan(mets["sharpe"])
    assert math.isclose(mets["no_trade_rate"], 1.0)


def test_compute_metrics_expectancy_long():
    """UP tahmini doğruysa expectancy > 0."""
    y_true = np.array(["UP"] * 10)
    y_pred = np.array(["UP"] * 10)
    rets   = np.ones(10) * 0.001
    mets   = compute_metrics(y_true, y_pred, rets)
    assert mets["expectancy_pct"] > 0
    assert mets["n_trades"] == 10


def test_compute_metrics_expectancy_short():
    """DOWN tahmini doğruysa (fiyat düştü) expectancy > 0."""
    y_true = np.array(["DOWN"] * 10)
    y_pred = np.array(["DOWN"] * 10)
    rets   = np.ones(10) * (-0.001)     # future_return negatif
    mets   = compute_metrics(y_true, y_pred, rets)
    # trade_return = -future_return = +0.001
    assert mets["expectancy_pct"] > 0


def test_compute_metrics_n_val_bars():
    y = np.array(["UP", "DOWN", "NO_TRADE"])
    r = np.array([0.001, -0.001, 0.0])
    mets = compute_metrics(y, y, r)
    assert mets["n_val_bars"] == 3.0


def test_compute_metrics_rates_sum_to_one():
    y_true = np.array(["UP", "DOWN", "NO_TRADE", "UP"])
    y_pred = np.array(["UP", "DOWN", "NO_TRADE", "NO_TRADE"])
    rets   = np.zeros(4)
    mets   = compute_metrics(y_true, y_pred, rets)
    total  = mets["up_rate"] + mets["down_rate"] + mets["no_trade_rate"]
    assert math.isclose(total, 1.0, rel_tol=1e-9)


# ── aggregate_metrics testleri ────────────────────────────────────────────────

def test_aggregate_empty():
    assert aggregate_metrics([]) == {}


def test_aggregate_sums_counts():
    m1 = {"n_trades": 10.0, "n_val_bars": 100.0, "expectancy_pct": 0.001}
    m2 = {"n_trades": 20.0, "n_val_bars": 100.0, "expectancy_pct": 0.002}
    agg = aggregate_metrics([m1, m2])
    assert agg["n_trades"]   == 30.0
    assert agg["n_val_bars"] == 200.0


def test_aggregate_nan_ignored_in_mean():
    m1 = {"sharpe": 0.5,         "n_trades": 10.0, "n_val_bars": 100.0}
    m2 = {"sharpe": float("nan"), "n_trades": 20.0, "n_val_bars": 100.0}
    agg = aggregate_metrics([m1, m2])
    # sharpe: mean([0.5]) = 0.5  (NaN atlandı)
    assert math.isclose(agg["sharpe"], 0.5)


# ── _embargo_bars testleri ────────────────────────────────────────────────────

def test_embargo_5m_on_5m():
    assert _embargo_bars("5m", "5m") == 2     # n_bars=1 + 1


def test_embargo_15m_on_5m():
    assert _embargo_bars("15m", "5m") == 4    # n_bars=3 + 1


def test_embargo_1h_on_5m():
    assert _embargo_bars("1h", "5m") == 13    # n_bars=12 + 1


# ── _make_splits testleri ─────────────────────────────────────────────────────

def test_make_splits_count():
    splits = _make_splits(N=500, n_folds=3, val_size=50, embargo=4, min_train=100)
    assert len(splits) == 3


def test_make_splits_no_leakage():
    """Her fold'da val_start > train_end."""
    splits = _make_splits(N=500, n_folds=3, val_size=50, embargo=4, min_train=100)
    for train_end, val_start, _ in splits:
        assert val_start > train_end, (
            f"Leakage: val_start={val_start} <= train_end={train_end}"
        )


def test_make_splits_exact_embargo_gap():
    """val_start - train_end == embargo (tam boşluk)."""
    embargo = 4
    splits  = _make_splits(N=500, n_folds=3, val_size=50, embargo=embargo, min_train=100)
    for train_end, val_start, _ in splits:
        assert val_start - train_end == embargo, (
            f"Gap={val_start - train_end} != embargo={embargo}"
        )


def test_make_splits_expanding():
    """Her fold train_end önceki fold'dan büyük."""
    splits = _make_splits(N=500, n_folds=3, val_size=50, embargo=4, min_train=100)
    for i in range(len(splits) - 1):
        assert splits[i][0] < splits[i + 1][0], "Expanding window değil"


def test_make_splits_min_train_skipped():
    """min_train karşılanamayan foldlar atlanır."""
    splits = _make_splits(N=500, n_folds=3, val_size=50, embargo=4, min_train=400)
    for train_end, _, _ in splits:
        assert train_end >= 400


# ── walk_forward_backtest entegrasyon testleri ────────────────────────────────

def test_wf_returns_result_type():
    df = _make_wf_df(600)
    result = walk_forward_backtest(
        df, horizon="5m", feature_cols=_FEAT_COLS,
        model_factory=LogisticRegressionBaseline,
        bar_interval="5m",
        n_folds=3, val_pct=0.2, min_train_bars=100,
    )
    assert isinstance(result, WalkForwardResult)
    assert result.n_folds_run > 0


def test_wf_no_data_leakage():
    """Her fold'da val_start_idx > train_end_idx."""
    df = _make_wf_df(600)
    result = walk_forward_backtest(
        df, horizon="5m", feature_cols=_FEAT_COLS,
        model_factory=LogisticRegressionBaseline,
        bar_interval="5m",
        n_folds=3, val_pct=0.2, min_train_bars=100,
    )
    for fr in result.fold_results:
        assert fr.val_start_idx > fr.train_end_idx, (
            f"Fold {fr.fold}: leakage — val_start={fr.val_start_idx} <= train_end={fr.train_end_idx}"
        )


def test_wf_embargo_respected():
    """val_start - train_end == embargo_bars."""
    df      = _make_wf_df(600)
    embargo = _embargo_bars("5m", "5m")   # = 2
    result  = walk_forward_backtest(
        df, horizon="5m", feature_cols=_FEAT_COLS,
        model_factory=LogisticRegressionBaseline,
        bar_interval="5m",
        n_folds=3, val_pct=0.2, min_train_bars=100,
    )
    for fr in result.fold_results:
        gap = fr.val_start_idx - fr.train_end_idx
        assert gap == embargo, (
            f"Fold {fr.fold}: embargo ihlali gap={gap} != {embargo}"
        )


def test_wf_oof_columns():
    df = _make_wf_df(600)
    result = walk_forward_backtest(
        df, horizon="5m", feature_cols=_FEAT_COLS,
        model_factory=LogisticRegressionBaseline,
        bar_interval="5m",
        n_folds=3, val_pct=0.2, min_train_bars=100,
    )
    for col in (
        "open_time", "y_true", "y_pred", "future_return",
        "proba_up", "proba_down", "proba_no_trade",
    ):
        assert col in result.oof_df.columns, f"OOF eksik kolon: {col}"


def test_wf_summary_keys():
    df = _make_wf_df(600)
    result = walk_forward_backtest(
        df, horizon="5m", feature_cols=_FEAT_COLS,
        model_factory=LogisticRegressionBaseline,
        bar_interval="5m",
        n_folds=3, val_pct=0.2, min_train_bars=100,
    )
    for key in ("directional_accuracy", "expectancy_pct", "n_trades", "n_val_bars"):
        assert key in result.summary, f"Summary eksik: {key}"


def test_wf_missing_col_raises():
    df = _make_wf_df(600).drop(columns=["feat_a"])
    with pytest.raises(ValueError, match="eksik kolonlar"):
        walk_forward_backtest(
            df, horizon="5m", feature_cols=_FEAT_COLS,
            model_factory=LogisticRegressionBaseline,
            bar_interval="5m",
            n_folds=3, val_pct=0.2, min_train_bars=100,
        )


def test_wf_no_valid_fold_raises():
    """min_train_bars çok yüksek → fold yok → ValueError."""
    df = _make_wf_df(100)
    with pytest.raises(ValueError):
        walk_forward_backtest(
            df, horizon="5m", feature_cols=_FEAT_COLS,
            model_factory=LogisticRegressionBaseline,
            bar_interval="5m",
            n_folds=3, val_pct=0.2, min_train_bars=99999,
        )


def test_wf_oof_time_monotone():
    """OOF open_time sıralı olmalı (foldlar sırayla ekleniyor)."""
    df = _make_wf_df(600)
    result = walk_forward_backtest(
        df, horizon="5m", feature_cols=_FEAT_COLS,
        model_factory=LogisticRegressionBaseline,
        bar_interval="5m",
        n_folds=3, val_pct=0.2, min_train_bars=100,
    )
    times = result.oof_df["open_time"].values
    assert (np.diff(times) >= 0).all(), "OOF open_time monoton artan değil"
