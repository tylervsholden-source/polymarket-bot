"""
Regression test: SmartMoney / TopTrader / OB_Depth / KalshiArb boosts must
actually reach bayesian_prob — they must NOT be silently wiped by the
"TÜM EXTERNAL BOOST'LAR DEVRE DIŞI" reset in strategies/arbitrage_engine.py.

Bug (16th daily review): `_pre_boost_prob = bayesian_prob` was captured
BEFORE the SmartMoney / TopTrader / OrderbookDepth / KalshiArb boost blocks
ran, but the reset line a bit further down (`bayesian_prob = _pre_boost_prob`)
unconditionally restored bayesian_prob to that pre-capture value. Its own
comment only lists SPIKE, MTF, LEAD_LAG, FUNDING, LS_RATIO, LIQUIDATION,
SPX_CORR, FNG, ENHANCED as "Kapatılan" (disabled) — SmartMoney/TopTrader/
OB_Depth/KalshiArb are not on that list and were never meant to be touched.
Because the capture happened too early, those four boosts were computed,
logged as if applied (e.g. "SmartMoney ... boost=+0.020 prob: X→Y"), and then
thrown away every single cycle — a fully silent no-op since the code this
review inherited from prior reviews (SPIKE/MTF/LEAD_LAG etc.) already
disables everything that runs AFTER the reset.

This also contradicts docs/architecture.md, which documents
"SmartTraderTracker -> +/-0.05 boost" as part of the live signal pipeline.

This test builds the same kind of scenario as
test_disabled_boosts_stay_disabled.py: a strong-bullish BTC market, run once
with a neutral SmartTraderTracker (baseline) and once with one that reports a
strong same-direction signal (boosted). Pre-fix, bayesian_prob is identical
in both runs (the boost never lands). Post-fix, the boosted run's
bayesian_prob must differ from the baseline by roughly the expected +0.02
(signal * 0.02 per the "reduced: 0.05 -> 0.02" comment), clamped by the
±0.04 aggregate cap.
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
    # Neutral for the (already-disabled) macro signals so they can't muddy the result.
    feed.get_mtf_consensus = lambda sym: {"aligned": False, "direction": "NEUTRAL"}
    feed.get_cross_exchange_signal = lambda sym: {"signal": "NEUTRAL", "boost": 0.0}
    return feed


class _NeutralSmartTracker:
    async def refresh(self):
        pass

    def get_signal(self, condition_id):
        return {"total_traders": 0, "signal": 0.0, "buyers": []}


class _BullishSmartTracker:
    """Strong same-direction smart-money signal — should push bayesian_prob up."""

    async def refresh(self):
        pass

    def get_signal(self, condition_id):
        return {"total_traders": 5, "signal": 1.0, "buyers": ["whale1", "whale2"]}


@pytest.mark.asyncio
async def test_smart_money_boost_actually_moves_bayesian_prob():
    import httpx
    from core.polymarket_client import PolymarketClient
    from strategies.arbitrage_engine import ArbitrageEngine

    client = PolymarketClient.__new__(PolymarketClient)
    client._clob = None
    client.session = httpx.AsyncClient()

    market = _fake_market(
        "btc-smartmoney-test", "Bitcoin up or down? 12:00PM-12:15PM ET", ask=0.40,
    )

    # ── Baseline: neutral SmartTraderTracker (no signal) ──
    baseline_engine = ArbitrageEngine(
        http_session=client.session, binance_feed=_make_feed(),
        smart_trader_tracker=_NeutralSmartTracker(), latency_arb=None,
    )
    baseline_engine.min_edge = 0.01

    baseline_signals = await baseline_engine.analyze([market], capital=5.0)
    assert len(baseline_signals) > 0
    baseline_prob = baseline_signals[0].bayesian_prob

    # ── Boosted: strong same-direction SmartTraderTracker signal ──
    boosted_engine = ArbitrageEngine(
        http_session=client.session, binance_feed=_make_feed(),
        smart_trader_tracker=_BullishSmartTracker(), latency_arb=None,
    )
    boosted_engine.min_edge = 0.01

    boosted_signals = await boosted_engine.analyze([market], capital=5.0)
    assert len(boosted_signals) > 0
    boosted_prob = boosted_signals[0].bayesian_prob

    assert boosted_prob != pytest.approx(baseline_prob), (
        "SmartTraderTracker boost (signal=+1.0, gated on volume_ratio>=1.0) must "
        "actually move bayesian_prob — it is NOT in the 'Kapatılan' (disabled) "
        f"list in arbitrage_engine.py, but baseline={baseline_prob} == "
        f"boosted={boosted_prob} (reset wiped it out)."
    )
    # Expected: +0.02 (signal * 0.02), possibly clamped by ±0.04 aggregate cap.
    assert boosted_prob > baseline_prob
    assert boosted_prob <= baseline_prob + 0.045
