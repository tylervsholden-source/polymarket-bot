"""
Regression test: the `window_minutes` feature fed to TradeClassifier.predict()
must match, per timeframe bucket, what TradeClassifier._parse_window() computes
at training time for a real question in that bucket — including 4h markets.

Bug (40th daily review): strategies/arbitrage_engine.py's ArbitrageEngine.analyze()
called `self.ml.predict({... "window_minutes": {"5m": 5, "15m": 15, "1h": 60}
.get(timeframe, 15), ...})` inline. This dict omitted the "4h" bucket even
though 4h up/down markets are explicitly live-traded (CLAUDE.md: "Tüm zaman
dilimlerine izin ver: 5m, 15m, 1h, 4h") and even though the *very same file*'s
_time_remaining_fraction() window_map correctly lists all four buckets
(5m/15m/1h/4h -> 300/900/3600/14400 seconds). Any 4h market silently fell
through to the dict's `.get(timeframe, 15)` default of 15 minutes.

strategies/ml_classifier.py::TradeClassifier.train() labels each historical
trade's window_minutes via _parse_window(question), which parses the market's
real start/end time out of the question text — for a genuine 4h market this
returns ~240, not 15. So live predict() calls for 4h signals fed the model a
window_minutes value 16x smaller than every 4h trade the model was trained on
— a classic train/serve feature skew. strategies/arbitrage_engine.py directly
halves the live Kelly bet size whenever the resulting ml_score < -0.5
(ML_CAUTION), so a systematically wrong window_minutes for every 4h signal
corrupts real position sizing for an entire timeframe class of live trades.

Fix: extracted the mapping into `_ml_window_minutes()` (mirroring the existing
`_momentum_decel_blocks_no()` isolated-for-testing pattern) and added the
missing "4h" -> 240 entry.
"""
from __future__ import annotations

from strategies.arbitrage_engine import _detect_timeframe, _ml_window_minutes
from strategies.ml_classifier import TradeClassifier

# Realistic market questions, one per timeframe bucket.
Q_5M = "Bitcoin Up or Down - March 16, 7:10PM-7:15PM ET"
Q_15M = "Bitcoin Up or Down - March 16, 7:00PM-7:15PM ET"
Q_1H = "Bitcoin Up or Down - March 16, 7:00PM-8:00PM ET"
Q_4H = "Bitcoin Up or Down - March 16, 8:00AM-12:00PM ET"


class TestMlWindowMinutesMatchesTraining:
    """For every real timeframe bucket, the live feature must equal (or be
    reasonably close to) what training extracts from a genuine question in
    that bucket — the whole point of the feature is to be comparable."""

    def test_4h_timeframe_detected(self):
        # Sanity check the fixture actually exercises the 4h bucket.
        assert _detect_timeframe(Q_4H) == "4h"

    def test_4h_window_minutes_matches_training_parse(self):
        live_value = _ml_window_minutes(_detect_timeframe(Q_4H))
        trained_value = TradeClassifier._parse_window(Q_4H)

        assert trained_value == 240.0  # sanity: fixture really is a 4h window
        assert live_value == trained_value, (
            f"4h live window_minutes={live_value} != training window_minutes="
            f"{trained_value} — ml.predict() is being fed a value the model "
            "was never trained to see for 4h markets (pre-fix this fell "
            "through to the dict's default of 15)"
        )

    def test_5m_window_minutes_matches_training_parse(self):
        assert _ml_window_minutes(_detect_timeframe(Q_5M)) == TradeClassifier._parse_window(Q_5M)

    def test_15m_window_minutes_matches_training_parse(self):
        assert _ml_window_minutes(_detect_timeframe(Q_15M)) == TradeClassifier._parse_window(Q_15M)

    def test_1h_window_minutes_matches_training_parse(self):
        assert _ml_window_minutes(_detect_timeframe(Q_1H)) == TradeClassifier._parse_window(Q_1H)

    def test_unknown_timeframe_falls_back_to_15(self):
        assert _ml_window_minutes("unknown_bucket") == 15
