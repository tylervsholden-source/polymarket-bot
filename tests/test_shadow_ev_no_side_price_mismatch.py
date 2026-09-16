"""
Regression test: Orchestrator._record_shadow_decisions() passed the YES-side
probability/price into compute_executable_ev() for NO-direction signals,
instead of the NO-side probability/price it had already computed for the
same record two dozen lines earlier.

Bug: agents/orchestrator.py::_record_shadow_decisions() computes the correct
NO-side ask (`_ask_no`, real-book or synthetic complement of YES) for the
`pricing_snap`/`signal_snap` fields, but the very next block — the
compute_executable_ev() call that produces `execution_adjusted_ev` — ignored
`_ask_no` entirely and unconditionally passed `calibrated_event_probability=
yes_prob, ask_price=ask_yes`, regardless of `sig_match.direction`. For a NO
signal this computes compute_executable_ev's `theoretical = calibrated_prob -
ask_price` using the *YES* market's mispricing instead of the actual NO
trade being evaluated — an unrelated number.

This is not just a display glitch: `execution_adjusted_ev` feeds
`shadow_runner/summary_metrics.py`'s `mean_ev_haircut_pct`, which
`monitoring/readiness_checks.py::check_ev_haircut_pct()` consumes inside
`shadow_runner/readiness.py::assess_readiness()` to help decide the
TINY_PILOT_CANDIDATE / NO_GO / CONDITIONAL_REVIEW verdict written to
`readiness_verdict.json` — the same file `control_plane/live_gate.py`
gates real order placement on (per the 53rd daily review). NO is the
dominant trade direction in this bot (docs/architecture.md), so this
corrupted the readiness signal for most of the live shadow corpus.

Concrete reproduction (see also the manual repro run against
execution_realism.core.compute_executable_ev directly): yes_prob=0.30,
ask_yes=0.28, real NO ask=0.55 — a genuinely good NO trade
(true edge = 0.70 - 0.55 = 0.15, executable_ev ≈ +0.122, passes_gate=True).
Pre-fix, the call used yes_prob/ask_yes (0.30/0.28) instead, producing
executable_ev ≈ -0.008 and passes_gate=False — a solid trade misreported
as a loser purely because of which side's numbers were plugged in.

Fix: branch on `sig_match.direction` and pass the NO-side probability
(`round(1.0 - yes_prob, 6)`) and NO-side ask (`_ask_no`, already computed
for the pricing snapshot) when the signal is NO; YES signals are unaffected.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

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


def _no_signal(condition_id: str, yes_prob: float, edge: float):
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
        market=market,
        direction="NO",
        bayesian_prob=yes_prob,
        market_price=0.28,
        edge=edge,
        entry_price=0.55,
        size=3.0,
        z_score=0.0,
        signal_type="ARB",
        reasoning="test",
        token_id="tok-no",
        side_diagnostics=None,
    )
    return market, signal


class TestNoSideExecutableEvUsesNoSidePricing:
    def test_no_signal_passes_no_side_probability_and_ask(self):
        market, signal = _no_signal("cond-1", yes_prob=0.30, edge=0.15)
        orch, _written = _make_fake_orchestrator()

        captured_kwargs = {}

        def _spy(*args, **kwargs):
            captured_kwargs.update(kwargs)
            from execution_realism.core import compute_executable_ev as _real
            return _real(*args, **kwargs)

        with patch("agents.orchestrator.compute_executable_ev", side_effect=_spy):
            orch._record_shadow_decisions([market], [signal], intended_size=3.0)

        assert captured_kwargs, "compute_executable_ev was never called"
        assert captured_kwargs["calibrated_event_probability"] == pytest.approx(0.70)
        assert captured_kwargs["ask_price"] == pytest.approx(0.55)

    def test_no_signal_execution_adjusted_ev_reflects_no_side_edge(self):
        """A genuinely good NO trade (true edge=0.15) must not be recorded as
        a loser (negative executable_ev) just because of which side's prices
        were plugged into compute_executable_ev.
        """
        market, signal = _no_signal("cond-2", yes_prob=0.30, edge=0.15)
        orch, written = _make_fake_orchestrator()

        orch._record_shadow_decisions([market], [signal], intended_size=3.0)

        assert len(written) == 1
        summary = written[0].decision_summary
        assert summary.execution_adjusted_ev is not None
        assert summary.execution_adjusted_ev > 0, (
            f"expected a positive executable EV for a true 0.15-edge NO trade, "
            f"got {summary.execution_adjusted_ev} (YES-side pricing bug?)"
        )

    def test_yes_signal_still_uses_yes_side_pricing(self):
        """Sanity check: the fix must not disturb YES-direction signals."""
        market = {
            "condition_id": "cond-3",
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
        orch, _written = _make_fake_orchestrator()

        captured_kwargs = {}

        def _spy(*args, **kwargs):
            captured_kwargs.update(kwargs)
            from execution_realism.core import compute_executable_ev as _real
            return _real(*args, **kwargs)

        with patch("agents.orchestrator.compute_executable_ev", side_effect=_spy):
            orch._record_shadow_decisions([market], [signal], intended_size=3.0)

        assert captured_kwargs["calibrated_event_probability"] == pytest.approx(0.75)
        assert captured_kwargs["ask_price"] == pytest.approx(0.60)
