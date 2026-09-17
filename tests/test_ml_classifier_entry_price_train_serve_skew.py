"""
Regression test (68th daily review): TradeClassifier._extract_features()
(training) must use the same "entry_price" quantity as
_extract_features_live() (live inference), or the model is trained on one
feature and scored against a different one.

Bug: on a real closed position, `trade["entry_price"]` is the actual CLOB
fill price — core/position_manager.py::add_position() sets it from
order["price"], and core/polymarket_client.py::place_order() adds a
+0.01-0.03 price bump to that price for fill priority before sending the
order. _extract_features_live() (used by predict(), called every live cycle
from strategies/arbitrage_engine.py to gate Kelly bet sizing via ML_CAUTION/
ML_BOOST) is scored from the pre-bump signal price
(arbitrage_engine.py's `trade_price`, fed in as params["entry_price"] —
never bumped).

Since entry_price feeds three feature slots — the raw "entry_price" feature,
"price_distance_from_50", and the binary "is_favorite" — training on the
post-bump fill price while serving on the pre-bump signal price corrupts all
three for every real trade, most visibly for near-coinflip 5-minute up/down
markets (this bot's primary market type) where the bump-sized offset
(~0.01-0.03) can flip "is_favorite" right at the 0.5 boundary.

`signal_price` (stored on the position since this review, alongside `edge`)
is the pre-bump equivalent; closed trades that predate the field fall back
to `entry_price`, matching the fallback pattern already used for `edge`.
"""
from __future__ import annotations

from strategies.ml_classifier import TradeClassifier


def _closed_trade(entry_price: float, signal_price: float | None, outcome: str = "YES") -> dict:
    trade = {
        "question": "Bitcoin Up or Down - March 22, 12:30PM-12:45PM ET",
        "outcome": outcome,
        "entry_price": entry_price,
        "result": "WIN",
    }
    if signal_price is not None:
        trade["signal_price"] = signal_price
    return trade


def test_extract_features_uses_signal_price_not_bumped_fill_price():
    clf = TradeClassifier()
    # Real fill was bumped +0.02 above the signal price (0.48 -> 0.50).
    trade = _closed_trade(entry_price=0.50, signal_price=0.48)

    features = clf._extract_features(trade)

    assert features is not None
    price_idx = clf._feature_names().index("entry_price")
    favorite_idx = clf._feature_names().index("is_favorite")
    dist_idx = clf._feature_names().index("price_distance_from_50")

    assert features[price_idx] == 0.48, (
        "training must use the pre-bump signal price stored on the trade "
        "(core/position_manager.py::add_position(signal_price=...)), matching "
        f"what _extract_features_live() feeds at inference — got "
        f"{features[price_idx]}, expected 0.48, not the bumped fill price 0.50"
    )
    assert features[favorite_idx] == 0.0, (
        "at the true signal price (0.48) this trade was NOT the favorite — "
        "using the bumped fill price (0.50) would flip this binary feature"
    )
    assert features[dist_idx] == abs(0.48 - 0.5)


def test_extract_features_falls_back_to_entry_price_for_legacy_trades():
    clf = TradeClassifier()
    trade = _closed_trade(entry_price=0.85, signal_price=None)

    features = clf._extract_features(trade)

    assert features is not None
    price_idx = clf._feature_names().index("entry_price")
    assert features[price_idx] == 0.85, (
        "legacy closed trades with no stored signal_price field must still "
        "fall back to entry_price instead of being dropped from the "
        "training set"
    )


def test_extract_features_matches_extract_features_live_for_same_signal_price():
    clf = TradeClassifier()
    closed_trade = _closed_trade(entry_price=0.57, signal_price=0.55)
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

    price_idx = clf._feature_names().index("entry_price")
    favorite_idx = clf._feature_names().index("is_favorite")
    dist_idx = clf._feature_names().index("price_distance_from_50")

    assert train_features is not None and live_features is not None
    assert train_features[price_idx] == live_features[price_idx] == 0.55
    assert train_features[favorite_idx] == live_features[favorite_idx]
    assert train_features[dist_idx] == live_features[dist_idx]
