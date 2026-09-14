"""
Regression test: TradeAnalyzer must not classify NEUTRAL (unfilled/refunded)
closed trades as LOSS.

Bug: agents/trade_analyzer.py::analyze_trade() recomputed outcome purely
from pnl's sign:

    analysis.outcome = "WIN" if analysis.pnl > 0 else "LOSS"

completely ignoring the trade's already-correct `result` field that
core/position_manager.py sets to "WIN" / "LOSS" / "NEUTRAL" (NEUTRAL is set
whenever a GTC order never fills before the market ends and the USDC is
simply refunded — pnl=0.0, not a trading loss). Because `pnl > 0` is False
for pnl==0, every NEUTRAL close was logged as a LOSS, which corrupted:

  - root-cause analysis (`_determine_root_cause` had no NEUTRAL branch,
    so it ran the LOSS-reason logic, e.g. "NO_TRAP" style reasoning on a
    trade that never even filled)
  - pattern matching (`_match_pattern` could match "NO_TRAP" or
    "LOW_EDGE_LOSS" for a trade that has nothing to do with those patterns)
  - per-pattern win-rate stats (`_update_pattern_stats` incremented
    `stats.losses` for a non-event), which feed AutonomousEngine's
    adaptive-sizing/learning loop via `get_recommendations()`.

This is the same bug class already fixed in agents/autonomous_engine.py's
`_update_performance()` (25th daily review's sibling fix, "23rd daily
review" PR #44) — trade_analyzer.py was missed and had an independent,
duplicated copy of the pnl-sign logic.

Fix: analyze_trade() now reads trade["result"] when it is one of
WIN/LOSS/NEUTRAL and only falls back to pnl-sign classification when
`result` is absent (backward compatibility with callers/tests that don't
set it). _determine_root_cause() gained an explicit NEUTRAL branch, and
_update_pattern_stats() only increments `losses` on an explicit "LOSS"
outcome (not "else").
"""
import importlib

import pytest


@pytest.fixture
def analyzer(tmp_path, monkeypatch):
    """Fresh TradeAnalyzer instance backed by throwaway files."""
    import agents.trade_analyzer as ta_module
    importlib.reload(ta_module)

    monkeypatch.setattr(ta_module.TradeAnalyzer, "ANALYSIS_FILE", tmp_path / "trade_analyses.json")
    monkeypatch.setattr(ta_module.TradeAnalyzer, "PATTERN_FILE", tmp_path / "trade_patterns.json")

    return ta_module.TradeAnalyzer()


def _neutral_trade(**overrides):
    trade = {
        "market_id": "m1",
        "question": "Will BTC be up?",
        "outcome": "NO",
        "pnl": 0.0,
        "price": 0.40,
        "exit_price": 0.40,
        "result": "NEUTRAL",
        "opened_at": 0,
        "closed_at": 60,
    }
    trade.update(overrides)
    return trade


def test_neutral_result_not_classified_as_loss(analyzer):
    """A NEUTRAL (unfilled/refunded) close must keep outcome == NEUTRAL, not LOSS."""
    trade = _neutral_trade()
    analysis = analyzer.analyze_trade(trade)

    assert analysis.outcome == "NEUTRAL"
    assert analysis.outcome != "LOSS"


def test_neutral_result_gets_unfilled_root_cause_not_loss_reasoning(analyzer):
    """Root cause for a NEUTRAL close must not run LOSS-branch reasoning."""
    # Low-edge NO trade: pre-fix this would hit the LOSS "NO_TRAP"-style
    # root cause even though the order never filled.
    trade = _neutral_trade(
        signal_data={"edge": 0.05, "risk_flags": []},
    )
    analysis = analyzer.analyze_trade(trade, signal_data={"edge": 0.05, "risk_flags": []})

    assert "NEUTRAL" in analysis.root_cause or "UNFILLED" in analysis.root_cause
    assert "NO_TRAP" not in analysis.root_cause


def test_neutral_trade_does_not_inflate_pattern_loss_stats(analyzer):
    """
    A NEUTRAL, low-edge NO trade would pre-fix match the NO_TRAP /
    LOW_EDGE_LOSS pattern (both require outcome == "LOSS") and increment
    stats.losses. Post-fix, outcome is "NEUTRAL" so neither pattern matches
    and no pattern stats are polluted by a non-event.
    """
    trade = _neutral_trade()
    signal_data = {"edge": 0.05, "risk_flags": []}
    analyzer.analyze_trade(trade, signal_data=signal_data)

    report = analyzer.get_pattern_report()
    assert "NO_TRAP" not in report
    assert "LOW_EDGE_LOSS" not in report


def test_real_win_and_loss_still_classified_correctly(analyzer):
    """Backward compatibility: explicit WIN/LOSS results still pass through,
    and trades with no `result` field at all still fall back to pnl-sign
    classification (older data / direct pnl-based callers)."""
    win_trade = {
        "market_id": "m2", "question": "q", "outcome": "YES",
        "pnl": 10.0, "price": 0.5, "exit_price": 1.0,
        "result": "WIN", "opened_at": 0, "closed_at": 60,
    }
    loss_trade = {
        "market_id": "m3", "question": "q", "outcome": "YES",
        "pnl": -10.0, "price": 0.5, "exit_price": 0.0,
        "result": "LOSS", "opened_at": 0, "closed_at": 60,
    }
    no_result_field_trade = {
        "market_id": "m4", "question": "q", "outcome": "YES",
        "pnl": 5.0, "price": 0.5, "exit_price": 1.0,
        "opened_at": 0, "closed_at": 60,
    }

    assert analyzer.analyze_trade(win_trade).outcome == "WIN"
    assert analyzer.analyze_trade(loss_trade).outcome == "LOSS"
    assert analyzer.analyze_trade(no_result_field_trade).outcome == "WIN"
