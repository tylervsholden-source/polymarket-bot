"""
tests/test_pnl_views.py

Tests for operator_layer/pnl.py.

Covers:
- build_open_positions: field mapping, risk flags
- build_closed_trades: pnl_pct derivation, neutral/win/loss
- build_equity_state: cash_available, total_equity, daily_stop_loss block
- Shadow-vs-real distinction: PnL here is real/simulated, not shadow EV
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone

from operator_layer.pnl import build_closed_trades, build_equity_state, build_open_positions
from operator_layer.types import ClosedTrade, EquityState, OpenPosition


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _pos(market_id="mkt-1", outcome="YES", amount=10.0, entry=0.60,
         current=0.70, current_val=11.67, upnl=1.67, status="MATCHED"):
    return {
        market_id: {
            "order_id": "order-1",
            "question": "BTC Up?",
            "outcome": outcome,
            "amount": amount,
            "entry_price": entry,
            "status": status,
            "current_price": current,
            "current_value": current_val,
            "unrealized_pnl": upnl,
            "end_date_iso": "",
        }
    }


def _closed(order_id="order-2", outcome="YES", amount=5.0, entry=0.40,
            close=0.60, pnl=2.0, payout=5.0, result="WIN"):
    return {
        "order_id": order_id,
        "question": "ETH Up?",
        "outcome": outcome,
        "amount": amount,
        "entry_price": entry,
        "status": "MATCHED",
        "close_price": close,
        "pnl": pnl,
        "payout": payout,
        "result": result,
    }


# ── build_open_positions ───────────────────────────────────────────────────────

class TestBuildOpenPositions:
    def test_basic_fields(self):
        positions = build_open_positions(_pos())
        assert len(positions) == 1
        p = positions[0]
        assert isinstance(p, OpenPosition)
        assert p.market_id == "mkt-1"
        assert p.outcome == "YES"
        assert abs(p.amount - 10.0) < 1e-9
        assert abs(p.entry_price - 0.60) < 1e-9
        assert abs(p.current_mark - 0.70) < 1e-9
        assert abs(p.unrealized_pnl - 1.67) < 1e-9

    def test_empty_positions(self):
        result = build_open_positions({})
        assert result == []

    def test_multiple_positions(self):
        positions_dict = {
            **_pos("mkt-1"),
            **_pos("mkt-2", outcome="NO", amount=5.0, entry=0.45,
                   current=0.55, current_val=6.11, upnl=1.11),
        }
        result = build_open_positions(positions_dict)
        assert len(result) == 2

    def test_deep_loss_risk_flag(self):
        # unrealized_pnl < -30% of amount
        positions = build_open_positions({
            "mkt-1": {
                "order_id": "o1", "question": "X", "outcome": "YES",
                "amount": 10.0, "entry_price": 0.60, "status": "MATCHED",
                "current_price": 0.40, "current_value": 6.67, "unrealized_pnl": -3.5,
            }
        })
        assert "DEEP_LOSS_>30PCT" in positions[0].risk_flags

    def test_no_risk_flags_when_normal(self):
        positions = build_open_positions(_pos(upnl=1.0))
        assert positions[0].risk_flags == []


# ── build_closed_trades ────────────────────────────────────────────────────────

class TestBuildClosedTrades:
    def test_win_trade(self):
        trades = build_closed_trades([_closed(pnl=2.0, result="WIN")])
        assert len(trades) == 1
        t = trades[0]
        assert isinstance(t, ClosedTrade)
        assert abs(t.realized_pnl - 2.0) < 1e-9
        assert t.result == "WIN"
        # pnl_pct = 2.0 / 5.0 * 100 = 40.0
        assert abs(t.pnl_pct - 40.0) < 1e-9

    def test_loss_trade(self):
        trades = build_closed_trades([_closed(pnl=-1.0, result="LOSS")])
        assert trades[0].result == "LOSS"
        assert trades[0].realized_pnl < 0

    def test_neutral_trade(self):
        trades = build_closed_trades([_closed(pnl=0.0, result="NEUTRAL")])
        assert trades[0].result == "NEUTRAL"
        assert trades[0].pnl_pct == 0.0

    def test_zero_amount_pnl_pct_none(self):
        item = _closed(amount=0.0, pnl=0.0)
        trades = build_closed_trades([item])
        assert trades[0].pnl_pct is None

    def test_empty_closed_list(self):
        assert build_closed_trades([]) == []

    def test_opened_at_is_none(self):
        """positions.json closed array has no timestamp — opened_at must be None."""
        trades = build_closed_trades([_closed()])
        assert trades[0].opened_at is None
        assert trades[0].closed_at is None
        assert trades[0].original_policy_profile is None


# ── build_equity_state ────────────────────────────────────────────────────────

class TestBuildEquityState:
    def _positions_data(self, capital=100.0, positions=None, closed=None, pnl=0.0):
        return {
            "capital": capital,
            "positions": positions or {},
            "closed": closed or [],
            "daily": {"date": "2026-03-15", "pnl": pnl},
        }

    def test_no_positions_equity(self):
        eq = build_equity_state(
            self._positions_data(capital=100.0),
            {},
            [],
            [],
        )
        assert isinstance(eq, EquityState)
        assert abs(eq.cash_available - 100.0) < 1e-9
        assert abs(eq.capital_committed - 0.0) < 1e-9
        assert abs(eq.total_equity - 100.0) < 1e-9
        assert eq.active_positions_count == 0

    def test_with_open_position(self):
        open_pos = build_open_positions(_pos(amount=20.0, upnl=2.0))
        closed   = build_closed_trades([])
        eq = build_equity_state(
            self._positions_data(capital=100.0),
            {},
            open_pos,
            closed,
        )
        assert abs(eq.capital_committed - 20.0) < 1e-9
        assert abs(eq.cash_available - 80.0) < 1e-9
        assert abs(eq.unrealized_pnl - 2.0) < 1e-9
        assert abs(eq.total_equity - 102.0) < 1e-9
        assert eq.active_positions_count == 1

    def test_realized_pnl_total_from_closed(self):
        closed_trades = build_closed_trades([
            _closed(pnl=3.0, result="WIN"),
            _closed(pnl=-1.0, result="LOSS"),
        ])
        eq = build_equity_state(
            self._positions_data(capital=96.0),
            {},
            [],
            closed_trades,
        )
        assert abs(eq.realized_pnl_total - 2.0) < 1e-9

    def test_daily_pnl_from_positions_json(self):
        eq = build_equity_state(
            self._positions_data(capital=100.0, pnl=-5.0),
            {},
            [],
            [],
        )
        assert abs(eq.realized_pnl_day - (-5.0)) < 1e-9

    def test_daily_stop_loss_block(self):
        """If daily_pnl < -15% of capital, blocked_reason should be set."""
        eq = build_equity_state(
            self._positions_data(capital=100.0, pnl=-20.0),
            {"initial_capital": 100.0},
            [],
            [],
        )
        assert eq.blocked_reason == "DAILY_STOP_LOSS"

    def test_no_block_when_small_loss(self):
        eq = build_equity_state(
            self._positions_data(capital=100.0, pnl=-5.0),
            {"initial_capital": 100.0},
            [],
            [],
        )
        assert eq.blocked_reason is None
