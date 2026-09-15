"""
Regression test: GOLDEN_HOUR/GOOD_HOUR size boosts run AFTER the MAX_BET_CAP
clamp in strategies/arbitrage_engine.py::_evaluate_market(), so they silently
push the final bet size back above the documented hard cap.

Bug: the code applies the cap first —

    # ── MAX BET CAP ──────────────────────────────────────────────────
    _MAX_BET = 4.0
    if size > _MAX_BET:
        size = _MAX_BET
    ...
    # ── GOLDEN HOUR BOOST ────────────────────────────────────────────
    # These hours have highest edge — boost Kelly by 1.3x (capped at max_bet).
    if _gh_hour in _GOLDEN_HOURS:
        size = size * 1.30
    elif _gh_hour in _GOOD_HOURS:
        size = size * 1.15

— but never re-applies `_MAX_BET` after multiplying, even though the boost's
own comment says the result is "capped at max_bet". Any Kelly-sized bet that
already hit the $4.00 cap (routine once capital is more than a few hundred
dollars, since Kelly recommends far more than $4 at that point) leaves the
golden-hour block at $4.00 * 1.30 = $5.20 — a 30% oversized position placed
with real capital, entirely outside the bot's own documented per-trade risk
ceiling, and with no test previously locking the cap as a true ceiling.

This test drives the real analyze() -> _evaluate_market() pipeline with a
mocked Kelly size (deliberately far above the cap) and a neutral ML score, at
a wall-clock time inside the 5-8PM ET "golden hour" window, and asserts the
final signal size never exceeds $4.00.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

import strategies.arbitrage_engine as ae

# 2026-07-01 21:30 UTC == 17:30 America/New_York (DST, UTC-4) -> golden hour.
_FIXED_UTC = datetime(2026, 7, 1, 21, 30, tzinfo=timezone.utc)


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
async def test_golden_hour_boost_does_not_exceed_max_bet_cap(monkeypatch):
    monkeypatch.setattr(ae, "datetime", _FixedDatetime)

    import httpx
    from core.polymarket_client import PolymarketClient
    from strategies.arbitrage_engine import ArbitrageEngine

    client = PolymarketClient.__new__(PolymarketClient)
    client._clob = None
    client.session = httpx.AsyncClient()

    market = _fake_market(
        "btc-golden-hour-test", "Bitcoin up or down? 5:25PM-5:30PM ET", ask=0.40,
    )

    engine = ArbitrageEngine(
        http_session=client.session, binance_feed=_make_feed(),
        smart_trader_tracker=None, latency_arb=None,
    )
    engine.min_edge = 0.01

    # Isolate the cap-vs-boost ordering bug: force Kelly to recommend far more
    # than the $4 cap (realistic once capital exceeds a few hundred dollars),
    # and force ML to a neutral score so ML_CAUTION doesn't mask the overflow.
    engine.kelly.position_size = lambda **kwargs: 50.0
    engine.ml.predict = lambda params: 0.0

    signals = await engine.analyze([market], capital=1000.0)
    assert len(signals) > 0, "expected a signal during golden hour with a forced high edge"

    size = signals[0].size
    assert size <= 4.0, (
        f"GOLDEN_HOUR boost pushed size to ${size:.2f}, above the documented "
        f"$4.00 MAX_BET_CAP ceiling (comment: 'capped at max_bet')"
    )
