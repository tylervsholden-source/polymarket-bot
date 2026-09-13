"""
Regression guard for CLAUDE.md kural 1: "Max tek pozisyon: portföyün %20'si
(Kelly override yapmaz)".

commit 9b5fd52 introduced a survival-mode min/max bet band (capital < $20 →
up to 80% of capital, floor 40% of capital) that could push bet_size above
the 20% cap even though Kelly's own signal.size is already capped at
max_position_pct — e.g. capital=$10 floored bet_size to $3 (30%), and
capital=$5 floored it to $2 (40%). This silently overrode Kelly upward,
exactly what the non-negotiable rule forbids. Guard against it recurring.
"""
from agents.orchestrator import compute_bet_size

MAX_POSITION_PCT = 0.20


def test_survival_mode_floor_never_exceeds_20_percent():
    for capital in (5.0, 8.0, 10.0, 15.0, 19.0):
        bet_size, _ = compute_bet_size(
            capital=capital,
            signal_size=0.0,  # worst case: Kelly recommends nothing, only the floor applies
            min_bet=3.0,
            max_bet=8.0,
            max_position_pct=MAX_POSITION_PCT,
        )
        assert bet_size <= capital * MAX_POSITION_PCT + 1e-9, (
            f"capital=${capital}: bet_size=${bet_size} exceeds 20% cap "
            f"(${capital * MAX_POSITION_PCT})"
        )


def test_survival_mode_ceiling_never_exceeds_20_percent():
    for capital in (5.0, 8.0, 10.0, 15.0, 19.0):
        bet_size, _ = compute_bet_size(
            capital=capital,
            signal_size=999.0,  # Kelly (hypothetically) recommends far more than the cap
            min_bet=3.0,
            max_bet=8.0,
            max_position_pct=MAX_POSITION_PCT,
        )
        assert bet_size <= capital * MAX_POSITION_PCT + 1e-9


def test_normal_capital_unaffected_by_position_cap():
    # >=$20 branch uses a 12% ceiling, already under 20% — cap should be a no-op.
    bet_size, effective_min = compute_bet_size(
        capital=71.0,
        signal_size=50.0,
        min_bet=3.0,
        max_bet=8.0,
        max_position_pct=MAX_POSITION_PCT,
    )
    assert bet_size == 4.0  # hard_max_bet (default $4) still binds, unrelated to this fix
    assert effective_min == 2.84  # 71 * 4% floor, well under the 20% cap (14.2)


def test_very_low_capital_below_one_dollar_signals_no_trade():
    # capital=$3 → 20% cap = $0.60, below the practical $1 minimum: caller
    # must treat this as "too low to trade" rather than trading over-cap.
    bet_size, _ = compute_bet_size(
        capital=3.0,
        signal_size=5.0,
        min_bet=3.0,
        max_bet=8.0,
        max_position_pct=MAX_POSITION_PCT,
    )
    assert bet_size < 1.0
