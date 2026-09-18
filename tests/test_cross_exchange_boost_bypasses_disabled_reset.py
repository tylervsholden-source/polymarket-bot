"""
Regression test: strategies/arbitrage_engine.py fed the live Cross-Exchange
Lead-Lag (Binance vs. Bitstamp) spread signal straight into
BayesianEstimator.estimate() as `cross_exchange_boost`, even though the
engine's own "Kapatılan" (disabled) comment block explicitly lists LEAD_LAG
as one of the external boosts that must have zero effect on bayesian_prob:

    # TÜM EXTERNAL BOOST'LAR DEVRE DIŞI — 5dk window için macro sinyaller zararlı
    # ...
    # Sadece Bayesian core (spot price action) kalıyor
    # Kapatılan: SPIKE, MTF, LEAD_LAG, FUNDING, LS_RATIO, LIQUIDATION,
    #            SPX_CORR, FNG, ENHANCED (multi-exchange, options, whale, social)
    bayesian_prob = _pre_boost_prob  # Tum boost'lari sifirla, sadece core Bayesian

That reset (and the arbitrage_engine-level "CROSS-EXCHANGE LEAD-LAG" block a
bit further down, whose `bayesian_prob = ... + boost` line is correctly
commented out) can only ever wipe out a boost applied as
`bayesian_prob = bayesian_prob + boost` *after* the reset point.

But _evaluate_market() computed the SAME underlying signal much earlier
(`_cross_boost = self.binance_feed.get_cross_exchange_signal(sym)["boost"]`)
and passed it straight into `self.bayesian.estimate(cross_exchange_boost=
_cross_boost, ...)`. Inside strategies/bayesian.py, that value is baked
directly into raw_signal ("raw_signal += cross_exchange_boost * 0.8") before
the log-odds update that produces bayesian_prob in the first place — i.e.
before `bayesian_prob` (and therefore `_pre_boost_prob`) even exists. The
reset a few dozen lines later has nothing to undo: the boost is already
inside the number it is resetting *to*.

Net effect: LEAD_LAG silently kept moving bayesian_prob (and therefore the
edge gate and Kelly sizing) on every live cycle for every market with a
Binance/Bitstamp price spread, exactly the "persistent bearish push" CLAUDE.md's
own findings blame for hurting NO win rate — never actually removed for this
signal, only for its redundant, already-inert sibling application further
down in the same function.

This is verified directly at the wiring level (what `_evaluate_market()`
passes to `BayesianEstimator.estimate()`), not indirectly through the
resulting probability: a strongly one-sided setup like
tests/test_disabled_boosts_stay_disabled.py's saturates the Bayesian model's
internal ±1.5 signal clamp, so the SPIKE/MTF/LEAD_LAG equality check there
passes whether or not the boost is actually disabled. Asserting the raw
`cross_exchange_boost` kwarg is immune to that coincidence.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    import core.position_manager as pm_mod
    monkeypatch.setattr(pm_mod, "DATA_FILE", tmp_path / "positions.json")
    monkeypatch.setenv("INITIAL_CAPITAL", "10.0")


def _fake_market(cid, question, ask, minutes=60):
    end = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
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
    # A strong, real-looking Binance-leads-Bitstamp spread — the exact shape
    # get_cross_exchange_signal() returns when the two exchanges diverge.
    feed.get_cross_exchange_signal = lambda sym: {
        "signal": "BULLISH", "boost": 0.03,
        "binance_price": 70000.0, "bitstamp_price": 69950.0, "spread_pct": 0.07,
    }
    return feed


@pytest.mark.asyncio
async def test_cross_exchange_boost_never_reaches_bayesian_core():
    import httpx
    from core.polymarket_client import PolymarketClient
    from strategies.arbitrage_engine import ArbitrageEngine

    client = PolymarketClient.__new__(PolymarketClient)
    client._clob = None
    client.session = httpx.AsyncClient()

    market = _fake_market(
        "btc-lead-lag-wiring-test", "Bitcoin up or down? 12:00PM-12:15PM ET", ask=0.40,
    )

    engine = ArbitrageEngine(
        http_session=client.session, binance_feed=_make_feed(),
        smart_trader_tracker=None, latency_arb=None,
    )
    engine.min_edge = 0.01

    seen_cross_exchange_boosts = []
    real_estimate = engine.bayesian.estimate

    def _spy_estimate(*args, **kwargs):
        seen_cross_exchange_boosts.append(kwargs.get("cross_exchange_boost", 0.0))
        return real_estimate(*args, **kwargs)

    engine.bayesian.estimate = _spy_estimate

    signals = await engine.analyze([market], capital=5.0)

    assert signals, "expected at least one signal from this strongly bullish setup"
    assert seen_cross_exchange_boosts, "BayesianEstimator.estimate() was never called"
    assert all(b == 0.0 for b in seen_cross_exchange_boosts), (
        "cross_exchange_boost fed into BayesianEstimator.estimate() must always "
        "be 0.0 -- LEAD_LAG is documented as 'Kapatılan' (disabled) and its own "
        "in-engine application block is correctly commented out, but "
        f"{seen_cross_exchange_boosts} was passed straight into the Bayesian "
        "core, bypassing the 'bayesian_prob = _pre_boost_prob' reset entirely "
        "(the boost was baked into bayesian_prob's own inputs before that "
        "reset point even runs)."
    )
