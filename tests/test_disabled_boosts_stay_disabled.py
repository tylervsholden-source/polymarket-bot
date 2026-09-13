"""
Regression test: SPIKE / MTF / LEAD_LAG boosts must stay disabled.

Bug: strategies/arbitrage_engine.py has a block (introduced in the initial
"full bot update" commit) that explicitly resets bayesian_prob to wipe out
all external boosts, with a comment listing everything it disables:

    # Kapatılan: SPIKE, MTF, LEAD_LAG, FUNDING, LS_RATIO, LIQUIDATION,
    #            SPX_CORR, FNG, ENHANCED (multi-exchange, options, whale, social)
    bayesian_prob = _pre_boost_prob  # Tum boost'lari sifirla, sadece core Bayesian

FUNDING, LS_RATIO, LIQUIDATION, SPX_CORR, FNG and ENHANCED were all correctly
disabled by commenting out their `bayesian_prob = ... + boost` line. SPIKE,
MTF and LEAD_LAG were NOT: their boost-application lines ran unchanged right
after the reset, silently re-enabling exactly the three signals the same
comment says are off. This matters because the reset exists specifically
because these external boosts documentedly created a persistent bearish
push (hurting NO win rate, per CLAUDE.md's "Kritik Kesifler").

This test builds the same strong-bullish BTC scenario used by
test_execution_path.py::test_full_sim_execution_chain, runs it once with a
neutral latency_arb/binance_feed (baseline), then again with a
latency_arb.get_spike_boost / binance_feed.get_mtf_consensus /
binance_feed.get_cross_exchange_signal each forced to return a large boost.
The resulting bayesian_prob must be identical in both runs.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    import core.position_manager as pm_mod
    monkeypatch.setattr(pm_mod, "DATA_FILE", tmp_path / "positions.json")
    monkeypatch.setenv("INITIAL_CAPITAL", "10.0")


def _fake_market(cid, question, ask, minutes=60):
    from datetime import datetime, timezone, timedelta
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
    return feed


@pytest.mark.asyncio
async def test_spike_mtf_lead_lag_boosts_do_not_move_bayesian_prob():
    import httpx
    from core.polymarket_client import PolymarketClient
    from strategies.arbitrage_engine import ArbitrageEngine

    client = PolymarketClient.__new__(PolymarketClient)
    client._clob = None
    client.session = httpx.AsyncClient()

    market = _fake_market(
        "btc-boost-test", "Bitcoin up or down? 12:00PM-12:15PM ET", ask=0.40,
    )

    # ── Baseline: neutral spike/mtf/lead-lag sources ──
    baseline_feed = _make_feed()
    baseline_feed.get_mtf_consensus = lambda sym: {"aligned": False, "direction": "NEUTRAL"}
    baseline_feed.get_cross_exchange_signal = lambda sym: {"signal": "NEUTRAL", "boost": 0.0}

    baseline_engine = ArbitrageEngine(
        http_session=client.session, binance_feed=baseline_feed,
        smart_trader_tracker=None, latency_arb=None,
    )
    baseline_engine.min_edge = 0.01

    baseline_signals = await baseline_engine.analyze([market], capital=5.0)
    assert len(baseline_signals) > 0
    baseline_prob = baseline_signals[0].bayesian_prob

    # ── Boosted: spike + MTF + lead-lag all fire hard in the SAME direction ──
    boosted_feed = _make_feed()
    boosted_feed.get_mtf_consensus = lambda sym: {
        "aligned": True, "agreement": 1.0, "direction": "UP", "boost": 0.03,
        "details": {"5m": 0.5, "15m": 0.5, "1h": 0.5},
    }
    boosted_feed.get_cross_exchange_signal = lambda sym: {
        "signal": "BULLISH", "boost": 0.03,
        "binance_price": 70000.0, "bitstamp_price": 69950.0, "spread_pct": 0.07,
    }

    class _FakeLatencyArb:
        def get_spike_boost(self, coin, max_age_sec=30.0):
            return 0.02  # max magnitude per get_spike_boost's own cap

    boosted_engine = ArbitrageEngine(
        http_session=client.session, binance_feed=boosted_feed,
        smart_trader_tracker=None, latency_arb=_FakeLatencyArb(),
    )
    boosted_engine.min_edge = 0.01

    boosted_signals = await boosted_engine.analyze([market], capital=5.0)
    assert len(boosted_signals) > 0
    boosted_prob = boosted_signals[0].bayesian_prob

    assert boosted_prob == pytest.approx(baseline_prob), (
        f"SPIKE/MTF/LEAD_LAG boosts must stay disabled (per the 'Kapatilan' "
        f"reset), but bayesian_prob moved from {baseline_prob} to {boosted_prob}"
    )
