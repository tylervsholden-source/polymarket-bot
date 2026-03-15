"""
crypto_directional/backtests/metrics.py

Backtest değerlendirme metrikleri.
Tüm fonksiyonlar saf numpy/pandas/sklearn — yan etki yok.
"""
from __future__ import annotations

import math

import numpy as np
from sklearn.metrics import precision_recall_fscore_support


def compute_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    future_returns: np.ndarray,
) -> dict[str, float]:
    """
    Fold veya OOF metrikleri hesaplar.

    Parametreler
    ------------
    y_true         : str ndarray — gerçek label (UP/DOWN/NO_TRADE)
    y_pred         : str ndarray — model tahmini (UP/DOWN/NO_TRADE)
    future_returns : float ndarray — future_return_{H} (label_generator çıktısı)

    Döndürür
    --------
    dict anahtarları:
        precision_{down,no_trade,up}  — sınıf bazlı precision
        recall_{down,no_trade,up}     — sınıf bazlı recall
        f1_{down,no_trade,up}         — sınıf bazlı F1
        directional_accuracy          — UP+DOWN tahminleri içinde doğru yön oranı
        expectancy_pct                — trade başına beklenen getiri; NaN if no trades
        sharpe                        — trade getirilerinin Sharpe oranı (unannualized); NaN if <2 trades
        n_trades                      — UP+DOWN tahmin sayısı
        n_val_bars                    — toplam bar sayısı
        no_trade_rate                 — NO_TRADE tahmin oranı
        up_rate                       — UP tahmin oranı
        down_rate                     — DOWN tahmin oranı

    Expectancy tanımı
    -----------------
    UP  tahmini → long  → trade_return = +future_return
    DOWN tahmini → short → trade_return = -future_return
    Yüksek min_confidence filtresi sonrası NO_TRADE'e düşen tahminler dahil değil.
    """
    y_true         = np.asarray(y_true, dtype=str)
    y_pred         = np.asarray(y_pred, dtype=str)
    future_returns = np.asarray(future_returns, dtype=float)

    labels = ["DOWN", "NO_TRADE", "UP"]
    prec, rec, f1, _ = precision_recall_fscore_support(
        y_true, y_pred, labels=labels, zero_division=0.0,
    )

    n = len(y_true)

    # Trade predictions: UP veya DOWN
    trade_mask = np.isin(y_pred, ["UP", "DOWN"])
    n_trades   = int(trade_mask.sum())

    # Directional accuracy
    dir_acc = float("nan")
    if n_trades > 0:
        dir_acc = float((y_pred[trade_mask] == y_true[trade_mask]).mean())

    # Expectancy ve Sharpe
    expectancy = float("nan")
    sharpe     = float("nan")
    if n_trades > 0:
        trade_returns = np.where(
            y_pred[trade_mask] == "UP",
            future_returns[trade_mask],        # long: +return
            -future_returns[trade_mask],       # short: -return
        )
        expectancy = float(np.nanmean(trade_returns))
        if n_trades >= 2:
            std = float(np.nanstd(trade_returns, ddof=1))
            if std > 0:
                sharpe = expectancy / std

    return {
        "precision_down":     float(prec[0]),
        "recall_down":        float(rec[0]),
        "f1_down":            float(f1[0]),
        "precision_no_trade": float(prec[1]),
        "recall_no_trade":    float(rec[1]),
        "f1_no_trade":        float(f1[1]),
        "precision_up":       float(prec[2]),
        "recall_up":          float(rec[2]),
        "f1_up":              float(f1[2]),
        "directional_accuracy": dir_acc,
        "expectancy_pct":       expectancy,
        "sharpe":               sharpe,
        "n_trades":             float(n_trades),
        "n_val_bars":           float(n),
        "no_trade_rate":        float((y_pred == "NO_TRADE").mean()),
        "up_rate":              float((y_pred == "UP").mean()),
        "down_rate":            float((y_pred == "DOWN").mean()),
    }


def aggregate_metrics(fold_metrics: list[dict[str, float]]) -> dict[str, float]:
    """
    Fold metriklerini birleştirir.

    n_trades, n_val_bars → toplam (sum).
    Diğer tüm metrikler → macro ortalama (NaN foldlar atlanır).
    """
    if not fold_metrics:
        return {}

    _sum_keys = {"n_trades", "n_val_bars"}

    all_keys: set[str] = set()
    for m in fold_metrics:
        all_keys.update(m.keys())

    result: dict[str, float] = {}
    for key in sorted(all_keys):
        if key in _sum_keys:
            result[key] = float(sum(m.get(key, 0.0) for m in fold_metrics))
        else:
            vals = [
                m[key] for m in fold_metrics
                if key in m and not math.isnan(m[key])
            ]
            result[key] = float(np.mean(vals)) if vals else float("nan")

    return result
