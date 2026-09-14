"""
Regression test: a failed/unavailable CLOB balance fetch must not be
mistaken for a genuine $0.00 balance.

Bug (core/polymarket_client.py::PolymarketClient.get_real_balance()):
    def get_real_balance(self) -> float:
        if not self._clob:
            return 0.0          # <-- "unavailable" sentinel == a real value
        try:
            ...
            return balance
        except Exception as e:
            logger.warning(...)
            return 0.0          # <-- same sentinel on a transient API error

Orchestrator._sync_real_balance() (agents/orchestrator.py) is called
unconditionally every cycle (and once at startup) from Orchestrator.run(),
regardless of live/sim mode:

    balance = self.client.get_real_balance()
    if balance < 0:
        return                       # <-- this guard exists to skip a failed
                                      #     fetch, implying get_real_balance()
                                      #     was meant to signal failure with a
                                      #     negative sentinel
    ...
    new_capital = balance + locked
    self.position_manager.data["capital"] = round(new_capital, 4)
    self.position_manager._save()

Because get_real_balance() actually returns 0.0 (not negative) on BOTH
failure paths -- (a) no CLOB client configured, which is the officially
documented no-API-key simulation mode from docs/api_guide.md ("Test in
simulation mode first, works without API key too"), and (b) any transient
exception while calling the real CLOB balance endpoint on a fully-configured
live account -- `_sync_real_balance()`'s `if balance < 0` guard never fires.
0.0 is treated as a real balance, and `capital` is overwritten (and
persisted to disk) to just the sum of currently-locked position amounts,
silently destroying the rest of the tracked capital on every single
transient balance-fetch failure. This directly corrupts the capital figure
used for Kelly position sizing, AutonomousDecisionEngine's SURVIVAL-mode
threshold, and PositionManager.daily_loss_exceeded() (CLAUDE.md's
non-negotiable daily -15% stop-loss).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agents.orchestrator import Orchestrator
from core.polymarket_client import PolymarketClient


# ── Root cause: get_real_balance()'s failure sentinel ───────────────────────

def test_get_real_balance_returns_negative_sentinel_when_no_clob_configured():
    """No API key / wallet configured (documented no-key simulation mode) —
    get_real_balance() must signal 'unavailable', not a real $0.00 balance."""
    client = PolymarketClient.__new__(PolymarketClient)
    client._clob = None

    balance = client.get_real_balance()

    assert balance < 0, (
        f"get_real_balance() returned {balance!r} for an unconfigured client — "
        "this is indistinguishable from a genuine $0.00 balance and will be "
        "silently accepted by Orchestrator._sync_real_balance()'s `if balance "
        "< 0: return` guard, which exists specifically to skip failed fetches."
    )


def test_get_real_balance_returns_negative_sentinel_on_api_exception():
    """A transient error from the real CLOB balance endpoint (network blip,
    rate limit, etc.) must also be distinguishable from a real $0 balance."""
    client = PolymarketClient.__new__(PolymarketClient)
    client._clob = MagicMock()
    client._clob.get_balance_allowance.side_effect = RuntimeError("timeout")

    balance = client.get_real_balance()

    assert balance < 0, (
        f"get_real_balance() returned {balance!r} after the CLOB call raised — "
        "a transient API failure must not be reported as a real $0 balance."
    )


def test_get_real_balance_still_returns_real_zero_on_success():
    """Sanity: an account that legitimately has $0 free USDC must still read
    as exactly 0.0 (not the failure sentinel) when the CLOB call succeeds."""
    client = PolymarketClient.__new__(PolymarketClient)
    client._clob = MagicMock()
    client._clob.get_balance_allowance.return_value = {"balance": 0}
    client._USDC_DECIMALS = 10 ** 6

    assert client.get_real_balance() == 0.0


# ── Live-path integration: Orchestrator._sync_real_balance() ────────────────

@pytest.fixture(autouse=True)
def tmp_positions_file(tmp_path, monkeypatch):
    import core.position_manager as pm_mod
    monkeypatch.setattr(pm_mod, "DATA_FILE", tmp_path / "positions.json")
    monkeypatch.setenv("INITIAL_CAPITAL", "500.0")


def _make_orchestrator(get_real_balance_return) -> Orchestrator:
    from core.position_manager import PositionManager

    orch = Orchestrator.__new__(Orchestrator)
    orch.client = MagicMock()
    orch.client.get_real_balance.return_value = get_real_balance_return
    orch.position_manager = PositionManager()
    orch.position_manager.data["capital"] = 500.0
    orch.position_manager.data["positions"]["mkt1"] = {
        "order_id": "o1", "question": "BTC up?",
        "outcome": "YES", "amount": 5.0, "entry_price": 0.5, "status": "LIVE",
    }
    orch.position_manager._save()
    return orch


@pytest.mark.asyncio
async def test_failed_balance_fetch_does_not_wipe_tracked_capital():
    """This is the exact live scenario: get_real_balance() fails and (pre-fix)
    returns 0.0. capital must stay untouched, not collapse to `locked` ($5)."""
    unconfigured_client = PolymarketClient.__new__(PolymarketClient)
    unconfigured_client._clob = None
    failed_fetch_sentinel = unconfigured_client.get_real_balance()

    orch = _make_orchestrator(get_real_balance_return=failed_fetch_sentinel)
    await orch._sync_real_balance()

    assert orch.position_manager.data["capital"] == pytest.approx(500.0), (
        f"capital was corrupted to {orch.position_manager.data['capital']} after "
        "a failed balance fetch — a transient CLOB error must never overwrite "
        "tracked capital with just the locked-position total."
    )


@pytest.mark.asyncio
async def test_successful_balance_fetch_still_updates_capital():
    """Non-regression: a real, successful balance read must still sync."""
    orch = _make_orchestrator(get_real_balance_return=90.0)
    await orch._sync_real_balance()

    # new_capital = balance(90) + locked(5) = 95
    assert orch.position_manager.data["capital"] == pytest.approx(95.0)
