"""
Regression test: the ±0.04 "AGGREGATE BOOST CAP" in strategies/arbitrage_engine.py
never actually bounded SmartMoney + TopTrader + OB_Depth + KalshiArb combined,
because its baseline (`_pre_boost_prob`) was captured AFTER those four boosts
had already been applied to `bayesian_prob`.

Bug (strategies/arbitrage_engine.py::_evaluate_market()):

    _pre_boost_prob = bayesian_prob      # captured AFTER SmartMoney/TopTrader/
                                          # OB_Depth/KalshiArb already ran (16th
                                          # daily review moved the capture here
                                          # on purpose, to stop the "Kapatılan"
                                          # reset a few lines below from wiping
                                          # those four boosts too).
    ...
    bayesian_prob = _pre_boost_prob      # reset disabled macro signals only

    ...
    # ── AGGREGATE BOOST CAP ──
    _total_external_boost = bayesian_prob - _pre_boost_prob   # always ~0 for
                                                                # SM/TT/OB/Kalshi!
    if abs(_total_external_boost) > _TOTAL_BOOST_CAP:          # never trips
        ...

Individually each of these boosts is capped (SmartMoney ±0.02, TopTrader
±0.03, OB_Depth ±0.03, KalshiArb ±0.02), but nothing capped their *combined*
effect — despite the code's own comment: "Allow external boosts (whale,
smart trader, orderflow) to modify probability but cap total boost at ±0.04
to prevent runaway." When several of these signals agree (a realistic,
not-rare scenario — they are all trend-following), bayesian_prob could be
pushed by up to +0.07 (0.02+0.03+0.02, or +0.10 with OB_Depth too) in one
cycle — comfortably blowing through the intended ±0.04 ceiling and even the
separate hard 0.65 confidence cap set a few lines above it (also justified
by real losses: "DOGE P=0.874→LOSS, HYPE P=0.917→LOSS").

This directly inflates `edge = bayesian_prob - price`, which drives
direction selection and Kelly position sizing for every live trade — an
uncapped combined boost means oversized bets on overconfident probability
estimates.

This test builds one BTC market and runs ArbitrageEngine.analyze() twice:
once with all external trackers neutral (baseline), once with SmartMoney
(+0.02), TopTrader (+0.03) and KalshiArb (+0.02) all agreeing in the same
(bullish) direction — a combined +0.07 raw boost. Pre-fix, bayesian_prob
moves by the full +0.07 (aggregate cap never fires). Post-fix, it must move
by at most the documented ±0.04.
"""
from __future__ import annotations

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
    from unittest.mock import AsyncMock
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
    async def refresh(self):
        pass

    def get_signal(self, condition_id):
        return {"total_traders": 5, "signal": 1.0, "buyers": ["w1", "w2"]}


class _BullishTopTrader:
    """Stub matching the get_boost(condition_id) interface consumed live."""

    def get_boost(self, condition_id):
        return 0.03

    def get_signal(self, condition_id):
        return None


class _BullishKalshi:
    """Stub matching the get_edge_adjustment(asset, poly_yes) interface consumed live."""

    def get_edge_adjustment(self, asset, poly_yes):
        return 0.02


@pytest.mark.asyncio
async def test_combined_external_boosts_respect_aggregate_cap():
    import httpx
    from core.polymarket_client import PolymarketClient
    from strategies.arbitrage_engine import ArbitrageEngine

    client = PolymarketClient.__new__(PolymarketClient)
    client._clob = None
    client.session = httpx.AsyncClient()

    market = _fake_market(
        "btc-boost-cap-test", "Bitcoin up or down? 12:00PM-12:15PM ET", ask=0.40,
    )

    # ── Baseline: everything neutral ──
    baseline_engine = ArbitrageEngine(
        http_session=client.session, binance_feed=_make_feed(),
        smart_trader_tracker=_NeutralSmartTracker(), latency_arb=None,
    )
    baseline_engine.min_edge = 0.01
    baseline_signals = await baseline_engine.analyze([market], capital=5.0)
    assert len(baseline_signals) > 0
    baseline_prob = baseline_signals[0].bayesian_prob

    # ── Boosted: SmartMoney (+0.02) + TopTrader (+0.03) + KalshiArb (+0.02),
    #    all agreeing bullish — raw combined boost = +0.07 ──
    boosted_engine = ArbitrageEngine(
        http_session=client.session, binance_feed=_make_feed(),
        smart_trader_tracker=_BullishSmartTracker(),
        top_trader=_BullishTopTrader(),
        kalshi_arb=_BullishKalshi(),
        latency_arb=None,
    )
    boosted_engine.min_edge = 0.01
    boosted_signals = await boosted_engine.analyze([market], capital=5.0)
    assert len(boosted_signals) > 0
    boosted_prob = boosted_signals[0].bayesian_prob

    delta = boosted_prob - baseline_prob
    assert delta > 0, "combined bullish boosts should still move prob up"
    assert delta <= 0.04 + 1e-9, (
        "combined SmartMoney(+0.02) + TopTrader(+0.03) + KalshiArb(+0.02) = "
        f"+0.07 raw boost moved bayesian_prob by {delta:.4f}, exceeding the "
        "documented ±0.04 aggregate external-boost cap — the cap's baseline "
        "was captured after these four boosts already applied, making it a "
        "no-op for exactly the signals it was meant to bound."
    )
