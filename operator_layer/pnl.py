"""
operator_layer/pnl.py

PnL computation and equity state builder.

Inputs: raw dicts from ledgers.py (positions.json contents).
Outputs: EquityState, list[OpenPosition], list[ClosedTrade].

REAL vs SHADOW:
  All values here come from positions.json and status.json — these reflect
  real order executions (or simulation positions). They are NOT shadow-derived.
  Shadow journal values (execution_adjusted_ev etc.) are in aggregator.py,
  not here. PnL here is actual capital movement.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from operator_layer.types import ClosedTrade, EquityState, OpenPosition


def build_open_positions(positions_dict: dict) -> list[OpenPosition]:
    """
    Build OpenPosition objects from positions.json → positions dict.

    Parameters
    ----------
    positions_dict : dict
        The "positions" key from positions.json.
        {market_id: {order_id, question, outcome, amount, entry_price,
                     status, current_price, current_value, unrealized_pnl, ...}}
    """
    result = []
    for market_id, pos in positions_dict.items():
        risk_flags = []
        upnl = pos.get("unrealized_pnl", 0.0)
        amount = pos.get("amount", 0.0)
        if amount > 0 and upnl < -0.3 * amount:
            risk_flags.append("DEEP_LOSS_>30PCT")
        if pos.get("snapshot_age_seconds", 0) > 300:
            risk_flags.append("STALE_MARK")

        result.append(OpenPosition(
            position_id=market_id,
            market_id=market_id,
            question=pos.get("question", ""),
            outcome=pos.get("outcome", ""),
            amount=amount,
            entry_price=pos.get("entry_price", 0.0),
            current_mark=pos.get("current_price"),
            current_value=pos.get("current_value"),
            unrealized_pnl=upnl,
            end_date_iso=pos.get("end_date_iso"),
            status=pos.get("status", ""),
            order_id=pos.get("order_id", ""),
            age_seconds=None,   # enriched by aggregator with wall clock
            risk_flags=risk_flags,
        ))
    return result


def build_closed_trades(closed_list: list) -> list[ClosedTrade]:
    """
    Build ClosedTrade objects from positions.json → closed array.

    Note: closed array lacks opened_at / closed_at timestamps —
    those fields will be None unless enriched via shadow journal join.
    """
    result = []
    for item in closed_list:
        amount    = item.get("amount", 0.0)
        close_prc = item.get("close_price", item.get("current_price", 0.0))
        pnl       = item.get("pnl", 0.0)
        pnl_pct   = (pnl / amount * 100.0) if amount > 0 else None

        result.append(ClosedTrade(
            trade_id=item.get("order_id", ""),
            market_id=None,   # not stored in closed array
            question=item.get("question", ""),
            outcome=item.get("outcome", ""),
            amount=amount,
            entry_price=item.get("entry_price", 0.0),
            close_price=close_prc,
            realized_pnl=pnl,
            pnl_pct=pnl_pct,
            result=item.get("result", "NEUTRAL"),
            status=item.get("status", ""),
            payout=item.get("payout", 0.0),
            opened_at=None,
            closed_at=None,
            original_policy_profile=None,
            original_decision_id=None,
            close_reason=None,
        ))
    return result


def build_equity_state(
    positions_data: dict,
    status_data: dict,
    open_positions: list[OpenPosition],
    closed_trades: list[ClosedTrade],
) -> EquityState:
    """
    Build EquityState from positions.json and status.json.

    Parameters
    ----------
    positions_data : dict
        Output of read_positions_ledger().
    status_data : dict
        Output of read_status_snapshot().
    open_positions : list[OpenPosition]
        Already-built open positions.
    closed_trades : list[ClosedTrade]
        Already-built closed trades.
    """
    capital = positions_data.get("capital", 0.0)

    # committed capital = sum of open position amounts
    committed  = sum(p.amount for p in open_positions)
    unrealized = sum(p.unrealized_pnl or 0.0 for p in open_positions)

    # Daily PnL from positions.json
    daily_pnl = positions_data.get("daily", {}).get("pnl", 0.0)

    # Total realized from closed trades
    realized_total = sum(t.realized_pnl for t in closed_trades)

    # cash_available: capital minus what's committed
    cash_available = capital - committed

    total_equity = cash_available + committed + unrealized

    initial_capital = status_data.get("initial_capital") or status_data.get("capital")

    # Determine if daily stop loss triggered.
    #
    # Must mirror core/position_manager.py's daily_loss_exceeded(), the
    # function that actually gates real order placement: the daily -15%
    # threshold is measured against *today's* start-of-day capital
    # (day_start_capital = capital - daily.pnl), not the bot's life-of-bot
    # initial_capital (a fixed env var set once at bot startup). Those two
    # denominators only coincide on day one — after that, capital drifts
    # away from initial_capital (the bot's whole purpose is 1000->3000 in
    # 20 days), so dividing by initial_capital instead of day_start_capital
    # makes this dashboard's DAILY_STOP_LOSS status disagree with the real
    # enforcement in both directions (falsely "blocked" after capital has
    # grown, falsely "clear" after capital has shrunk).
    blocked_reason: Optional[str] = None
    day_start_capital = capital - daily_pnl
    if day_start_capital <= 0:
        blocked_reason = "DAILY_STOP_LOSS"
    elif daily_pnl < 0:
        daily_loss_pct = abs(daily_pnl) / day_start_capital
        if daily_loss_pct >= 0.15:
            blocked_reason = "DAILY_STOP_LOSS"

    return EquityState(
        timestamp_utc=datetime.now(timezone.utc),
        cash_available=cash_available,
        capital_committed=committed,
        unrealized_pnl=unrealized,
        realized_pnl_day=daily_pnl,
        realized_pnl_total=realized_total,
        total_equity=total_equity,
        active_positions_count=len(open_positions),
        initial_capital=initial_capital,
        blocked_reason=blocked_reason,
    )
