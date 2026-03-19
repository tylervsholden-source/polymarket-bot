"""
tests/test_position_closed_trade_views.py

Tests for open position and closed trade views from positions.json data.

Covers:
- Position view queries: inspect open positions
- Closed trade queries: inspect a specific trade
- PnL consistency
- Shadow-vs-real distinction is clear (opened_at is None for positions.json trades)
- Risk flag computation
"""
from __future__ import annotations

import pytest

from operator_layer.pnl import build_closed_trades, build_equity_state, build_open_positions
from operator_layer.types import ClosedTrade, OpenPosition


# ── Workflow: inspect open positions ──────────────────────────────────────────

class TestInspectOpenPositions:
    """
    Operator workflow: what positions are currently open?
    """

    def test_list_all_open_positions(self):
        positions_dict = {
            "mkt-btc-15m": {
                "order_id": "0xabc",
                "question": "Bitcoin Up or Down - 12:30PM-12:45PM ET",
                "outcome": "YES",
                "amount": 10.0,
                "entry_price": 0.62,
                "status": "MATCHED",
                "current_price": 0.71,
                "current_value": 11.45,
                "unrealized_pnl": 1.45,
            },
            "mkt-eth-15m": {
                "order_id": "0xdef",
                "question": "Ethereum Up or Down - 12:30PM ET",
                "outcome": "NO",
                "amount": 5.0,
                "entry_price": 0.48,
                "status": "MATCHED",
                "current_price": 0.42,
                "current_value": 4.38,
                "unrealized_pnl": -0.62,
            },
        }
        positions = build_open_positions(positions_dict)

        assert len(positions) == 2
        btc = next(p for p in positions if "Bitcoin" in p.question)
        eth = next(p for p in positions if "Ethereum" in p.question)

        assert btc.outcome == "YES"
        assert abs(btc.unrealized_pnl - 1.45) < 1e-9
        assert abs(btc.entry_price - 0.62) < 1e-9

        assert eth.outcome == "NO"
        assert eth.unrealized_pnl < 0

    def test_can_identify_losing_position(self):
        positions_dict = {
            "mkt-1": {
                "order_id": "o1", "question": "BTC?", "outcome": "YES",
                "amount": 10.0, "entry_price": 0.70, "status": "MATCHED",
                "current_price": 0.40, "current_value": 5.71, "unrealized_pnl": -4.29,
            }
        }
        positions = build_open_positions(positions_dict)
        assert positions[0].unrealized_pnl < 0
        assert "DEEP_LOSS_>30PCT" in positions[0].risk_flags

    def test_can_identify_winning_position(self):
        positions_dict = {
            "mkt-1": {
                "order_id": "o1", "question": "BTC?", "outcome": "YES",
                "amount": 10.0, "entry_price": 0.50, "status": "MATCHED",
                "current_price": 0.75, "current_value": 15.0, "unrealized_pnl": 5.0,
            }
        }
        positions = build_open_positions(positions_dict)
        assert positions[0].unrealized_pnl > 0
        assert positions[0].risk_flags == []

    def test_no_open_positions_returns_empty(self):
        assert build_open_positions({}) == []


# ── Workflow: inspect a closed trade ──────────────────────────────────────────

class TestInspectClosedTrade:
    """
    Operator workflow: inspect a specific closed trade and its realized PnL.
    """

    def _sample_closed_list(self):
        return [
            {
                "order_id": "0xwin1",
                "question": "Solana Up or Down",
                "outcome": "YES",
                "amount": 8.0,
                "entry_price": 0.45,
                "status": "MATCHED",
                "close_price": 1.0,
                "pnl": 9.78,
                "payout": 17.78,
                "result": "WIN",
            },
            {
                "order_id": "0xloss1",
                "question": "XRP Up or Down",
                "outcome": "NO",
                "amount": 5.0,
                "entry_price": 0.52,
                "status": "MATCHED",
                "close_price": 0.0,
                "pnl": -5.0,
                "payout": 0.0,
                "result": "LOSS",
            },
            {
                "order_id": "0xneutral",
                "question": "BTC Up or Down",
                "outcome": "YES",
                "amount": 3.0,
                "entry_price": 0.50,
                "status": "MATCHED",
                "close_price": 0.5,
                "pnl": 0.0,
                "payout": 0.0,
                "result": "NEUTRAL",
            },
        ]

    def test_all_trades_parsed(self):
        trades = build_closed_trades(self._sample_closed_list())
        assert len(trades) == 3

    def test_can_find_win_trade(self):
        trades = build_closed_trades(self._sample_closed_list())
        wins = [t for t in trades if t.result == "WIN"]
        assert len(wins) == 1
        assert abs(wins[0].realized_pnl - 9.78) < 1e-9

    def test_can_find_loss_trade(self):
        trades = build_closed_trades(self._sample_closed_list())
        losses = [t for t in trades if t.result == "LOSS"]
        assert len(losses) == 1
        assert losses[0].realized_pnl < 0

    def test_pnl_pct_computation(self):
        trades = build_closed_trades(self._sample_closed_list())
        win = next(t for t in trades if t.result == "WIN")
        # pnl_pct = 9.78 / 8.0 * 100 = 122.25
        assert win.pnl_pct is not None
        assert abs(win.pnl_pct - (9.78 / 8.0 * 100)) < 0.01

    def test_loss_pnl_pct_negative(self):
        trades = build_closed_trades(self._sample_closed_list())
        loss = next(t for t in trades if t.result == "LOSS")
        assert loss.pnl_pct is not None
        assert loss.pnl_pct < 0

    def test_positions_json_has_no_timestamps(self):
        """
        opened_at / closed_at are not available from positions.json.
        These must be None to be honest about the data contract.
        """
        trades = build_closed_trades(self._sample_closed_list())
        for t in trades:
            assert t.opened_at is None, "opened_at must be None — not in positions.json"
            assert t.closed_at is None, "closed_at must be None — not in positions.json"

    def test_original_policy_profile_none(self):
        """
        positions.json does not store which policy profile approved the trade.
        This must be None — not fabricated.
        """
        trades = build_closed_trades(self._sample_closed_list())
        for t in trades:
            assert t.original_policy_profile is None


# ── Workflow: which markets made / lost money ──────────────────────────────────

class TestPnLWorkflow:
    def test_total_realized_pnl(self):
        closed_list = [
            {"order_id": "o1", "question": "BTC", "outcome": "YES",
             "amount": 10.0, "entry_price": 0.5, "status": "MATCHED",
             "close_price": 1.0, "pnl": 10.0, "payout": 20.0, "result": "WIN"},
            {"order_id": "o2", "question": "ETH", "outcome": "NO",
             "amount": 5.0, "entry_price": 0.6, "status": "MATCHED",
             "close_price": 0.0, "pnl": -5.0, "payout": 0.0, "result": "LOSS"},
        ]
        trades = build_closed_trades(closed_list)
        total_pnl = sum(t.realized_pnl for t in trades)
        assert abs(total_pnl - 5.0) < 1e-9

    def test_yes_vs_no_performance(self):
        closed_list = [
            {"order_id": "o1", "question": "A", "outcome": "YES",
             "amount": 5.0, "entry_price": 0.5, "status": "MATCHED",
             "close_price": 1.0, "pnl": 5.0, "payout": 10.0, "result": "WIN"},
            {"order_id": "o2", "question": "B", "outcome": "YES",
             "amount": 5.0, "entry_price": 0.5, "status": "MATCHED",
             "close_price": 0.0, "pnl": -5.0, "payout": 0.0, "result": "LOSS"},
            {"order_id": "o3", "question": "C", "outcome": "NO",
             "amount": 5.0, "entry_price": 0.4, "status": "MATCHED",
             "close_price": 1.0, "pnl": 7.5, "payout": 12.5, "result": "WIN"},
        ]
        trades = build_closed_trades(closed_list)
        yes_pnl = sum(t.realized_pnl for t in trades if t.outcome == "YES")
        no_pnl  = sum(t.realized_pnl for t in trades if t.outcome == "NO")
        assert abs(yes_pnl - 0.0) < 1e-9   # YES: +5 -5 = 0
        assert abs(no_pnl - 7.5) < 1e-9    # NO: +7.5

    def test_equity_state_total_from_all_sources(self):
        positions_data = {
            "capital": 90.0,
            "positions": {},
            "closed": [],
            "daily": {"date": "2026-03-15", "pnl": 3.0},
        }
        # 1 open position with unrealized +2
        open_pos = build_open_positions({
            "mkt-1": {
                "order_id": "o1", "question": "X", "outcome": "YES",
                "amount": 10.0, "entry_price": 0.5, "status": "MATCHED",
                "current_price": 0.7, "current_value": 14.0, "unrealized_pnl": 2.0,
            }
        })
        closed = build_closed_trades([])
        eq = build_equity_state(positions_data, {}, open_pos, closed)
        # cash_available = 90 - 10 = 80
        assert abs(eq.cash_available - 80.0) < 1e-9
        # total_equity = cash + committed + unrealized = 80 + 10 + 2 = 92
        assert abs(eq.total_equity - 92.0) < 1e-9
