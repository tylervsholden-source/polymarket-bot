"""
Regression test (96th daily review): _record_shadow_decisions()'s
_pricing_sanity_reason only mirrored step 1 of
calibration/decision_policy.py::_check_binary_sanity() — the ask_sum band
([BINARY_SANITY_MIN_ASK_SUM_LIVE, BINARY_SANITY_MAX_ASK_SUM_LIVE]). Steps 2
(bid_sum ceiling, live/paper_strict only) and 3 (per-side min ask floor,
live/paper_strict only) were never implemented for the shadow path, even
though LIVE_CAL_CONFIG.check_bid_overround is True and decision_policy.py
applies both as a hard SUSPICIOUS_UNDERROUND REJECT for every live candidate,
signal-matched or not.

Consequence: an EXECUTE candidate whose bid_yes+bid_no exceeded 1.00 (a
risk-free-arb-impossible quote) or whose ask_yes/ask_no was pathologically
one-sided (< BINARY_SANITY_MIN_SINGLE_ASK_LIVE) still sailed through as
EXECUTE here even though the canonical policy would hard-reject it — the
same class of gap the 95th review found and fixed for the ask_sum band and
staleness checks.

Fix: _pricing_sanity_reason now also checks bid_sum > BINARY_SANITY_MAX_BID_
SUM_LIVE (guarded by LIVE_CAL_CONFIG.check_bid_overround, matching
decision_policy.py's `config.check_bid_overround` guard) and
min(ask_yes, ask_no) < BINARY_SANITY_MIN_SINGLE_ASK_LIVE.
"""
from __future__ import annotations

from datetime import datetime, timezone
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


def _yes_signal(condition_id: str, **market_overrides):
    market = {
        "condition_id": condition_id,
        "question": "Bitcoin 5 min up or down?",
        "best_ask": 0.40,
        "best_bid": 0.38,
        "volume": 500_000,
    }
    market.update(market_overrides)
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


class TestBidSumAndSingleAskSanityAppliedToExecuteCandidates:
    def test_bid_overround_execute_candidate_downgraded_to_reject(self):
        """ask_yes(0.40)+ask_no(0.60)=1.00 is a healthy ask band, but
        bid_yes(0.38)+bid_no(0.65)=1.03 > the live bid_sum ceiling of 1.00 —
        a risk-free-arb-impossible quote decision_policy.py hard-rejects."""
        market, signal = _yes_signal(
            "cond-bidoverround-execute", no_best_ask=0.60, no_best_bid=0.65,
        )
        orch, written = _make_fake_orchestrator()

        orch._record_shadow_decisions(
            [market], [signal], intended_size=3.0,
            market_fetch_utc=datetime.now(timezone.utc),
        )

        assert len(written) == 1
        ds = written[0].decision_summary
        assert ds.decision == "REJECT", (
            "bid-overround EXECUTE candidate was still recorded as EXECUTE — "
            "bid_sum ceiling was never checked for matched signals"
        )
        assert ds.passes_final_gate is False
        assert ds.rejection_reason == "SUSPICIOUS_UNDERROUND"
        assert ds.intended_size_usdc_used == 0.0

    def test_one_sided_ask_execute_candidate_downgraded_to_reject(self):
        """ask_yes(0.03) is far below the live single-ask floor (0.05) — a
        pathological one-sided quote — even though ask_sum(1.03) and
        bid_sum(0.98) both sit comfortably inside their healthy bands and
        execution_realism's own gate would pass (buying at 0.03 against a
        0.60 bayesian_prob is hugely profitable)."""
        market, signal = _yes_signal(
            "cond-onesided-execute",
            best_ask=0.03, no_best_ask=1.00, no_best_bid=0.60,
        )
        orch, written = _make_fake_orchestrator()

        orch._record_shadow_decisions(
            [market], [signal], intended_size=3.0,
            market_fetch_utc=datetime.now(timezone.utc),
        )

        assert len(written) == 1
        ds = written[0].decision_summary
        assert ds.decision == "REJECT", (
            "one-sided-ask EXECUTE candidate was still recorded as EXECUTE — "
            "the per-side min ask floor was never checked for matched signals"
        )
        assert ds.passes_final_gate is False
        assert ds.rejection_reason == "SUSPICIOUS_UNDERROUND"

    def test_healthy_execute_candidate_still_records_as_execute(self):
        """No regression: a comfortably fillable, fresh, well-priced candidate
        (bid_sum and single-ask both healthy) must still record EXECUTE."""
        market, signal = _yes_signal("cond-healthy-execute-2")
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
