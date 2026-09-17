"""
Regression test: TradeAnalyzer._evaluate_signal_accuracy() compared
whale_direction against hardcoded LOWERCASE literals ("bullish"/"bearish"),
but every real producer of this field emits UPPERCASE values:
  - agents/whale_tracker.py::WhaleTracker._analyze() returns
    "direction": "BULLISH" | "BEARISH" | "NEUTRAL"
  - agents/subagents/research_agent.py::WhaleData.direction (same values)
  - agents/subagents/signal_agent_v2.py::EnrichedSignal.whale_direction
    (ctx.get("whale_direction", "NEUTRAL") -- same values, "NEUTRAL" default)

Because `whale_dir == "bullish"` / `"bearish"` never matched real ("BULLISH"/
"BEARISH") data, accuracy["whale"] was always False whenever whale_direction
was non-empty -- it could never be True. In _match_pattern():

    if whale_acc is True and analysis.outcome == "WIN":
        return "WHALE_ALIGNED_WIN"

WHALE_ALIGNED_WIN could therefore never be produced from real trade data,
even for the textbook case: whale bought BULLISH, the bot bought YES on the
same market, and the trade WON. This permanently disabled that half of the
pattern-stats learning loop referenced in CLAUDE.md's TradeAnalyzer
"Adaptif parametre önerileri" (get_recommendations()'s WHALE_ALIGNED_WIN
check can never trigger with zero occurrences).

Separately, core/position_manager.py::add_position() never accepted or
stored a whale_direction field at all -- only edge/confluence_score/
risk_flags (added by an earlier daily review's signal-metadata fix) -- so
a real closed trade's `signal_data` (the closed position dict itself, per
agents/orchestrator.py::_analyze_new_closed_trades(), which calls
`analyze_trade(trade=trade, signal_data=trade, ...)`) never carried
whale_direction into TradeAnalyzer in the first place, making the
comparison bug unreachable from live data.

Fix:
  - add_position() now accepts and stores an optional whale_direction.
  - agents/orchestrator.py's live order path passes signal.whale_direction
    through to add_position().
  - _evaluate_signal_accuracy() upper-cases whale_direction before
    comparing, matching every real producer's casing.
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


def test_add_position_stores_whale_direction(pm):
    """add_position() with whale_direction must persist it on the position."""
    order = {"order_id": "o1", "outcome": "YES", "amount": 2.0, "price": 0.40, "status": "MATCHED"}
    pm.add_position("mkt1", order, "BTC up?", whale_direction="BULLISH")
    assert pm.data["positions"]["mkt1"]["whale_direction"] == "BULLISH"


def test_add_position_without_whale_direction_omits_key(pm):
    """Call sites with no whale data (CLOB resync, bond scanner) must not fabricate one."""
    order = {"order_id": "o2", "outcome": "YES", "amount": 1.0, "price": 0.50, "status": "LIVE"}
    pm.add_position("mkt2", order, "ETH up?")
    assert "whale_direction" not in pm.data["positions"]["mkt2"]


def test_whale_aligned_win_detected_with_real_uppercase_data(pm, analyzer):
    """
    Textbook case: whale bought BULLISH, the bot bought YES on the same
    market, and the trade WON. Real whale data is uppercase ("BULLISH"),
    exactly as agents/whale_tracker.py / EnrichedSignal.whale_direction
    produce it. This must be recognized as WHALE_ALIGNED_WIN.
    """
    order = {"order_id": "o3", "outcome": "YES", "amount": 2.0, "price": 0.40, "status": "MATCHED"}
    pm.add_position(
        "mkt3", order, "SOL up?",
        edge=0.10, confluence_score=0.5, whale_direction="BULLISH",
    )
    pm._close_position("mkt3", 1.0)  # YES resolves to 1 -> WIN

    trade = pm.data["closed"][-1]
    assert trade["result"] == "WIN"
    assert trade["whale_direction"] == "BULLISH"

    analysis = analyzer.analyze_trade(trade=trade, signal_data=trade, all_closed=[trade])

    assert analysis.signal_accuracy.get("whale") is True, (
        "whale=BULLISH, direction=YES, WIN is the textbook aligned-and-correct "
        "case -- accuracy['whale'] must be True, not silently absent/False due "
        "to a case-sensitive string comparison"
    )
    assert analysis.pattern_match == "WHALE_ALIGNED_WIN"


def test_whale_bearish_short_win_detected_with_real_uppercase_data(pm, analyzer):
    """Same textbook case, mirrored on the NO/BEARISH side."""
    order = {"order_id": "o4", "outcome": "NO", "amount": 2.0, "price": 0.40, "status": "MATCHED"}
    pm.add_position(
        "mkt4", order, "ETH up?",
        edge=0.10, confluence_score=0.5, whale_direction="BEARISH",
    )
    pm._close_position("mkt4", 1.0)  # NO token resolves to 1 -> WIN

    trade = pm.data["closed"][-1]
    assert trade["result"] == "WIN"

    analysis = analyzer.analyze_trade(trade=trade, signal_data=trade, all_closed=[trade])

    assert analysis.signal_accuracy.get("whale") is True
    assert analysis.pattern_match == "WHALE_ALIGNED_WIN"


def test_evaluate_signal_accuracy_case_insensitive_in_isolation(analyzer):
    """
    Isolates the comparison bug from the add_position plumbing gap: even
    with a hand-built signal_data dict that already carries a real,
    uppercase whale_direction (as every producer emits it), the
    comparison itself must not require lowercase input.
    """
    from agents.trade_analyzer import TradeAnalysis

    analysis = TradeAnalysis(direction="YES", outcome="WIN")
    accuracy = analyzer._evaluate_signal_accuracy(
        analysis, {"whale_direction": "BULLISH"}
    )
    assert accuracy.get("whale") is True
