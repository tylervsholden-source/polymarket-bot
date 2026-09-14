"""
Regression test: real (non-simulated) closed trades never carried signal
metadata into TradeAnalyzer, so root-cause analysis and pattern matching
were always computed from fabricated inputs.

Bug: core/position_manager.py::add_position() only ever stored
order_id/question/outcome/amount/entry_price/status/token_id/created_at/
strategy. agents/orchestrator.py's live order path (the "DOĞRUDAN EMİR VER"
branch) called add_position() without ever passing along signal.edge,
signal.confluence_score or signal.risk_flags — those were only attached to
the separate, sim-only `_sim_trades` dict, which TradeAnalyzer never sees.

agents/orchestrator.py::_analyze_new_closed_trades() calls
`trade_analyzer.analyze_trade(trade=trade, signal_data=trade, ...)` — i.e.
the closed position dict itself doubles as signal_data. Since that dict
never had "edge"/"confluence_score"/"risk_flags"/"exit_price" keys, every
real trade's TradeAnalysis had edge_at_entry=0.0, confluence_at_entry=0.0,
risk_flags_at_entry=[], exit_price=0.0 and hold_duration_seconds=0.0
(opened_at/closed_at were also never stored, so the `if opened and closed`
guard silently fell back to 0). This made every real LOSS misdiagnosed as
"LOW_EDGE_LOSS" regardless of its true edge, and permanently disabled the
HIGH_EDGE_WIN / HIGH_CONFLUENCE_WIN / REGIME_OVEREXTEND_LOSS /
WHALE_OPPOSED_LOSS pattern branches, corrupting the get_recommendations()
learning loop described in CLAUDE.md ("TradeAnalyzer ... Adaptif parametre
önerileri").

Fix: add_position() now accepts optional edge/confluence_score/risk_flags
and stores an "opened_at" epoch timestamp; _close_position() and
_close_position_neutral() now stamp "closed_at". orchestrator.py's live
order path passes signal.edge/confluence_score/risk_flags through.
trade_analyzer.py's exit_price fallback now also checks "close_price"
(the actual key position_manager writes on close).
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


def test_add_position_stores_signal_metadata(pm):
    """add_position() with edge/confluence_score/risk_flags must persist them."""
    order = {"order_id": "o1", "outcome": "NO", "amount": 2.0, "price": 0.40, "status": "MATCHED"}
    pm.add_position(
        "mkt1", order, "BTC up?",
        edge=0.22, confluence_score=0.81, risk_flags=["thin_book"],
    )
    pos = pm.data["positions"]["mkt1"]
    assert pos["edge"] == pytest.approx(0.22)
    assert pos["confluence_score"] == pytest.approx(0.81)
    assert pos["risk_flags"] == ["thin_book"]
    assert "opened_at" in pos


def test_add_position_without_signal_metadata_omits_keys(pm):
    """Call sites with no signal (CLOB resync, bond scanner) must not fabricate 0s."""
    order = {"order_id": "o2", "outcome": "YES", "amount": 1.0, "price": 0.50, "status": "LIVE"}
    pm.add_position("mkt2", order, "ETH up?")
    pos = pm.data["positions"]["mkt2"]
    assert "edge" not in pos
    assert "confluence_score" not in pos
    assert "risk_flags" not in pos


def test_real_loss_trade_carries_true_edge_into_analyzer(pm, analyzer):
    """
    A real LOSS trade with a genuinely high edge (0.22) must be diagnosed
    using that edge — not silently treated as edge=0.0/"LOW_EDGE_LOSS".
    """
    order = {"order_id": "o3", "outcome": "NO", "amount": 2.0, "price": 0.40, "status": "MATCHED"}
    pm.add_position(
        "mkt3", order, "SOL up?",
        edge=0.22, confluence_score=0.75, risk_flags=[],
    )
    # NO position: token_close_price is the NO token's own resolution price.
    # It resolves to 0.0 -> a loss for the NO holder who bought in at 0.40.
    pm._close_position("mkt3", 0.0)

    trade = pm.data["closed"][-1]
    assert trade["result"] == "LOSS"

    analysis = analyzer.analyze_trade(trade=trade, signal_data=trade, all_closed=[trade])

    assert analysis.edge_at_entry == pytest.approx(0.22)
    assert analysis.confluence_at_entry == pytest.approx(0.75)
    assert analysis.pattern_match != "LOW_EDGE_LOSS", (
        "0.22 edge must not be misdiagnosed as a low-edge loss"
    )
    assert analysis.hold_duration_seconds >= 0.0
    assert analysis.exit_price == pytest.approx(0.0)
