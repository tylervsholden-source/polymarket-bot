"""
Regression test: TradeAnalyzer._evaluate_signal_accuracy() compared
regime_direction against analysis.direction directly (after lowercasing
both), but they are produced in disjoint vocabularies:
  - regime_direction is "UP" / "DOWN" / "NEUTRAL" (agents/subagents/
    research_agent.py::RegimeData.direction, agents/subagents/
    signal_agent_v2.py::EnrichedSignal.regime_direction, strategies/
    bayesian.py's regime_direction parameter, strategies/
    arbitrage_engine.py's _regime_dir)
  - analysis.direction is always "YES" / "NO" (PositionManager only ever
    stores "YES"/"NO" in a position's outcome)

"up"/"down" can never equal "yes"/"no", so the old comparison

    regime_dir.lower() == analysis.direction.lower() and is_win

was always False, and its negation

    regime_dir.lower() != analysis.direction.lower() and not is_win

was always True whenever the trade lost. The whole expression collapsed
to `not is_win`: accuracy["regime"] was True on every loss and False on
every win, completely independent of what the regime actually predicted.
This is the same bug class as the whale_direction case fixed in
tests/test_trade_analyzer_whale_case_mismatch.py, but a vocabulary
mismatch (UP/DOWN vs YES/NO) rather than a casing mismatch.

Fix:
  - add_position() now accepts and stores an optional regime_direction.
  - agents/orchestrator.py's live order path passes signal.regime_direction
    through to add_position().
  - _evaluate_signal_accuracy() maps UP->YES-aligned / DOWN->NO-aligned
    before comparing, matching every real producer's vocabulary.
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


@pytest.fixture
def pm(tmp_path, monkeypatch):
    import core.position_manager as pm_mod
    monkeypatch.setattr(pm_mod, "DATA_FILE", tmp_path / "positions.json")
    monkeypatch.setenv("INITIAL_CAPITAL", "100.0")
    from core.position_manager import PositionManager
    return PositionManager()


def test_add_position_stores_regime_direction(pm):
    order = {"order_id": "o1", "outcome": "YES", "amount": 2.0, "price": 0.40, "status": "MATCHED"}
    pm.add_position("mkt1", order, "BTC up?", regime_direction="UP")
    assert pm.data["positions"]["mkt1"]["regime_direction"] == "UP"


def test_add_position_without_regime_direction_omits_key(pm):
    order = {"order_id": "o2", "outcome": "YES", "amount": 1.0, "price": 0.50, "status": "LIVE"}
    pm.add_position("mkt2", order, "ETH up?")
    assert "regime_direction" not in pm.data["positions"]["mkt2"]


def test_regime_up_yes_win_is_correct(pm, analyzer):
    """
    Textbook case: regime was UP, the bot bought YES on the same market,
    and the trade WON -- the regime call was objectively correct and must
    be recorded as such.
    """
    order = {"order_id": "o3", "outcome": "YES", "amount": 2.0, "price": 0.40, "status": "MATCHED"}
    pm.add_position("mkt3", order, "SOL up?", regime_direction="UP")
    pm._close_position("mkt3", 1.0)  # YES resolves to 1 -> WIN

    trade = pm.data["closed"][-1]
    assert trade["result"] == "WIN"
    assert trade["regime_direction"] == "UP"

    analysis = analyzer.analyze_trade(trade=trade, signal_data=trade, all_closed=[trade])

    assert analysis.signal_accuracy.get("regime") is True, (
        "regime=UP, direction=YES, WIN is the textbook aligned-and-correct "
        "case -- accuracy['regime'] must be True, not False from a "
        "UP/DOWN vs YES/NO vocabulary mismatch"
    )


def test_regime_down_no_win_is_correct(pm, analyzer):
    """Mirrored on the NO/DOWN side."""
    order = {"order_id": "o4", "outcome": "NO", "amount": 2.0, "price": 0.40, "status": "MATCHED"}
    pm.add_position("mkt4", order, "ETH up?", regime_direction="DOWN")
    pm._close_position("mkt4", 1.0)  # NO token resolves to 1 -> WIN

    trade = pm.data["closed"][-1]
    assert trade["result"] == "WIN"

    analysis = analyzer.analyze_trade(trade=trade, signal_data=trade, all_closed=[trade])

    assert analysis.signal_accuracy.get("regime") is True


def test_regime_up_yes_loss_is_incorrect(pm, analyzer):
    """
    Regime UP, bot bought YES, but the trade LOST -- the regime call was
    aligned with the trade yet the trade still lost, so this is not a case
    where the old bug's "always True on loss" behavior happens to look
    right; the regime prediction of upward movement failed to pay off and
    must be recorded as incorrect.
    """
    order = {"order_id": "o5", "outcome": "YES", "amount": 2.0, "price": 0.40, "status": "MATCHED"}
    pm.add_position("mkt5", order, "BNB up?", regime_direction="UP")
    pm._close_position("mkt5", 0.0)  # YES resolves to 0 -> LOSS

    trade = pm.data["closed"][-1]
    assert trade["result"] == "LOSS"

    analysis = analyzer.analyze_trade(trade=trade, signal_data=trade, all_closed=[trade])

    assert analysis.signal_accuracy.get("regime") is False


def test_evaluate_signal_accuracy_regime_vocab_in_isolation(analyzer):
    """Isolates the comparison bug from the add_position plumbing gap."""
    from agents.trade_analyzer import TradeAnalysis

    analysis = TradeAnalysis(direction="YES", outcome="WIN")
    accuracy = analyzer._evaluate_signal_accuracy(
        analysis, {"regime_direction": "UP"}
    )
    assert accuracy.get("regime") is True

    # NO trade that WON means price actually went down -- opposite of the
    # UP regime call, so the regime prediction was wrong.
    analysis_loss = TradeAnalysis(direction="NO", outcome="WIN")
    accuracy_loss = analyzer._evaluate_signal_accuracy(
        analysis_loss, {"regime_direction": "UP"}
    )
    assert accuracy_loss.get("regime") is False
