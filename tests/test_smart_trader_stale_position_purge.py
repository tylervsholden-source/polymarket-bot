"""
Regression test: SmartTraderTracker never purges a closed position from its
cache, so a top trader's smart-money signal keeps biasing bayesian_prob
forever after they have actually exited the market.

Bug (21st daily review): `agents/smart_trader_tracker.py::_fetch_positions()`
only ever writes/overwrites `self._positions[cid][trader_name] = net` for
positions the Positions API returns *this* refresh. When a trader closes a
position, the API simply stops returning it — the method has no code path
that removes the stale entry from `self._positions`, so the last known net
size/direction lingers in the cache indefinitely.

This directly reaches the live signal pipeline: `ArbitrageEngine._evaluate_
market()` calls `self.smart_trader.get_signal(condition_id)` every cycle for
every open market and applies `boost = sm["signal"] * 0.02` straight onto
`bayesian_prob` (see docs/architecture.md: "SmartTraderTracker -> +/-0.05
boost" and tests/test_smart_money_boost_survives_reset.py). A trader who
closed their position 20 minutes ago still silently pushes the bot's
probability estimate in the stale direction on every single cycle after
that, for as long as the market stays open.

Pre-fix: the second assertion below fails (signal stays 1.0, buyer stays
listed, even though the trader has zero open positions).
Post-fix: closing the position purges the trader's stale entry so
get_signal() reports the true, empty state.
"""
from __future__ import annotations

import pytest

import agents.smart_trader_tracker as stt_mod


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeSession:
    """Returns one canned response per call, in order."""

    def __init__(self, responses):
        self._responses = list(responses)

    async def get(self, url, params=None):
        return self._responses.pop(0)


def _make_tracker():
    tracker = stt_mod.SmartTraderTracker.__new__(stt_mod.SmartTraderTracker)
    tracker._positions = {}
    tracker._traders = list(stt_mod.TOP_TRADERS)
    tracker._last_refresh = 0.0
    tracker._last_leaderboard = 0.0
    return tracker


@pytest.mark.asyncio
async def test_closed_position_is_purged_from_cache():
    tracker = _make_tracker()
    trader = tracker._traders[0]
    cid = "market-stale-test"

    # Refresh #1: trader has an open $100 YES position in this market.
    open_payload = [{"conditionId": cid, "size": 100.0, "outcome": "YES", "endDate": ""}]
    tracker.session = _FakeSession([_FakeResponse(200, open_payload)])
    assert await tracker._fetch_positions(trader) is True

    sig = tracker.get_signal(cid)
    assert sig["signal"] == pytest.approx(1.0)
    assert trader["name"] in sig["buyers"]

    # Refresh #2: trader has fully closed the position — API now returns
    # nothing for this trader (this is how the real Positions API behaves:
    # a closed position simply disappears from the response).
    tracker.session = _FakeSession([_FakeResponse(200, [])])
    assert await tracker._fetch_positions(trader) is True

    sig2 = tracker.get_signal(cid)
    assert sig2["signal"] == 0.0, (
        "closed position must be purged from the cache instead of lingering "
        f"forever and continuing to bias bayesian_prob: {sig2}"
    )
    assert trader["name"] not in sig2["buyers"]
    assert sig2["total_traders"] == 0


@pytest.mark.asyncio
async def test_position_flip_replaces_not_accumulates():
    """A trader flipping YES -> NO must fully replace the old signal, not
    just add a second stale entry under a different market."""
    tracker = _make_tracker()
    trader = tracker._traders[0]
    cid = "market-flip-test"

    yes_payload = [{"conditionId": cid, "size": 50.0, "outcome": "YES", "endDate": ""}]
    tracker.session = _FakeSession([_FakeResponse(200, yes_payload)])
    await tracker._fetch_positions(trader)
    assert tracker.get_signal(cid)["signal"] == pytest.approx(1.0)

    no_payload = [{"conditionId": cid, "size": 50.0, "outcome": "NO", "endDate": ""}]
    tracker.session = _FakeSession([_FakeResponse(200, no_payload)])
    await tracker._fetch_positions(trader)
    sig = tracker.get_signal(cid)
    assert sig["signal"] == pytest.approx(-1.0)
    assert trader["name"] in sig["sellers"]
    assert trader["name"] not in sig["buyers"]
