"""
Regression test for the 71st daily review.

AutonomousDecisionEngine._assess_risk() used to add an unconditional +2
risk-score penalty to every NO-direction signal, justified by a stale
comment ("NO trades historically 33% WR"), with no YES equivalent. That
extra +2 can push a signal into the next risk tier (e.g. LOW→MEDIUM or
MEDIUM→HIGH), which evaluate() then converts into a smaller size_multiplier
(LOW=1.0, MEDIUM=0.75, HIGH=0.50) applied to the real order size in
agents/orchestrator.py — undersizing (or more readily skipping) NO trades
relative to an otherwise-identical YES signal.

strategies/arbitrage_engine.py's GATE 4 / Factor 2 carried the exact same
assumption ("NO edges less reliable", "NO trades historically 33% WR") and
was removed earlier the same day (commit 4358096) once data/3day_eval.txt
(last 44 real trades) showed the opposite: NO 55.6% WR / +$15.40 PnL vs YES
47.1% WR / -$14.39 PnL. autonomous_engine.py is a separate class/file and
was missed in that cleanup.

This test asserts a NO-direction signal's risk assessment matches an
otherwise-identical YES-direction signal's.
"""
from __future__ import annotations

from agents.autonomous_engine import AutonomousDecisionEngine, RiskLevel


class _Signal:
    def __init__(self, direction: str, edge: float = 0.10, risk_flags=None, confluence_score: float = 0.6):
        self.direction = direction
        self.edge = edge
        self.risk_flags = risk_flags or []
        self.confluence_score = confluence_score


def test_no_direction_risk_level_matches_yes_for_identical_signal_quality():
    engine = AutonomousDecisionEngine()

    yes_signal = _Signal(direction="YES", edge=0.07, risk_flags=["a", "b"])
    no_signal = _Signal(direction="NO", edge=0.07, risk_flags=["a", "b"])

    yes_level = engine._assess_risk(yes_signal, review_decision=None, capital=100.0, open_count=0, max_positions=5)
    no_level = engine._assess_risk(no_signal, review_decision=None, capital=100.0, open_count=0, max_positions=5)

    assert no_level == yes_level, (
        f"NO-direction signal got a harsher risk tier ({no_level}) than an "
        f"otherwise-identical YES signal ({yes_level}) purely from direction"
    )


def test_no_direction_no_longer_bumps_risk_tier():
    # Chosen so the old +2 NO penalty alone crosses a tier boundary
    # (edge<0.08 -> +2, 2 flags -> +2 => 4 without the removed penalty,
    # which is RiskLevel.MEDIUM; the old code added +2 more -> 6 -> HIGH).
    engine = AutonomousDecisionEngine()
    signal = _Signal(direction="NO", edge=0.07, risk_flags=["a", "b"])

    level = engine._assess_risk(signal, review_decision=None, capital=100.0, open_count=0, max_positions=5)

    assert level == RiskLevel.MEDIUM, (
        f"expected MEDIUM (edge+flags only), got {level} — a leftover NO "
        f"direction penalty would push this to HIGH"
    )
