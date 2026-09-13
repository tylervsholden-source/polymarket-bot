"""
Regression test: NO position live valuation must use the real CLOB orderbook
for the token actually traded, not a stale Gamma-derived fallback.

Root cause (sim-live gap / "NO current_price jumps to 0.99 right before a
LOSS" pattern seen in data/last_5_losses.json):

PositionManager.update_positions() fetches the position's market via
`client.get_market()` (core/polymarket_client.py), which normalizes ONLY
`best_ask`/`best_bid`/`condition_id`/`end_date_iso` — unlike
`_normalize_markets()` (used for the market *scan*), it never sets
`yes_token_id`/`no_token_id`. So the NO-side branch in update_positions()
```
no_tid = (market or {}).get("no_token_id")   # always None here
no_book = client.get_orderbook(no_tid) if no_tid else None   # always None
```
never actually queries the live NO orderbook — it silently ALWAYS falls back
to `current_price = 1.0 - yes_ask`, a value derived from Gamma's (frequently
stale/lagging, as already documented elsewhere in this codebase) best_ask
field. In the last seconds of these 5-minute crypto up/down markets this
stale value can show a NO position marked near-certain-to-win (current_price
~0.99, positive unrealized_pnl) right up until it resolves as a full LOSS.

Fix: use pos["token_id"] (the exact token recorded at order time — always
correct regardless of what get_market() does or doesn't populate) as the
orderbook lookup key, falling back to market.get("no_token_id") only if that
positional record is somehow missing.
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    import core.position_manager as pm_mod
    tmp_file = tmp_path / "positions.json"
    monkeypatch.setattr(pm_mod, "DATA_FILE", tmp_file)
    monkeypatch.setenv("INITIAL_CAPITAL", "100.0")
    return tmp_file


def _client_with(market: dict, orderbook_by_token: dict) -> MagicMock:
    client = MagicMock()
    client.get_market = AsyncMock(return_value=market)
    client.get_orderbook = MagicMock(side_effect=lambda tid: orderbook_by_token.get(tid))
    return client


@pytest.mark.asyncio
async def test_get_market_never_populates_no_token_id():
    """Locks in the root cause: single-market get_market() has no token ids."""
    from core.polymarket_client import PolymarketClient

    client = PolymarketClient.__new__(PolymarketClient)
    client.session = MagicMock()
    client.session.get = AsyncMock(
        return_value=MagicMock(
            raise_for_status=lambda: None,
            json=lambda: {"conditionId": "mkt-1", "bestAsk": "0.02", "bestBid": "0.0"},
        )
    )
    fresh = await client.get_market("mkt-1")
    assert "no_token_id" not in fresh
    assert "yes_token_id" not in fresh


@pytest.mark.asyncio
async def test_no_position_valuation_uses_real_orderbook_not_stale_gamma_fallback():
    from core.position_manager import PositionManager

    pm = PositionManager()
    pm.data["capital"] = 10.0
    pm.data["positions"]["mkt1"] = {
        "order_id": "o1",
        "question": "Solana Up or Down - Jan 1, 1:00PM-1:05PM ET",
        "outcome": "NO",
        "amount": 2.75,
        "entry_price": 0.55,
        "status": "MATCHED",
        "token_id": "NO-TOKEN-REAL",
    }
    pm._save()

    # Mirrors real client.get_market(): no token-id keys at all, and a
    # stale-looking best_ask that (via the old 1-yes_ask fallback) would
    # misleadingly value the NO position as almost-certainly winning.
    market = {
        "resolved": False, "closed": False,
        "best_ask": 0.02, "best_bid": 0.0,
        "end_date_iso": "2099-01-01T00:00:00Z",
    }
    assert "no_token_id" not in market

    # The real, live NO orderbook shows NO is actually about to LOSE.
    client = _client_with(market, {"NO-TOKEN-REAL": {"best_bid": 0.03, "best_ask": 0.05}})

    await pm.update_positions(client)

    pos = pm.data["positions"]["mkt1"]
    client.get_orderbook.assert_called_with("NO-TOKEN-REAL")
    assert pos["current_price"] == pytest.approx(0.03), (
        f"NO valuation must use the live orderbook (0.03), not the stale "
        f"Gamma fallback (1-0.02=0.98). Got {pos['current_price']}"
    )
    assert pos["unrealized_pnl"] < 0
