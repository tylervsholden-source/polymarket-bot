"""
tests/test_regime_review.py

Round 105 daily review: regression test for compute_regime_review()
silently reporting is_stable=True when only one rolling window can be
formed (n >= window_size but n < window_size + step). With exactly one
window there is no adjacent-window delta to compare, so no trend can be
detected either way — the correct verdict is "unknown", matching the
existing n < window_size convention ("unknown != stable"), not "stable".

check_regime_stability() (monitoring/readiness_checks.py) treats
is_stable=False with zero flags as WARN ("insufficient data"), so this
also verifies the fix restores the WARN classification instead of a
false GREEN reaching the live readiness gate.
"""
from __future__ import annotations

from datetime import datetime, timezone

from monitoring.readiness_checks import CheckLevel, check_regime_stability
from monitoring.regime_review import compute_regime_review
from shadow_runner.types import DecisionSummary, PricingSnapshot, ShadowDecisionRecord, SignalSnapshot

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


def _make_record(i, profile="live"):
    sig = SignalSnapshot(
        asset="BTC", horizon_minutes=5, signal_timestamp_utc=_NOW,
        predicted_class="UP", raw_confidence=0.80,
        class_probabilities={"UP": 0.80, "DOWN": 0.12, "NO_TRADE": 0.08},
    )
    pri = PricingSnapshot(
        market_id=f"mkt-{i:04d}", ask_yes=0.44, bid_yes=0.42, ask_no=0.57, bid_no=0.55,
        liquidity=5000.0, pricing_timestamp_utc=_NOW, snapshot_age_seconds=0.0,
    )
    dec = DecisionSummary(
        decision="EXECUTE_YES", rejection_reason=None, policy_mode=profile,
        passes_final_gate=True, intended_size_usdc_used=20.0,
        execution_adjusted_ev=0.035, required_edge_threshold=0.02,
    )
    return ShadowDecisionRecord(
        record_id=f"rec-{i:04d}", run_id="run-test", ts_recorded_utc=_NOW,
        signal=sig, pricing=pri, policy_profile=profile,
        intended_size_usdc=20.0, decision_summary=dec,
    )


class TestSingleWindowIsUnknownNotStable:
    def test_exactly_one_window_is_not_stable(self):
        # window_size=20, step=10 -> n=25 forms exactly one window (0:20);
        # a second window would need n >= 30.
        records = [_make_record(i) for i in range(25)]
        review = compute_regime_review(records, profile="live")
        assert len(review.rolling_snapshots) == 1
        assert review.is_stable is False

    def test_exactly_one_window_readiness_check_is_warn_not_green(self):
        records = [_make_record(i) for i in range(25)]
        review = compute_regime_review(records, profile="live")
        result = check_regime_stability(review)
        assert result.level == CheckLevel.WARN

    def test_two_windows_with_no_flags_is_stable(self):
        # n=35 -> windows at (0:20) and (10:30) -> 2 snapshots, enough to
        # compare deltas; identical records everywhere so no flags fire.
        records = [_make_record(i) for i in range(35)]
        review = compute_regime_review(records, profile="live")
        assert len(review.rolling_snapshots) == 2
        assert review.is_stable is True
        result = check_regime_stability(review)
        assert result.level == CheckLevel.GREEN
