"""
Regression test: ML_BOOST (high ML-confidence prediction, ml_score > 0.5) was
computed and logged in strategies/arbitrage_engine.py::_evaluate_market(), but
never actually applied to the trade's bet size — unlike its symmetric
counterpart, ML_CAUTION (ml_score < -0.5), which halves size:

    if ml_score < -0.5:
        original_ml = size
        size = size * 0.5
        logger.info(f"ML_CAUTION: ... {original_ml:.2f}->{size:.2f}")
    elif ml_score > 0.5:
        logger.info(f"ML_BOOST: ... (high confidence)")   # <-- size untouched

Two independent pieces of evidence show this was never intended to be a
no-op: the log line is literally named "ML_BOOST" (paired 1:1 with
"ML_CAUTION" just above it, which *does* resize), and
strategies/ml_classifier.py's own `_load_model()` warns, when scikit-learn is
unavailable, that "ml_score will stay 0.0 (no ML_CAUTION/ML_BOOST sizing
effect)" — documenting an ML_BOOST sizing effect that the code never actually
implements. The 86th daily review's commit message repeats the same
assumption ("TradeClassifier.predict() ... wired into live Kelly sizing via
ML_CAUTION/ML_BOOST/..."). A high-confidence ML prediction (ml_score > 0.5)
therefore never influenced a live bet's size at all, silently discarding half
of the ML gate's intended effect.

Fix: mirror ML_CAUTION's magnitude (0.5x reduction) with a symmetric 1.5x
increase for ML_BOOST.

This test drives the real analyze() -> _evaluate_market() pipeline (same
harness as test_golden_hour_boost_bypasses_max_bet_cap.py), forces Kelly to
recommend a small size (which the $3 KELLY_FLOOR then raises to exactly
$3.00, deterministically, regardless of any other confidence multiplier),
picks a wall-clock hour outside both GOLDEN_HOURS and GOOD_HOURS so that
boost can't be confused with this one, and forces ml_score=0.9 (via a mocked
TradeClassifier.predict). It asserts the final signal size is actually
boosted above the $3.00 floor (previously it stayed frozen at exactly $3.00).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

import strategies.arbitrage_engine as ae

# 2026-07-01 13:30 UTC == 09:30 America/New_York (DST, UTC-4) -> NOT golden
# hour ({17,18,19}) and NOT good hour ({11,15,3,4}).
_FIXED_UTC = datetime(2026, 7, 1, 13, 30, tzinfo=timezone.utc)


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _FIXED_UTC if tz is not None else _FIXED_UTC.replace(tzinfo=None)


def _fake_market(cid, question, ask, minutes=60):
    end = (_FIXED_UTC + timedelta(minutes=minutes)).isoformat()
    return {
        "condition_id": cid, "question": question,
        "best_ask": ask, "best_bid": round(ask * 0.98, 4),
        "volume": 50000, "endDate": end,
        "yes_token_id": f"YES-{cid}", "no_token_id": f"NO-{cid}",
    }


def _make_feed():
    from agents.binance_feed import BinanceFeed
    feed = BinanceFeed()
    feed._cache = {
        "BTCUSDT": {
            "price": 70000.0,
            "ob_imbalance": 0.4,
            "intervals": {
                "5m":  {"change_pct": 0.50, "trend_pct": 0.9, "volume_ratio": 4.0, "rsi": 65.0, "momentum": 3},
                "15m": {"change_pct": 0.50, "trend_pct": 0.9, "volume_ratio": 4.0, "rsi": 65.0, "momentum": 3},
                "1h":  {"change_pct": 0.50, "trend_pct": 0.9, "volume_ratio": 4.0, "rsi": 65.0, "momentum": 3},
                "4h":  {"change_pct": 0.50, "trend_pct": 0.9, "volume_ratio": 4.0, "rsi": 65.0, "momentum": 3},
            },
        }
    }
    feed.refresh = AsyncMock()
    feed.get_mtf_consensus = lambda sym: {"aligned": False, "direction": "NEUTRAL"}
    feed.get_cross_exchange_signal = lambda sym: {"signal": "NEUTRAL", "boost": 0.0}
    return feed


@pytest.mark.asyncio
async def test_high_ml_confidence_actually_increases_bet_size(monkeypatch):
    monkeypatch.setattr(ae, "datetime", _FixedDatetime)

    import httpx
    from core.polymarket_client import PolymarketClient
    from strategies.arbitrage_engine import ArbitrageEngine

    client = PolymarketClient.__new__(PolymarketClient)
    client._clob = None
    client.session = httpx.AsyncClient()

    market = _fake_market(
        "btc-ml-boost-test", "Bitcoin up or down? 9:25AM-9:30AM ET", ask=0.40,
    )

    engine = ArbitrageEngine(
        http_session=client.session, binance_feed=_make_feed(),
        smart_trader_tracker=None, latency_arb=None,
    )
    engine.min_edge = 0.01

    # Force Kelly to recommend a tiny size — well under the $3 KELLY_FLOOR —
    # so the floor deterministically raises it to exactly $3.00 regardless of
    # CONFIDENCE_MULT's exact value. High ML confidence (0.9 > 0.5) should
    # then boost that $3.00 upward.
    engine.kelly.position_size = lambda **kwargs: 1.0
    engine.ml.predict = lambda params: 0.9

    signals = await engine.analyze([market], capital=1000.0)
    assert len(signals) > 0, "expected a signal outside golden/good hours with a forced high edge"

    size = signals[0].size
    assert size > 3.0, (
        f"ML_BOOST (ml_score=0.9 > 0.5) did not increase size above the "
        f"$3.00 KELLY_FLOOR — got ${size:.2f}. High ML confidence must have "
        f"a real sizing effect, symmetric to ML_CAUTION's 0.5x reduction."
    )
    # Symmetric 1.5x boost of the $3.00 floor, capped at $4.00 by the
    # pre-existing MAX_BET_CAP_POST_BOOST re-clamp (golden/good hour block).
    assert size == pytest.approx(4.0, abs=0.01), (
        f"expected ML_BOOST to raise $3.00 by 1.5x=$4.50 and then be capped "
        f"at the $4.00 hard ceiling; got ${size:.2f}"
    )
