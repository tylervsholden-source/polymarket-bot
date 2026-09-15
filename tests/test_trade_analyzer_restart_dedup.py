"""
Regression test: TradeAnalyzer must not re-count the same closed trade into
its persisted pattern stats after a process restart.

Bug: Orchestrator._analyze_new_closed_trades() tracks which closed trades it
has already analyzed with an in-memory-only counter
(`self._last_analyzed_count`, orchestrator.py, initialized to 0 on every
Orchestrator.__init__). That counter is never persisted, while both its
input (position_manager's `data/positions.json["closed"]`) and its output
(TradeAnalyzer's `_patterns`, persisted to data/trade_patterns.json and
restored via `_load_history()`) survive process restarts on disk.
`_update_pattern_stats()` has no dedup of its own — it unconditionally does
`stats.occurrences += 1` / `wins|losses += 1` / `total_pnl += pnl` for every
trade it is given.

So on every restart (a deploy, a crash, a manual restart), the very next
`_analyze_new_closed_trades()` call replays *every* historical closed trade
again (since `new_count = len(closed_trades) - 0 == len(closed_trades)`),
permanently re-incrementing the already-persisted pattern stats on top of
what earlier runs had already accumulated. This corrupts the win-rate /
occurrence counts `get_recommendations()` uses to decide adaptive-sizing
recommendations (e.g. "REGIME_OVEREXTEND ... WR — agresif küçült"), making
them reflect N restarts of the same handful of trades rather than N distinct
trades.

Fix: TradeAnalyzer now dedups by the trade's stable `order_id` (the same
field core/position_manager.py already uses to dedup closed trades) instead
of relying on a caller-supplied index. Analyzed order_ids are persisted
alongside the analyses list in data/trade_analyses.json and restored in
_load_history(), so a fresh TradeAnalyzer instance (as created on every
process restart) does not double-count a trade it already saw in a prior
process's lifetime.
"""
import importlib

import pytest


@pytest.fixture
def make_analyzer(tmp_path, monkeypatch):
    """Factory for TradeAnalyzer instances backed by the same throwaway files,
    simulating a fresh process restart re-reading persisted state."""
    import agents.trade_analyzer as ta_module

    analysis_file = tmp_path / "trade_analyses.json"
    pattern_file = tmp_path / "trade_patterns.json"

    def _make():
        importlib.reload(ta_module)
        monkeypatch.setattr(ta_module.TradeAnalyzer, "ANALYSIS_FILE", analysis_file)
        monkeypatch.setattr(ta_module.TradeAnalyzer, "PATTERN_FILE", pattern_file)
        return ta_module.TradeAnalyzer()

    return _make


def _loss_trade(order_id, **overrides):
    trade = {
        "order_id": order_id,
        "market_id": "m1",
        "question": "Will BTC be up?",
        "outcome": "NO",
        "pnl": -10.0,
        "price": 0.40,
        "exit_price": 0.0,
        "result": "LOSS",
        "opened_at": 0,
        "closed_at": 60,
    }
    trade.update(overrides)
    return trade


def test_same_order_id_analyzed_twice_in_one_process_is_not_double_counted(make_analyzer):
    analyzer = make_analyzer()
    signal_data = {"edge": 0.10, "risk_flags": [], "confluence_score": 0.0}
    trade = _loss_trade("order-1")

    analyzer.analyze_trade(trade, signal_data=signal_data)
    analyzer.analyze_trade(trade, signal_data=signal_data)  # replay, e.g. duplicate call

    report = analyzer.get_pattern_report()
    assert report.get("NO_TRAP", {}).get("occurrences") == 1


def test_restart_does_not_replay_already_analyzed_trade_into_pattern_stats(make_analyzer):
    """Simulates the real bug scenario: a trade is analyzed, the process
    restarts (new TradeAnalyzer instance, same disk files), and the
    orchestrator's closed-trade list is replayed from index 0 again."""
    signal_data = {"edge": 0.10, "risk_flags": [], "confluence_score": 0.0}
    trade = _loss_trade("order-1")

    analyzer_before_restart = make_analyzer()
    analyzer_before_restart.analyze_trade(trade, signal_data=signal_data)

    # Process restart: brand-new TradeAnalyzer, reloads persisted state from disk.
    analyzer_after_restart = make_analyzer()
    analyzer_after_restart.analyze_trade(trade, signal_data=signal_data)

    report = analyzer_after_restart.get_pattern_report()
    assert report.get("NO_TRAP", {}).get("occurrences") == 1, (
        "restart replayed the same order_id and double-counted it into "
        "persisted pattern stats"
    )


def test_distinct_order_ids_still_both_counted(make_analyzer):
    """Make sure the dedup fix doesn't over-suppress genuinely new trades."""
    analyzer = make_analyzer()
    signal_data = {"edge": 0.10, "risk_flags": [], "confluence_score": 0.0}

    analyzer.analyze_trade(_loss_trade("order-1"), signal_data=signal_data)
    analyzer.analyze_trade(_loss_trade("order-2"), signal_data=signal_data)

    report = analyzer.get_pattern_report()
    assert report.get("NO_TRAP", {}).get("occurrences") == 2
