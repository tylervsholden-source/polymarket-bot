"""
Regression test: TradeClassifier must not train on NEUTRAL (unfilled/
refunded) closed trades as if they were losses.

Bug: strategies/ml_classifier.py::TradeClassifier.train() built its
training labels purely from a WIN-vs-everything-else check:

    result = 1 if trade.get("result") == "WIN" else 0

`core/position_manager.py::_close_position_neutral()` closes a position
with `pnl=0.0` and `result="NEUTRAL"` whenever a GTC order never fills
before the market ends (a routine, expected occurrence — see
`update_positions()`'s "Emir dolmadı (NEUTRAL)" / "FORCE_CLOSE_TIMEOUT ...
NEUTRAL" paths). Because `result == "WIN"` is False for a NEUTRAL close,
every one of these non-events was trained as label 0 — indistinguishable
from a real LOSS — even though nothing about the bot's directional call
was actually wrong; the order simply never got filled. This is the same
"NEUTRAL-counted-as-LOSS" bug class already fixed in
agents/trade_analyzer.py (25th daily review, #46) and
agents/autonomous_engine.py (23rd daily review, #44); ml_classifier.py had
its own independent, unfixed copy.

This directly reaches the live trading path: `strategies/arbitrage_engine.py`
instantiates `self.ml = TradeClassifier()` and calls `self.ml.predict(...)`
on every candidate market every cycle. Whenever `ml_score < -0.5` the live
Kelly bet `size` is HALVED ("ML_CAUTION"). A model trained on markets/hours/
assets whose orders merely failed to fill — mislabeled as losses — learns
spurious associations and systematically under-sizes (or wrongly boosts)
real trades, unrelated to actual trading skill or edge.

Fix: extracted the label-building loop into `_build_training_set()`, which
uses a new `_result_label()` helper returning 1 for WIN, 0 for LOSS, and
None for anything else (NEUTRAL, or a missing/unknown `result`) — callers
skip None entirely instead of mislabeling it as a LOSS.

This test exercises `_result_label()` and `_build_training_set()` directly
(not `train()`, which requires scikit-learn — an optional, not-always-
installed dependency this repo does not even list in requirements.txt) so
it runs regardless of whether scikit-learn is available.
"""
from __future__ import annotations

from strategies.ml_classifier import TradeClassifier


def _closed_trade(result: str, **overrides) -> dict:
    """A minimally valid closed trade that _extract_features() will accept."""
    trade = {
        "question": "Bitcoin Up or Down - March 16, 7:10PM-7:15PM ET",
        "outcome": "YES",
        "entry_price": 0.55,
        "result": result,
        "pnl": {"WIN": 4.5, "LOSS": -5.0, "NEUTRAL": 0.0}.get(result, 0.0),
    }
    trade.update(overrides)
    return trade


class TestResultLabel:
    def test_win_labeled_1(self):
        assert TradeClassifier._result_label(_closed_trade("WIN")) == 1

    def test_loss_labeled_0(self):
        assert TradeClassifier._result_label(_closed_trade("LOSS")) == 0

    def test_neutral_is_not_a_loss(self):
        """The core bug: NEUTRAL must NOT map to 0 (LOSS)."""
        label = TradeClassifier._result_label(_closed_trade("NEUTRAL"))
        assert label is None, (
            f"NEUTRAL (unfilled/refunded, pnl=0) close mapped to {label!r} — "
            "must be excluded from training, not labeled as a LOSS (0)"
        )

    def test_missing_result_is_excluded(self):
        trade = _closed_trade("WIN")
        del trade["result"]
        assert TradeClassifier._result_label(trade) is None

    def test_unknown_result_is_excluded(self):
        assert TradeClassifier._result_label(_closed_trade("EXPIRED")) is None


class TestBuildTrainingSet:
    def test_neutral_trades_excluded_from_training_set(self):
        clf = TradeClassifier()
        closed = [
            _closed_trade("WIN"),
            _closed_trade("LOSS"),
            _closed_trade("NEUTRAL"),
            _closed_trade("NEUTRAL"),
            _closed_trade("WIN"),
        ]
        X, y = clf._build_training_set(closed)

        # Only the 3 WIN/LOSS trades should survive — the 2 NEUTRAL closes
        # must be dropped entirely, not folded into label 0.
        assert len(X) == 3
        assert len(y) == 3
        assert sorted(y) == [0, 1, 1]

    def test_win_rate_not_diluted_by_neutrals(self):
        """A pure regression check on the exact scenario the bug caused:
        a coin/hour whose orders never filled (NEUTRAL) must not drag
        down the trained win rate for markets that actually resolved WIN.
        """
        clf = TradeClassifier()
        # 4 real WINs, 1 real LOSS, 5 NEUTRAL (unfilled) — true WR among
        # resolved trades is 4/5 = 80%.
        closed = (
            [_closed_trade("WIN") for _ in range(4)]
            + [_closed_trade("LOSS")]
            + [_closed_trade("NEUTRAL") for _ in range(5)]
        )
        X, y = clf._build_training_set(closed)

        assert len(y) == 5, "NEUTRAL closes must not appear in the training set"
        win_rate = sum(y) / len(y)
        assert win_rate == 0.8, (
            f"win_rate={win_rate:.2f} — NEUTRAL closes diluted/corrupted the "
            "true WIN/LOSS ratio (pre-fix this was 4/10 = 0.4)"
        )

    def test_all_neutral_yields_empty_training_set(self):
        clf = TradeClassifier()
        closed = [_closed_trade("NEUTRAL") for _ in range(5)]
        X, y = clf._build_training_set(closed)
        assert X == []
        assert y == []
