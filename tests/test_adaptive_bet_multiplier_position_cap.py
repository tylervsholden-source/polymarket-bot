"""
Regression guard for CLAUDE.md kural 1: "Max tek pozisyon: portföyün %20'si
(Kelly override yapmaz)".

Bug: agents/orchestrator.py::_cycle() applies compute_bet_size() (which
correctly clamps bet_size to min(HARD_MAX_BET, capital*max_position_pct)),
then multiplies the result by self._adaptive_bet_multiplier — the
AutonomousDecisionEngine.get_adaptive_params() AGGRESSIVE-mode value (1.15,
set once win_rate>65% and total_trades>10) — with no re-clamp back to the
20%-of-capital position cap. Only a flat $4.00 HARD_MAX_BET check ran after
that multiplication.

For capital below roughly $33 the 20% position cap binds tighter than the
flat $4.00 HARD_MAX_BET, so e.g. capital=$15 -> compute_bet_size() clamps to
$3.00 (the 20% cap) -> *1.15 = $3.45, which is still under $4.00 and so
sails straight past the HARD_MAX_BET check untouched — a real position 15%
over the bot's own documented, non-negotiable per-trade ceiling, placed with
live capital. Same "boost applied after the cap, never re-clamped" shape as
the GOLDEN_HOUR/GOOD_HOUR bug fixed in the 39th daily review
(tests/test_golden_hour_boost_bypasses_max_bet_cap.py), just a different
multiplier and call site (agents/orchestrator.py's AutonomousEngine adaptive
bet multiplier, not arbitrage_engine.py's time-of-day boost).
"""
from agents.orchestrator import apply_adaptive_bet_multiplier, compute_bet_size

MAX_POSITION_PCT = 0.20
AGGRESSIVE_MULTIPLIER = 1.15  # AutonomousDecisionEngine.get_adaptive_params()


def test_aggressive_multiplier_never_exceeds_20_percent_cap():
    # Capitals where the 20% position cap binds tighter than the $4 HARD_MAX_BET,
    # so the multiplier isn't accidentally saved by the flat hard cap downstream.
    for capital in (10.0, 12.0, 15.0, 18.0, 19.0):
        bet_size, _ = compute_bet_size(
            capital=capital,
            signal_size=999.0,  # Kelly recommends far more than any cap
            min_bet=3.0,
            max_bet=8.0,
            max_position_pct=MAX_POSITION_PCT,
        )
        adjusted = apply_adaptive_bet_multiplier(
            bet_size, AGGRESSIVE_MULTIPLIER, capital, MAX_POSITION_PCT,
        )
        assert adjusted <= capital * MAX_POSITION_PCT + 1e-9, (
            f"capital=${capital}: AGGRESSIVE multiplier pushed bet_size to "
            f"${adjusted:.2f}, above the 20% cap (${capital * MAX_POSITION_PCT:.2f})"
        )


def test_aggressive_multiplier_reproduces_precise_reported_violation():
    # Exact figures from the bug report: capital=$15 -> 20% cap = $3.00 ->
    # pre-fix 1.15x = $3.45 (a real 15% overage that the $4.00 HARD_MAX_BET
    # check never catches, since $3.45 < $4.00).
    capital = 15.0
    bet_size, _ = compute_bet_size(
        capital=capital, signal_size=999.0, min_bet=3.0, max_bet=8.0,
        max_position_pct=MAX_POSITION_PCT,
    )
    assert bet_size == 3.0  # sanity: 20% cap bound, well under $4 HARD_MAX_BET

    pre_fix_violation = bet_size * AGGRESSIVE_MULTIPLIER
    assert abs(pre_fix_violation - 3.45) < 1e-9  # would have slipped past HARD_MAX_BET undetected

    fixed = apply_adaptive_bet_multiplier(
        bet_size, AGGRESSIVE_MULTIPLIER, capital, MAX_POSITION_PCT,
    )
    assert fixed == 3.0


def test_non_aggressive_multiplier_unaffected():
    # DEFENSIVE (0.6) / SURVIVAL (0.3) multipliers only ever shrink bet_size —
    # the fix must not change their (already-safe) behavior.
    capital = 100.0
    bet_size = 4.0
    for shrink_mult in (0.6, 0.3, 1.0):
        adjusted = apply_adaptive_bet_multiplier(bet_size, shrink_mult, capital, MAX_POSITION_PCT)
        assert adjusted == bet_size * shrink_mult
