"""
Round 105 daily review: regression test for the OVERPRICED MARKET BLOCK in
ArbitrageEngine._evaluate_market comparing against 1.50 instead of the
documented/intended 1.10.

Three independent sources agree the live threshold should be 1.10:
- The block's own inline comment ("YES+NO > 1.10 = market maker spread too
  wide, edge is fake").
- docs/PRICING_SANITY_SPEC.md's LIVE-profile max_ask_sum = 1.10.
- tests/test_no_side_execution_path.py's own comment ("YES+NO > 1.10 ->
  OVERPRICED_BLOCK returns None").

The code instead used 1.50, a 40-cent-wide gap in which a severely
mispriced/illiquid order book (fake edge) could still reach a real order.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

from strategies.arbitrage_engine import ArbitrageEngine


def _future_end_date(minutes: int = 60) -> str:
    dt = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_market(best_ask: str, no_best_ask: str, condition_id: str = "cond_ovp") -> dict:
    return {
        "condition_id": condition_id,
        "question": "Bitcoin Up or Down - March 16, 7:00PM-8:00PM ET",
        "best_ask": best_ask,
        "best_bid": str(round(float(best_ask) - 0.02, 4)),
        "no_best_ask": no_best_ask,
        "no_best_bid": str(round(float(no_best_ask) - 0.02, 4)),
        "yes_token_id": "yes_tok_1",
        "no_token_id": "no_tok_1",
        "endDate": _future_end_date(60),
    }


def _make_engine() -> ArbitrageEngine:
    return ArbitrageEngine(http_session=None, binance_feed=None, smart_trader_tracker=None)


class TestOverpricedBlockThreshold:
    def test_sum_above_110_is_blocked(self):
        # YES=0.60 + NO=0.55 = 1.15 > 1.10 -> must be blocked (previously
        # only blocked above 1.50, letting this through).
        market = _make_market(best_ask="0.60", no_best_ask="0.55")
        engine = _make_engine()
        result = asyncio.run(
            engine._evaluate_market(market, capital=500.0, z_score=0.0, signal_type="bayesian")
        )
        assert result is None
        # Blocked before diagnostics are recorded for this market.
        assert "cond_ovp" not in engine.get_last_diagnostics()

    def test_sum_at_exactly_110_is_not_blocked_by_this_gate(self):
        # YES=0.55 + NO=0.55 = 1.10 exactly -> boundary is exclusive (> 1.10),
        # so this gate does not fire (other gates may still reject the trade).
        market = _make_market(best_ask="0.55", no_best_ask="0.55")
        engine = _make_engine()
        asyncio.run(
            engine._evaluate_market(market, capital=500.0, z_score=0.0, signal_type="bayesian")
        )
        # Reaching diagnostics means OVERPRICED_BLOCK did not short-circuit.
        assert "cond_ovp" in engine.get_last_diagnostics()

    def test_sum_between_110_and_150_now_blocked(self):
        # YES=0.55 + NO=0.80 = 1.35: previously slipped through (< 1.50),
        # now correctly blocked as fake/illiquid edge.
        market = _make_market(best_ask="0.55", no_best_ask="0.80")
        engine = _make_engine()
        result = asyncio.run(
            engine._evaluate_market(market, capital=500.0, z_score=0.0, signal_type="bayesian")
        )
        assert result is None
        assert "cond_ovp" not in engine.get_last_diagnostics()
