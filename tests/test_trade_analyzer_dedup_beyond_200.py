"""
Regression test: TradeAnalyzer must not re-count an old closed trade into its
persisted pattern stats after a restart, once the bot has analyzed more than
200 trades in its lifetime.

Background: the 43rd daily review fixed TradeAnalyzer's restart-replay
double-counting bug by dedupping on the trade's stable `order_id`, persisting
`_analyzed_order_ids` to disk (data/trade_analyses.json) and restoring it in
`_load_history()` (see test_trade_analyzer_restart_dedup.py). That fix is
correct for small histories, but `_save_history()` only ever writes the most
recent 200 entries to disk:

    recent = self._analyses[-200:] if len(self._analyses) > 200 else self._analyses

and `_load_history()` rebuilds `_analyzed_order_ids` purely by reading the
order_ids present in that same truncated file:

    if self.ANALYSIS_FILE.exists():
        with open(self.ANALYSIS_FILE) as f:
            for entry in json.load(f):
                oid = entry.get("order_id", "")
                if oid:
                    self._analyzed_order_ids.add(oid)

So once a bot has analyzed more than 200 trades and then restarts (a deploy,
a crash, a manual restart), any order_id older than the trailing 200-entry
window is no longer recoverable from disk. Orchestrator._analyze_new_closed_
trades() still replays the *entire* `positions.json["closed"]` list from
index 0 on every restart (its own dedup counter, `_last_analyzed_count`, is
in-memory only). For every one of those old, no-longer-remembered trades,
`analyze_trade()` treats it as brand new and `_update_pattern_stats()`
unconditionally re-increments occurrences/wins/losses/total_pnl for it —
permanently corrupting the persisted win-rate stats that
`get_recommendations()` uses for adaptive-sizing suggestions, exactly the bug
class the 43rd daily review set out to fix, just outside the window that
fix's own persisted-history truncation actually covers.

Fix: persist the full set of analyzed order_ids (not just the ids present in
the truncated 200-entry `analyses` list) in data/trade_analyses.json, and
restore all of them in `_load_history()`.
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


def _win_trade(order_id, **overrides):
    trade = {
        "order_id": order_id,
        "market_id": "m1",
        "question": "Will BTC be up?",
        "outcome": "YES",
        "pnl": 5.0,
        "price": 0.40,
        "exit_price": 1.0,
        "result": "WIN",
        "opened_at": 0,
        "closed_at": 60,
    }
    trade.update(overrides)
    return trade


def test_restart_does_not_replay_trade_older_than_200_analyses(make_analyzer):
    """The very first trade ever analyzed (order_id "order-0") falls out of
    the trailing 200-entry persisted analyses window once 205 trades have
    been analyzed. A restart must still recognize it as already-seen."""
    signal_data = {"edge": 0.20, "confluence_score": 0.8, "risk_flags": []}

    analyzer_before_restart = make_analyzer()

    first_trade = _win_trade("order-0")
    analyzer_before_restart.analyze_trade(first_trade, signal_data=signal_data)

    # Push the first trade out of the trailing 200-entry persisted window.
    for i in range(1, 205):
        analyzer_before_restart.analyze_trade(
            _win_trade(f"order-{i}"), signal_data=signal_data
        )

    before_report = analyzer_before_restart.get_pattern_report()
    before_occurrences = before_report.get("HIGH_EDGE_WIN", {}).get("occurrences")

    # Process restart: brand-new TradeAnalyzer, reloads persisted state from disk.
    analyzer_after_restart = make_analyzer()

    # Orchestrator replays the *entire* closed-trade history from index 0
    # after a restart (its own dedup counter is in-memory only) — including
    # the very first trade, which is no longer in the truncated analyses file.
    analyzer_after_restart.analyze_trade(first_trade, signal_data=signal_data)

    after_report = analyzer_after_restart.get_pattern_report()
    after_occurrences = after_report.get("HIGH_EDGE_WIN", {}).get("occurrences")

    assert after_occurrences == before_occurrences, (
        "restart replayed a trade older than the trailing 200-entry analyses "
        "window and double-counted it into persisted pattern stats "
        f"(before={before_occurrences}, after={after_occurrences})"
    )
