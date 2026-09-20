"""SpreadModel.record()/z_score() bug: the current observation was folded into
its own mean/std baseline (see strategies/spread_model.py fix comment).

record() appended the current spread to history *before* calling z_score(),
so z_score()'s mu/sigma were computed over a sample that already included the
very point being tested. For a genuine outlier this pulls mu toward the
outlier and inflates sigma, understating the z-score and letting real
dislocations slip under find_dislocations()'s min_z=1.8 threshold — the
model becomes systematically less sensitive than its own docstring
(z = (current - historical_mean) / historical_std) describes.
"""
from strategies.spread_model import SpreadModel


def test_z_score_uses_prior_history_not_self():
    sm = SpreadModel(window=30, min_z=1.8)

    # 5 mildly noisy prior observations, spread near 0.00.
    for s in (0.005, -0.004, 0.003, -0.002, 0.001):
        sm.record("A", 0.50 + s, "B", 0.50)

    # A sudden, genuine dislocation: spread jumps to 0.10 — far outside the
    # tiny prior noise band.
    z = sm.record("A", 0.60, "B", 0.50)

    # Folding the outlier into its own mean/std (the old bug) pulls mu
    # sharply toward it and inflates sigma from the jump itself, capping
    # how large |z| can ever read. Tested against the prior-only baseline,
    # a jump this far outside the noise band must score deep in outlier
    # territory.
    assert abs(z) > 10.0, (
        f"z-score={z} — the outlier's own value leaked into the mean/std "
        "it was tested against, damping the score"
    )


def test_z_score_matches_manual_prior_only_computation():
    sm = SpreadModel(window=30, min_z=1.8)
    prior_spreads = [0.01, -0.02, 0.03, 0.00, -0.01]
    for s in prior_spreads:
        sm.record("A", 0.50 + s, "B", 0.50)

    current_price1, current_price2 = 0.70, 0.50  # spread = 0.20, way off
    z = sm.record("A", current_price1, "B", current_price2)

    mu = sum(prior_spreads) / len(prior_spreads)
    var = sum((s - mu) ** 2 for s in prior_spreads) / len(prior_spreads)
    sigma = var ** 0.5
    expected_z = round(((current_price1 - current_price2) - mu) / sigma, 3)

    assert z == expected_z
