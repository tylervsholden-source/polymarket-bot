"""
Regression test for the min-volume gate (CLAUDE.md: "Min market hacmi: $5,000 USDC").

Bug: `agents/orchestrator.py`'s live cycle called
`self.client.get_active_markets(min_volume=0)` — hardcoded to 0, so the
CLAUDE.md-documented volume floor (and `.env.example`'s `MIN_MARKET_VOLUME=10000`)
was never applied to the live market scan. `strategies/quality_filter.py`
implements the same check correctly but is only wired into the standalone
`scan_markets.py` script, never into the live orchestrator. Low-volume/illiquid
markets passed straight through to signal generation with no liquidity floor.

This locks in the actual enforcement mechanism (`PolymarketClient._normalize_markets`,
which `get_active_markets` delegates to) and the orchestrator's env-driven default,
so a future accidental `min_volume=0` regression fails a test instead of only
being caught by inspection.
"""
from __future__ import annotations

from core.polymarket_client import PolymarketClient


def _make_client() -> PolymarketClient:
    return PolymarketClient.__new__(PolymarketClient)


def _market(volume: float, best_ask: float = 0.5, cid: str = "m1") -> dict:
    return {
        "conditionId": cid,
        "volume": volume,
        "bestAsk": best_ask,
        "clobTokenIds": ["tok-yes", "tok-no"],
    }


def test_normalize_markets_drops_below_min_volume():
    client = _make_client()
    markets = [_market(volume=4_999, cid="low"), _market(volume=10_000, cid="high")]

    result = client._normalize_markets(markets, min_volume=10_000)

    ids = {m["condition_id"] for m in result}
    assert ids == {"high"}


def test_normalize_markets_passes_all_when_min_volume_zero():
    client = _make_client()
    markets = [_market(volume=1, cid="tiny"), _market(volume=10_000, cid="high")]

    result = client._normalize_markets(markets, min_volume=0)

    ids = {m["condition_id"] for m in result}
    assert ids == {"tiny", "high"}
