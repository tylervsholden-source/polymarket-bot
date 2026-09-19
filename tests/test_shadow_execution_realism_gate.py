"""
Regression test: Orchestrator._record_shadow_decisions() computed
execution_realism.compute_executable_ev(..., policy_mode="live") for every
EXECUTE candidate but never read the result's own passes_gate verdict.

Bug: fill_fraction and executable_ev were extracted from `er`, but `er.
passes_gate` (and the fill_decision/staleness detail behind it) was
discarded. Every candidate that produced a TradeSignal was therefore always
written with decision=EXECUTE_YES/EXECUTE_NO, passes_final_gate=True,
rejection_reason=None — even when execution_realism itself concluded the
order was UNFILLABLE, would only PARTIAL fill (which execution_realism/
core.py's own docstring says must be "rejected at decide() as
PARTIAL_FILL_REJECTED" in live mode), or landed in a STALE/EXPIRED pricing
zone (staleness.should_reject=True).

Consequence: shadow_runner/summary_metrics.py's partial_fill_rejection_rate
and stale_pricing_rate are computed as rejection_counts.get("PARTIAL_FILL_
REJECTED"/"STALE_PRICING", 0) / total — a rejection_reason that could never
be produced for an executed candidate on this path. Both rates were
therefore pinned at 0.0 regardless of real execution risk, and monitoring/
readiness_checks.py's check_partial_fill_rejection_rate/check_stale_pricing_
rate (BLOCKER/FAIL-level gates behind the TINY_PILOT_CANDIDATE verdict that
control_plane/live_gate.py's readiness check ultimately gates real order
placement on) always read GREEN no matter what the live orderbook actually
looked like.

Fix: when `er.passes_gate` is False, downgrade the record to REJECT with
the specific reason (PARTIAL_FILL_REJECTED / UNFILLABLE / STALE_PRICING /
EXECUTABLE_EV_BELOW_THRESHOLD), and set passes_final_gate=False —
mirroring what a real live order attempt would have experienced.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from agents.orchestrator import Orchestrator


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


def _yes_signal(condition_id: str, volume: float):
    market = {
        "condition_id": condition_id,
        "question": "Bitcoin 5 min up or down?",
        "best_ask": 0.40,
        "best_bid": 0.38,
        "volume": volume,
    }
    signal = SimpleNamespace(
        market=market,
        direction="YES",
        bayesian_prob=0.60,
        market_price=0.40,
        edge=0.20,
        entry_price=0.40,
        size=3.0,
        z_score=0.0,
        signal_type="ARB",
        reasoning="test",
        token_id="tok-yes",
        side_diagnostics=None,
    )
    return market, signal


class TestExecutionRealismGateAppliedToShadowRecord:
    def test_generously_liquid_fresh_trade_still_records_as_execute(self):
        """Sanity check / no regression: a comfortably fillable, fresh
        candidate must still be recorded as an EXECUTE, same as before."""
        market, signal = _yes_signal("cond-healthy", volume=500_000)
        orch, written = _make_fake_orchestrator()

        orch._record_shadow_decisions(
            [market], [signal], intended_size=3.0,
            market_fetch_utc=datetime.now(timezone.utc),
        )

        assert len(written) == 1
        ds = written[0].decision_summary
        assert ds.decision == "EXECUTE_YES"
        assert ds.passes_final_gate is True
        assert ds.rejection_reason is None

    def test_unfillable_liquidity_downgrades_execute_to_reject(self):
        """intended_size=3.0 vs liquidity=5.0 -> ratio=0.6 > 50% -> UNFILLABLE."""
        market, signal = _yes_signal("cond-unfillable", volume=5.0)
        orch, written = _make_fake_orchestrator()

        orch._record_shadow_decisions(
            [market], [signal], intended_size=3.0,
            market_fetch_utc=datetime.now(timezone.utc),
        )

        assert len(written) == 1
        ds = written[0].decision_summary
        assert ds.decision == "REJECT", (
            "UNFILLABLE candidate was still recorded as EXECUTE — "
            "execution_realism's passes_gate verdict was ignored"
        )
        assert ds.passes_final_gate is False
        assert ds.rejection_reason == "UNFILLABLE"
        assert ds.intended_size_usdc_used == 0.0

    def test_partial_fill_downgrades_execute_to_partial_fill_rejected(self):
        """intended_size=3.0 vs liquidity=9.0 -> ratio=0.333, in (25%, 50%] -> PARTIAL,
        which live-mode policy hard-rejects per execution_realism/core.py."""
        market, signal = _yes_signal("cond-partial", volume=9.0)
        orch, written = _make_fake_orchestrator()

        orch._record_shadow_decisions(
            [market], [signal], intended_size=3.0,
            market_fetch_utc=datetime.now(timezone.utc),
        )

        assert len(written) == 1
        ds = written[0].decision_summary
        assert ds.decision == "REJECT", (
            "PARTIAL-fill candidate was still recorded as EXECUTE in live "
            "policy_mode — execution_realism's own docstring requires this "
            "to be rejected as PARTIAL_FILL_REJECTED"
        )
        assert ds.passes_final_gate is False
        assert ds.rejection_reason == "PARTIAL_FILL_REJECTED"

    def test_stale_pricing_downgrades_execute_to_stale_pricing_reject(self):
        """A market_fetch_utc far enough in the past pushes the 5-minute
        horizon's staleness zone to EXPIRED (should_reject=True)."""
        market, signal = _yes_signal("cond-stale", volume=500_000)
        orch, written = _make_fake_orchestrator()

        orch._record_shadow_decisions(
            [market], [signal], intended_size=3.0,
            market_fetch_utc=datetime.now(timezone.utc) - timedelta(hours=2),
        )

        assert len(written) == 1
        ds = written[0].decision_summary
        assert ds.decision == "REJECT", (
            "EXPIRED-staleness candidate was still recorded as EXECUTE — "
            "staleness.should_reject was computed but never checked"
        )
        assert ds.passes_final_gate is False
        assert ds.rejection_reason == "STALE_PRICING"

    def test_partial_fill_rejection_is_now_visible_to_summary_metrics(self):
        """End-to-end: shadow_runner.summary_metrics.compute_summary_metrics()
        must actually count this PARTIAL-fill record's rejection — this is
        the concrete metric (partial_fill_rejection_rate) that was silently
        pinned at 0.0 before the fix."""
        from shadow_runner.summary_metrics import compute_summary_metrics

        market, signal = _yes_signal("cond-partial-metrics", volume=9.0)
        orch, written = _make_fake_orchestrator()
        orch._record_shadow_decisions(
            [market], [signal], intended_size=3.0,
            market_fetch_utc=datetime.now(timezone.utc),
        )

        metrics = compute_summary_metrics(written, profile="live")
        assert metrics.partial_fill_rejected_count == 1
        assert metrics.partial_fill_rejection_rate == 1.0
