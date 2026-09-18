"""
Regression test: TradeAnalyzer must not classify EXPIRED (sim market never
resolved within the 45-minute window) closed trades as LOSS.

Bug: agents/orchestrator.py::_check_sim_resolutions() sets
`trade["result"] = "EXPIRED"` when a sim trade's market never resolves
(CLOB winner field unset, orderbook mid-price stays in the 0.15-0.85
"UNCLEAR" band) 45+ minutes after entry — and never sets a "pnl" key on
that trade dict at all (neither at trade creation nor in the EXPIRED
branch). agents/trade_analyzer.py::analyze_trade() only special-cased
"WIN"/"LOSS"/"NEUTRAL" in trade["result"]; "EXPIRED" fell through to:

    analysis.outcome = "WIN" if analysis.pnl > 0 else "LOSS"

Since `trade.get("pnl", 0)` defaults to 0 for these trades, `0 > 0` is
False and every EXPIRED close was permanently mislabeled as a LOSS — the
same bug class already fixed for NEUTRAL closes (see
test_trade_analyzer_neutral_not_loss.py). This was previously unreachable
in the bot's default sim/paper mode because _analyze_new_closed_trades()
read position_manager.data["closed"] (always empty in sim mode) instead of
self._sim_results; the 85th daily review (commit dc69345) fixed that
routing bug and made self._sim_results — which does contain EXPIRED
trades — flow into analyze_trade() for the first time, making this
EXPIRED-as-LOSS mislabeling newly reachable in the bot's actual default
operating mode.

Fix: analyze_trade() now maps result == "EXPIRED" to outcome == "NEUTRAL"
(an EXPIRED close carries no information about whether the directional
call was right, exactly like an unfilled/refunded NEUTRAL close), which
already flows correctly through every existing NEUTRAL-aware branch
(_determine_root_cause, _evaluate_signal_accuracy's is_resolved gate,
_update_pattern_stats).
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


def _expired_trade(**overrides):
    """Shaped like a real sim trade dict from orchestrator._sim_trades /
    _sim_results — note there is deliberately no "pnl" key, matching the
    real production shape (see _check_sim_resolutions and the trade-entry
    construction in orchestrator.py, neither of which ever sets one)."""
    trade = {
        "market_id": "m1",
        "question": "Will BTC be up?",
        "direction": "NO",
        "outcome": "NO",
        "price": 0.40,
        "result": "EXPIRED",
        "final_yes_price": 0.55,
        "opened_at": 0,
        "closed_at": 2700,
    }
    trade.update(overrides)
    return trade


def test_expired_result_not_classified_as_loss(analyzer):
    """An EXPIRED (unresolved market) close must be NEUTRAL, not LOSS."""
    trade = _expired_trade()
    analysis = analyzer.analyze_trade(trade)

    assert analysis.outcome == "NEUTRAL"
    assert analysis.outcome != "LOSS"


def test_expired_result_gets_unfilled_root_cause_not_loss_reasoning(analyzer):
    """Root cause for an EXPIRED close must not run LOSS-branch reasoning."""
    trade = _expired_trade()
    analysis = analyzer.analyze_trade(trade, signal_data={"edge": 0.05, "risk_flags": []})

    assert "NEUTRAL" in analysis.root_cause or "UNFILLED" in analysis.root_cause
    assert "NO_TRAP" not in analysis.root_cause


def test_expired_trade_does_not_inflate_pattern_loss_stats(analyzer):
    """A low-edge NO EXPIRED trade would pre-fix match the NO_TRAP /
    LOW_EDGE_LOSS pattern (both require outcome == "LOSS") and increment
    stats.losses for a market that never even resolved."""
    trade = _expired_trade()
    signal_data = {"edge": 0.05, "risk_flags": []}
    analyzer.analyze_trade(trade, signal_data=signal_data)

    report = analyzer.get_pattern_report()
    assert "NO_TRAP" not in report
    assert "LOW_EDGE_LOSS" not in report
