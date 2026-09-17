"""
Regression test: Orchestrator._record_shadow_decisions() hardcoded
snapshot_age_seconds=0.0 for every recorded candidate, instead of the real
elapsed time since candidates' best_ask/best_bid were actually fetched
(client.get_active_markets() at the top of _cycle()).

Bug: agents/orchestrator.py::_record_shadow_decisions() built both the
PricingSnapshot (snapshot_age_seconds=0.0, pricing_timestamp_utc=now) and
the compute_executable_ev() call (snapshot_age_seconds=0.0) as if the
candidate's prices were captured at the exact instant of shadow-recording.
In reality, _cycle() fetches `markets`/`candidates` once, then runs the full
async multi-agent pipeline (parallel research + signal generation, followed
by a sequential Claude reviewer API call) before _record_shadow_decisions()
is ever called — routinely tens of seconds later, easily past a 5-minute
market's 30s "fresh" window.

compute_executable_ev() feeds this age into
execution_realism.staleness_penalty.compute_staleness_penalty(), whose
penalty is subtracted from executable_ev. Pinning age to 0.0 always selects
the minimum (FRESH, penalty=0.0) staleness zone, so execution_adjusted_ev is
systematically inflated for every live-shadow record. That value feeds
shadow_runner/summary_metrics.py's mean_ev_haircut_pct, which
shadow_runner/readiness.py::assess_readiness() passes to
monitoring/readiness_checks.py::check_ev_haircut_pct() — one of the checks
behind the TINY_PILOT_CANDIDATE verdict that control_plane/live_gate.py
gates real order placement on (per the 53rd daily review). A systematically
understated EV haircut biases that readiness verdict toward GO.

Fix: thread the real market-fetch timestamp (captured once at the top of
_cycle(), right after client.get_active_markets()) through to
_record_shadow_decisions() as `market_fetch_utc`, and compute
snapshot_age_seconds = (now - market_fetch_utc).total_seconds() instead of
hardcoding 0.0, for both the PricingSnapshot and the compute_executable_ev()
call.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agents.orchestrator import Orchestrator


def _make_fake_orchestrator():
    """Orchestrator.__new__ + only the attributes _record_shadow_decisions touches.

    Avoids running __init__ (which opens real HTTP sessions / API clients).
    """
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
    """A generously liquid, comfortably-fillable YES trade so slippage/fill
    effects stay constant across both calls — only staleness should differ.
    """
    market = {
        "condition_id": condition_id,
        "question": "Bitcoin 5 min up or down?",
        "best_ask": 0.40,
        "best_bid": 0.38,
        "volume": 500_000,
    }
    signal = SimpleNamespace(
        market=market,
        direction="YES",
        bayesian_prob=0.60,   # gross edge = 0.60 - 0.40 = 0.20
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


class TestSnapshotAgeReflectsRealMarketFetchTime:
    def test_snapshot_age_is_zero_when_market_fetch_utc_is_recent(self):
        """Sanity check: a market fetched "just now" should report ~0s age."""
        market, signal = _yes_signal("cond-fresh")
        orch, written = _make_fake_orchestrator()

        orch._record_shadow_decisions(
            [market], [signal], intended_size=3.0,
            market_fetch_utc=datetime.now(timezone.utc),
        )

        assert len(written) == 1
        assert written[0].pricing.snapshot_age_seconds == pytest.approx(0.0, abs=1.0)

    def test_snapshot_age_reflects_real_elapsed_time_since_market_fetch(self):
        """The bug: snapshot_age_seconds must NOT stay pinned at 0.0 when the
        candidate's prices were actually fetched a while ago — it has to
        reflect the real gap between market_fetch_utc and recording time.
        """
        market, signal = _yes_signal("cond-stale")
        orch, written = _make_fake_orchestrator()

        stale_fetch_time = datetime.now(timezone.utc) - timedelta(seconds=100)
        orch._record_shadow_decisions(
            [market], [signal], intended_size=3.0,
            market_fetch_utc=stale_fetch_time,
        )

        assert len(written) == 1
        age = written[0].pricing.snapshot_age_seconds
        # Pre-fix this was hardcoded 0.0 regardless of market_fetch_utc.
        assert age > 50.0, (
            f"snapshot_age_seconds={age} — expected ~100s (real elapsed time "
            f"since market_fetch_utc), got a value consistent with the old "
            f"hardcoded 0.0 bug"
        )
        assert age == pytest.approx(100.0, abs=2.0)

    def test_stale_snapshot_lowers_executable_ev_via_staleness_penalty(self):
        """The concrete financial effect: a candidate whose prices are ~100s
        old (well past the 5-minute horizon's 30s FRESH window, landing in
        the STALE zone, penalty=0.015 — see execution_realism/types.py's
        STALENESS_5M) must show a *lower* execution_adjusted_ev than an
        otherwise-identical candidate recorded as fresh. Pre-fix, both were
        computed with snapshot_age_seconds=0.0 (always FRESH, penalty=0.0),
        so this penalty never applied and the two EVs came out identical.
        """
        fresh_market, fresh_signal = _yes_signal("cond-fresh-ev")
        stale_market, stale_signal = _yes_signal("cond-stale-ev")

        fresh_orch, fresh_written = _make_fake_orchestrator()
        fresh_orch._record_shadow_decisions(
            [fresh_market], [fresh_signal], intended_size=3.0,
            market_fetch_utc=datetime.now(timezone.utc),
        )

        stale_orch, stale_written = _make_fake_orchestrator()
        stale_orch._record_shadow_decisions(
            [stale_market], [stale_signal], intended_size=3.0,
            market_fetch_utc=datetime.now(timezone.utc) - timedelta(seconds=100),
        )

        fresh_ev = fresh_written[0].decision_summary.execution_adjusted_ev
        stale_ev = stale_written[0].decision_summary.execution_adjusted_ev
        assert fresh_ev is not None and stale_ev is not None

        # STALE zone penalty for a 5-minute horizon is 0.015 (execution_realism
        # /types.py::STALENESS_5M.stale_penalty) versus 0.0 for FRESH.
        assert stale_ev < fresh_ev, (
            f"stale execution_adjusted_ev={stale_ev} should be lower than "
            f"fresh execution_adjusted_ev={fresh_ev} once real snapshot age "
            f"is applied — got equal/higher, consistent with the old "
            f"hardcoded snapshot_age_seconds=0.0 bug"
        )
        assert fresh_ev - stale_ev == pytest.approx(0.015, abs=1e-6)

    def test_no_market_fetch_utc_falls_back_to_zero_age(self):
        """Backward-compatible default: omitting market_fetch_utc (e.g. an
        older/alternate caller) must not crash, and behaves like the old
        code — age 0.0 — rather than raising.
        """
        market, signal = _yes_signal("cond-no-fetch-ts")
        orch, written = _make_fake_orchestrator()

        orch._record_shadow_decisions([market], [signal], intended_size=3.0)

        assert len(written) == 1
        assert written[0].pricing.snapshot_age_seconds == 0.0
