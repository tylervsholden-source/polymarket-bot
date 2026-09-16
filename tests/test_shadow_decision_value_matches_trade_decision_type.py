"""
Regression test (57th daily review): Orchestrator._record_shadow_decisions()
wrote DecisionSummary.decision="EXECUTE" (a bare literal) for real live
EXECUTE decisions, instead of the documented TradeDecisionType vocabulary
("EXECUTE_YES" / "EXECUTE_NO" / "REJECT").

shadow_runner/types.py's own docstring for DecisionSummary.decision says:

    decision: str  # TradeDecisionType.value

and calibration/types.py's TradeDecisionType enum only has EXECUTE_YES,
EXECUTE_NO and REJECT — "EXECUTE" is not a member. The paper_strict/
paper_loose shadow harness (shadow_runner/runner.py, via
calibration/decision_policy.py) always writes the correct EXECUTE_YES/
EXECUTE_NO values. Only the live orchestrator's own recording call wrote
the non-conformant "EXECUTE" literal.

Several real consumers identify "is this an execute?" by exact match
against ("EXECUTE_YES", "EXECUTE_NO"):
  - monitoring/daily_review.py::_build_candidate_lists() (the "WOULD-TRADE"
    / "OBSERVATION ZONE" section of the daily shadow review used by the
    human operator when authorizing a live pilot)
  - monitoring/metrics.py::_is_execute() (drift monitoring rejection/EV
    metrics)
  - shadow_runner/reporting.py::_is_execute() (profile divergence report)

None of these ever matched the live orchestrator's bare "EXECUTE" literal,
so every real EXECUTE decision recorded by the live trading loop was
silently invisible to them: the daily review's "WOULD-TRADE" list showed
0 candidates even on days with dozens of real executes with positive edge,
misleading exactly the human review step (mandatory_review_hours=24,
per shadow_runner/readiness.py's PilotConstraints) this bot's readiness
process relies on before authorizing any live pilot.

(compute_summary_metrics(), which actually feeds the readiness verdict
written to readiness_verdict.json, filters with `!= "REJECT"` and was
therefore unaffected — this bug was confined to the human-facing would-
trade/divergence reporting, not the GO/NO_GO verdict itself.)

Fix: agents/orchestrator.py::_record_shadow_decisions() now writes
"EXECUTE_YES" or "EXECUTE_NO" (based on sig_match.direction) instead of
the bare "EXECUTE" literal.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from agents.orchestrator import Orchestrator
from monitoring.daily_review import generate_daily_review


def _make_fake_orchestrator():
    orch = Orchestrator.__new__(Orchestrator)
    orch.arb_engine = SimpleNamespace(get_last_diagnostics=lambda: {})
    from control_plane.entry_window_guard import DEFAULT_ENTRY_WINDOW_POLICY
    orch._entry_window_policy = DEFAULT_ENTRY_WINDOW_POLICY
    orch._shadow_run_id = "test-run"

    written = []
    fake_writer = SimpleNamespace(
        write=lambda record: written.append(record),
        flush=lambda: None,
    )
    orch._get_shadow_writer = lambda: fake_writer
    return orch, written


def _yes_signal(condition_id: str):
    market = {
        "condition_id": condition_id,
        "question": "Bitcoin 5 min up or down?",
        "best_ask": 0.60,
        "best_bid": 0.58,
        "volume": 50_000,
    }
    signal = SimpleNamespace(
        market=market, direction="YES", bayesian_prob=0.75,
        market_price=0.60, edge=0.15, entry_price=0.60, size=3.0,
        z_score=0.0, signal_type="ARB", reasoning="test",
        token_id="tok-yes", side_diagnostics=None,
    )
    return market, signal


def _no_signal(condition_id: str):
    market = {
        "condition_id": condition_id,
        "question": "Bitcoin 5 min up or down?",
        "best_ask": 0.28,
        "best_bid": 0.27,
        "volume": 50_000,
        "no_best_ask": 0.55,
        "no_best_bid": 0.53,
    }
    signal = SimpleNamespace(
        market=market, direction="NO", bayesian_prob=0.30,
        market_price=0.28, edge=0.15, entry_price=0.55, size=3.0,
        z_score=0.0, signal_type="ARB", reasoning="test",
        token_id="tok-no", side_diagnostics=None,
    )
    return market, signal


class TestShadowDecisionValueMatchesTradeDecisionType:
    def test_yes_execute_writes_execute_yes(self):
        market, signal = _yes_signal("cond-yes")
        orch, written = _make_fake_orchestrator()
        orch._record_shadow_decisions([market], [signal], intended_size=3.0)
        assert len(written) == 1
        assert written[0].decision_summary.decision == "EXECUTE_YES"

    def test_no_execute_writes_execute_no(self):
        market, signal = _no_signal("cond-no")
        orch, written = _make_fake_orchestrator()
        orch._record_shadow_decisions([market], [signal], intended_size=3.0)
        assert len(written) == 1
        assert written[0].decision_summary.decision == "EXECUTE_NO"

    def test_no_signal_produced_still_writes_reject(self):
        market = {
            "condition_id": "cond-reject",
            "question": "Bitcoin 5 min up or down?",
            "best_ask": 0.50,
            "best_bid": 0.49,
            "volume": 50_000,
        }
        orch, written = _make_fake_orchestrator()
        orch._record_shadow_decisions([market], [], intended_size=3.0)
        assert len(written) == 1
        assert written[0].decision_summary.decision == "REJECT"

    def test_daily_review_would_trade_list_sees_real_live_executes(self):
        """End-to-end: real orchestrator-shaped EXECUTE records must show up
        in monitoring.daily_review's WOULD-TRADE list, not just in the raw
        execution_rate metric."""
        BASE = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
        orch, written = _make_fake_orchestrator()
        for i in range(5):
            market, signal = _yes_signal(f"cond-{i}")
            orch._record_shadow_decisions([market], [signal], intended_size=3.0)
        for i in range(20):
            market = {
                "condition_id": f"cond-reject-{i}",
                "question": "Bitcoin 5 min up or down?",
                "best_ask": 0.50, "best_bid": 0.49, "volume": 50_000,
            }
            orch._record_shadow_decisions([market], [], intended_size=3.0)

        assert len(written) == 25
        report = generate_daily_review(written)
        assert report.live_metrics.execute_count == 5
        assert len(report.would_trade) == 5, (
            "daily review's WOULD-TRADE list must reflect real live EXECUTE "
            "decisions, not stay empty due to a decision-string vocabulary "
            "mismatch"
        )
