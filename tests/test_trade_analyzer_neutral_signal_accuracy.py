"""
Regression test: TradeAnalyzer must not label a NEUTRAL (unfilled/refunded)
close as a correctly- or incorrectly-predicted signal.

Bug: agents/trade_analyzer.py::_evaluate_signal_accuracy() computed
`is_win = analysis.outcome == "WIN"` and then used `not is_win` as the
"signal predicted the opposing, and that was right" branch:

    whale_correct = (
        (whale_dir == "BULLISH" and analysis.direction == "YES" and is_win) or
        ...
        (whale_dir == "BULLISH" and analysis.direction == "NO" and not is_win) or
        (whale_dir == "BEARISH" and analysis.direction == "YES" and not is_win)
    )

`not is_win` is True for both LOSS and NEUTRAL. A NEUTRAL close (GTC order
never filled, pnl=0.0) carries zero information about whether the
whale/regime call was directionally right — the trade never happened — but
this logic silently scored it as a correct prediction whenever the position
direction opposed the signal. Same flaw for `smart_money`/`edge_prediction`,
which used raw `is_win` (False for both LOSS and NEUTRAL).

This is the same "NEUTRAL counted as a directional result" bug class already
fixed elsewhere in this file (outcome classification, root-cause, pattern
stats — see test_trade_analyzer_neutral_not_loss.py) but missed in this
sibling function.

Fix: _evaluate_signal_accuracy() now only sets whale/regime/smart_money/
edge_prediction accuracy when analysis.outcome is WIN or LOSS, skipping
NEUTRAL closes entirely.
"""
import importlib

import pytest


@pytest.fixture
def analyzer(tmp_path, monkeypatch):
    import agents.trade_analyzer as ta_module
    importlib.reload(ta_module)

    monkeypatch.setattr(ta_module.TradeAnalyzer, "ANALYSIS_FILE", tmp_path / "trade_analyses.json")
    monkeypatch.setattr(ta_module.TradeAnalyzer, "PATTERN_FILE", tmp_path / "trade_patterns.json")

    return ta_module.TradeAnalyzer()


def _neutral_no_trade(**overrides):
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


def test_neutral_close_does_not_score_whale_signal(analyzer):
    """A NEUTRAL close with an opposing whale signal must not be scored at all,
    not silently marked 'correct' (pre-fix: whale_dir=BULLISH + direction=NO +
    not is_win(NEUTRAL) => whale_correct=True)."""
    trade = _neutral_no_trade(signal_data={"whale_direction": "BULLISH"})
    analysis = analyzer.analyze_trade(trade, signal_data={"whale_direction": "BULLISH"})

    assert analysis.outcome == "NEUTRAL"
    assert "whale" not in analysis.signal_accuracy


def test_neutral_close_does_not_score_regime_signal(analyzer):
    trade = _neutral_no_trade(signal_data={"regime_direction": "UP"})
    analysis = analyzer.analyze_trade(trade, signal_data={"regime_direction": "UP"})

    assert analysis.outcome == "NEUTRAL"
    assert "regime" not in analysis.signal_accuracy


def test_neutral_close_does_not_score_smart_money_or_edge(analyzer):
    trade = _neutral_no_trade(signal_data={"smart_money_signal": "BUY"})
    analysis = analyzer.analyze_trade(trade, signal_data={"smart_money_signal": "BUY"})

    assert analysis.outcome == "NEUTRAL"
    assert "smart_money" not in analysis.signal_accuracy
    assert "edge_prediction" not in analysis.signal_accuracy


def test_real_loss_with_opposing_whale_signal_still_scored_correct(analyzer):
    """Backward compatibility: a real LOSS (not NEUTRAL) with an opposing
    whale signal is still correctly scored as whale_correct=True."""
    trade = {
        "market_id": "m2", "question": "q", "outcome": "NO",
        "pnl": -5.0, "price": 0.5, "exit_price": 0.0,
        "result": "LOSS", "opened_at": 0, "closed_at": 60,
    }
    signal_data = {"whale_direction": "BULLISH"}
    analysis = analyzer.analyze_trade(trade, signal_data=signal_data)

    assert analysis.outcome == "LOSS"
    assert analysis.signal_accuracy.get("whale") is True
