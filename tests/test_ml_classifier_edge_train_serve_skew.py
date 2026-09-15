"""
Regression test: TradeClassifier._extract_features() (training) must use the
same "edge" quantity as _extract_features_live() (live inference), or the
model is trained on one feature and scored against a different one.

Bug: _extract_features() ignored the real `edge` field that
core/position_manager.py::add_position() has stored on every real closed
trade since the 28th daily review (18de444), and instead recomputed
`edge = abs(entry_price - 0.5)` — a "distance from 0.5" proxy that measures
how big a favorite the market considered the outcome, not the strategy's
actual Bayesian-vs-price mispricing edge. _extract_features_live() (used by
predict(), called every live cycle from strategies/arbitrage_engine.py to
gate Kelly bet sizing via ML_CAUTION) reads the real edge:
`edge = float(params.get("edge", 0.0))`.

Since both feature vectors feed the same trained slot (index 3, "edge"),
the model learns a relationship between "distance from 0.5" and win/loss,
then at inference is scored against a completely different quantity (the
real mispricing edge) with no consistent relationship to what it learned —
corrupting ml_score for every real trade.

Concrete example: a real trade opened with entry_price=0.85 and a genuine
signal edge of 0.07 must train on edge=0.07 (what the live model will later
see for a similarly-priced trade), not edge=0.35 (abs(0.85-0.5)).
"""
from __future__ import annotations

from strategies.ml_classifier import TradeClassifier


def _closed_trade(entry_price: float, edge: float | None, outcome: str = "YES") -> dict:
    trade = {
        "question": "Bitcoin Up or Down - March 22, 12:30PM-12:45PM ET",
        "outcome": outcome,
        "entry_price": entry_price,
        "result": "WIN",
    }
    if edge is not None:
        trade["edge"] = edge
    return trade


def test_extract_features_uses_real_stored_edge_not_distance_from_half():
    clf = TradeClassifier()
    trade = _closed_trade(entry_price=0.85, edge=0.07)

    features = clf._extract_features(trade)

    assert features is not None
    edge_idx = clf._feature_names().index("edge")
    assert features[edge_idx] == 0.07, (
        "training must use the real signal edge stored on the trade "
        "(core/position_manager.py::add_position()), matching what "
        "_extract_features_live() feeds the same model slot at inference — "
        f"got {features[edge_idx]}, expected the stored edge 0.07, not the "
        f"abs(entry_price - 0.5) proxy ({abs(0.85 - 0.5)})"
    )


def test_extract_features_falls_back_to_proxy_for_legacy_trades_without_edge():
    clf = TradeClassifier()
    trade = _closed_trade(entry_price=0.85, edge=None)

    features = clf._extract_features(trade)

    assert features is not None
    edge_idx = clf._feature_names().index("edge")
    assert features[edge_idx] == abs(0.85 - 0.5), (
        "legacy closed trades with no stored edge field must still fall "
        "back to the distance-from-0.5 proxy instead of being dropped "
        "from the training set"
    )


def test_extract_features_matches_extract_features_live_for_same_edge():
    clf = TradeClassifier()
    closed_trade = _closed_trade(entry_price=0.55, edge=0.12)
    live_params = {
        "asset": "BTC",
        "direction": "YES",
        "entry_price": 0.55,
        "edge": 0.12,
        "hour_et": 12,
        "minute_et": 30,
        "window_minutes": 15,
    }

    train_features = clf._extract_features(closed_trade)
    live_features = clf._extract_features_live(live_params)

    edge_idx = clf._feature_names().index("edge")
    assert train_features is not None and live_features is not None
    assert train_features[edge_idx] == live_features[edge_idx] == 0.12, (
        "the same real edge must land in the same feature slot whether the "
        "trade came from training data (closed positions) or a live "
        "predict() call — otherwise the model is trained and served on two "
        "different quantities"
    )
