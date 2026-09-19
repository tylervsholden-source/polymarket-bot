"""
Regression test (92nd daily review): live-mode shadow records could never be
tagged STALE_PRICING or SUSPICIOUS_UNDERROUND, so 2 of the readiness checks
behind the live gate were structurally vacuous for live data.

Bug: agents/orchestrator.py::_record_shadow_decisions() built rejection_reason
for REJECT candidates from only _side_diag.direction_reason (a NoSideStatus
value like "BOTH_EDGES_NEGATIVE") or the literal "NO_SIGNAL_PRODUCED". It
never checked pricing snapshot age or the binary YES+NO ask sum, so a
genuinely stale snapshot or a suspiciously underround/overround market was
silently absorbed into one of those other reasons.

shadow_runner/summary_metrics.py's stale_pricing_rate and
suspicious_underround_rate only recognize rejection_reason == "STALE_PRICING"
/ "SUSPICIOUS_UNDERROUND" (rej_counts.get(...)) — the exact
CalibrationRejectionReason strings calibration/decision_policy.py uses.
monitoring/readiness_checks.py's check_stale_pricing_rate and
check_suspicious_underround_rate feed on those rates to help gate the
TINY_PILOT_CANDIDATE verdict that control_plane/live_gate.py checks before
allowing real orders. With rejection_reason never producing those strings for
live records, both rates stayed pinned at 0.0 regardless of what the live
pricing feed actually did.

Fix: classify pricing-sanity issues (stale snapshot per LIVE_CAL_CONFIG's
60s threshold; YES+NO ask sum outside the live sanity band
[BINARY_SANITY_MIN_ASK_SUM_LIVE, BINARY_SANITY_MAX_ASK_SUM_LIVE]) before
falling back to the NoSideStatus/"NO_SIGNAL_PRODUCED" reasons.
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


def _reject_market(condition_id: str, **overrides):
    """A candidate with no matching signal (sig_match is None -> REJECT)."""
    market = {
        "condition_id": condition_id,
        "question": "Bitcoin 5 min up or down?",
        "best_ask": 0.40,
        "best_bid": 0.38,
        "volume": 500_000,
    }
    market.update(overrides)
    return market


class TestPricingSanityRejectionReasons:
    def test_stale_snapshot_tagged_stale_pricing(self):
        market = _reject_market("cond-stale-reject")
        orch, written = _make_fake_orchestrator()

        stale_fetch_time = datetime.now(timezone.utc) - timedelta(seconds=90)
        orch._record_shadow_decisions(
            [market], [], intended_size=3.0, market_fetch_utc=stale_fetch_time,
        )

        assert len(written) == 1
        assert written[0].decision_summary.rejection_reason == "STALE_PRICING"

    def test_suspicious_underround_tagged(self):
        # ask_yes(0.30) + no_best_ask(0.30) = 0.60, well below the live
        # sanity floor of 0.97 -> SUSPICIOUS_UNDERROUND, not NO_SIGNAL_PRODUCED.
        market = _reject_market(
            "cond-underround-reject",
            best_ask=0.30,
            no_best_ask=0.30,
        )
        orch, written = _make_fake_orchestrator()

        orch._record_shadow_decisions(
            [market], [], intended_size=3.0, market_fetch_utc=datetime.now(timezone.utc),
        )

        assert len(written) == 1
        assert written[0].decision_summary.rejection_reason == "SUSPICIOUS_UNDERROUND"

    def test_healthy_pricing_falls_back_to_no_signal_produced(self):
        # Fresh snapshot, no real NO-book (synthetic ask_no ~= 1 - bid_yes),
        # ask sum stays near 1.0 -> within the sanity band -> old fallback
        # behavior must be preserved.
        market = _reject_market("cond-healthy-reject")
        orch, written = _make_fake_orchestrator()

        orch._record_shadow_decisions(
            [market], [], intended_size=3.0, market_fetch_utc=datetime.now(timezone.utc),
        )

        assert len(written) == 1
        assert written[0].decision_summary.rejection_reason == "NO_SIGNAL_PRODUCED"

    def test_stale_takes_priority_over_underround(self):
        # Both conditions triggered at once -> staleness is checked first
        # since a stale snapshot invalidates the pricing entirely.
        market = _reject_market(
            "cond-both-reject",
            best_ask=0.30,
            no_best_ask=0.30,
        )
        orch, written = _make_fake_orchestrator()

        stale_fetch_time = datetime.now(timezone.utc) - timedelta(seconds=90)
        orch._record_shadow_decisions(
            [market], [], intended_size=3.0, market_fetch_utc=stale_fetch_time,
        )

        assert len(written) == 1
        assert written[0].decision_summary.rejection_reason == "STALE_PRICING"
