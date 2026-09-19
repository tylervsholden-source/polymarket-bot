"""
Regression test (95th daily review): pricing-sanity checks (STALE_PRICING /
SUSPICIOUS_UNDERROUND) were only ever consulted for candidates that were
*already* going to be recorded as REJECT for some other reason (no matching
signal, or execution_realism's own gate) — see the 92nd/93rd reviews'
tests/test_shadow_stale_underround_rejection_reason.py and
tests/test_shadow_execution_realism_gate.py.

Bug: Orchestrator._record_shadow_decisions() built `_rejection_reason` (and
therefore `_is_execute_after_realism`) with an if/elif chain where the
STALE_PRICING/SUSPICIOUS_UNDERROUND checks lived in `elif` branches only
reached once `_is_execute_after_realism` was already False. A candidate with
a matching signal (is_execute=True) whose execution_realism gate passed
(_er_rejection_reason is None) took the `if _is_execute_after_realism:`
branch straight to `_rejection_reason = None` — so pricing sanity was never
evaluated for it at all, no matter how far ask_yes+ask_no was outside the
live sanity band [BINARY_SANITY_MIN_ASK_SUM_LIVE, BINARY_SANITY_MAX_ASK_SUM_
LIVE] or how stale the snapshot was. calibration/decision_policy.py::decide()
— the canonical policy this shadow path exists to mirror — runs this exact
check (step 6b) as a hard REJECT for every live candidate before any EV gate,
regardless of whether a signal matched.

Consequence: shadow_runner/summary_metrics.py's suspicious_underround_rate /
stale_pricing_rate (feeding monitoring/readiness_checks.py's
check_suspicious_underround_rate / check_stale_pricing_rate, both behind the
TINY_PILOT_CANDIDATE verdict control_plane/live_gate.py gates real order
placement on) could never count a suspiciously-priced or stale-snapshot
candidate that happened to also match a real trade signal — exactly the
candidates that matter most, since only those risk real capital.

Fix: compute the pricing-sanity reason unconditionally (not only inside the
already-REJECT elif chain) and let it downgrade _is_execute_after_realism the
same way an execution_realism gate failure already does.
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


class TestPricingSanityAppliedToExecuteCandidates:
    def test_underround_execute_candidate_downgraded_to_reject(self):
        """ask_yes(0.40) + ask_no(0.30) = 0.70, well below the live floor of
        0.97 -> even though a real signal matched and execution_realism's own
        gate passes (fresh, deeply liquid), this must still be REJECTed."""
        market, signal = _yes_signal("cond-underround-execute", no_best_ask=0.30)
        orch, written = _make_fake_orchestrator()

        orch._record_shadow_decisions(
            [market], [signal], intended_size=3.0,
            market_fetch_utc=datetime.now(timezone.utc),
        )

        assert len(written) == 1
        ds = written[0].decision_summary
        assert ds.decision == "REJECT", (
            "suspiciously underround EXECUTE candidate was still recorded as "
            "EXECUTE — pricing sanity was never checked for matched signals"
        )
        assert ds.passes_final_gate is False
        assert ds.rejection_reason == "SUSPICIOUS_UNDERROUND"
        assert ds.intended_size_usdc_used == 0.0

    def test_stale_execute_candidate_downgraded_to_reject(self):
        """A market_fetch_utc older than LIVE_CAL_CONFIG.max_snapshot_age_seconds
        (60s) must reject a matched, otherwise-healthy signal too."""
        market, signal = _yes_signal("cond-stale-execute")
        orch, written = _make_fake_orchestrator()

        orch._record_shadow_decisions(
            [market], [signal], intended_size=3.0,
            market_fetch_utc=datetime.now(timezone.utc) - timedelta(seconds=90),
        )

        assert len(written) == 1
        ds = written[0].decision_summary
        assert ds.decision == "REJECT", (
            "stale-snapshot EXECUTE candidate was still recorded as EXECUTE — "
            "pricing sanity was never checked for matched signals"
        )
        assert ds.passes_final_gate is False
        assert ds.rejection_reason == "STALE_PRICING"

    def test_bid_overround_execute_candidate_downgraded_to_reject(self):
        """96th daily review: _check_binary_sanity()'s step 2 (bid_sum ceiling,
        checked unconditionally in live mode per LIVE_CAL_CONFIG.
        check_bid_overround=True) was never ported to this shadow path — only
        step 1 (ask_sum band) was. bid_yes(0.60) + no_best_bid(0.41) = 1.01,
        just over the live ceiling of 1.00 (near risk-free-arb pricing), while
        ask_yes(0.60) + no_best_ask(0.41) = 1.01 stays comfortably inside the
        ask_sum band — isolating the bid_sum check."""
        market, signal = _yes_signal(
            "cond-bid-overround-execute",
            best_ask=0.60, best_bid=0.60,
            no_best_ask=0.41, no_best_bid=0.41,
        )
        signal.bayesian_prob = 0.75  # edge=0.15 vs ask_yes=0.60, clears execution_realism
        orch, written = _make_fake_orchestrator()

        orch._record_shadow_decisions(
            [market], [signal], intended_size=3.0,
            market_fetch_utc=datetime.now(timezone.utc),
        )

        assert len(written) == 1
        ds = written[0].decision_summary
        assert ds.decision == "REJECT", (
            "bid_sum > BINARY_SANITY_MAX_BID_SUM_LIVE EXECUTE candidate was "
            "still recorded as EXECUTE — bid_sum ceiling was never checked"
        )
        assert ds.passes_final_gate is False
        assert ds.rejection_reason == "SUSPICIOUS_UNDERROUND"
        assert ds.intended_size_usdc_used == 0.0

    def test_one_sided_ask_execute_candidate_downgraded_to_reject(self):
        """96th daily review: _check_binary_sanity()'s step 3 (individual ask
        floor — a pathologically one-sided quote) was never ported either.
        no_best_ask(0.04) is below BINARY_SANITY_MIN_SINGLE_ASK_LIVE (0.05)
        while ask_yes(0.93) + no_best_ask(0.04) = 0.97 stays inside the
        ask_sum band and bid_yes(0.92) + no_best_bid(0.03) = 0.95 stays under
        the bid_sum ceiling — isolating the individual-ask check."""
        market, signal = _yes_signal(
            "cond-one-sided-ask-execute",
            best_ask=0.93, best_bid=0.92,
            no_best_ask=0.04, no_best_bid=0.03,
        )
        signal.bayesian_prob = 0.99  # edge=0.06 vs ask_yes=0.93, clears execution_realism
        orch, written = _make_fake_orchestrator()

        orch._record_shadow_decisions(
            [market], [signal], intended_size=3.0,
            market_fetch_utc=datetime.now(timezone.utc),
        )

        assert len(written) == 1
        ds = written[0].decision_summary
        assert ds.decision == "REJECT", (
            "one-sided-ask (< BINARY_SANITY_MIN_SINGLE_ASK_LIVE) EXECUTE "
            "candidate was still recorded as EXECUTE — individual ask floor "
            "was never checked"
        )
        assert ds.passes_final_gate is False
        assert ds.rejection_reason == "SUSPICIOUS_UNDERROUND"
        assert ds.intended_size_usdc_used == 0.0

    def test_healthy_execute_candidate_still_records_as_execute(self):
        """No regression: a comfortably fillable, fresh, well-priced candidate
        must still be recorded as EXECUTE, same as before."""
        market, signal = _yes_signal("cond-healthy-execute")
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
