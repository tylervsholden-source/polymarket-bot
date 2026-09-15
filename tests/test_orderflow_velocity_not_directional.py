"""
Regression test: OrderFlowAgent's composite bias score must not let trade
*count* velocity vote on direction — it has none.

Bug: calc_trade_velocity() (agents/subagents/orderflow_agent.py) measures
raw trade-count change over a window with zero buy/sell information (unlike
calc_cvd(), which does check is_buy per trade). compute_bias_score() folded
it into the weighted directional bias sum anyway, with a "positive =
bullish" sign and weight 3 out of a total of 43. A burst of trading
activity — including a wave of panic *selling* — raises the trade count
just as much as a buying frenzy, so this let velocity noise dilute or flip
a real bearish OBI/CVD reading exactly when the market was moving hardest.

Concrete scenario: OBI=-0.7 and CVD=-0.8 (heavy one-sided selling, both in
the book and in the executed tape) should read clearly BEARISH. Real
composite bias with all other indicators neutral:
    (-70*8 + -80*7) / 40 = -1120/40 = -28.0  → BEARISH (bias < -20)
With the old velocity term included (a count spike -> velocity score
clamped to +100, weight 3, old TOTAL_WEIGHT 43):
    (-70*8 + -80*7 + 100*3) / 43 = -820/43 = -19.1  → NEUTRAL

This directly affects OrderFlowData.is_bearish/is_bullish/agrees_with(),
consumed by signal_agent_v2._compute_confluence()'s orderflow-alignment
vote and _detect_risk_flags()'s ORDERFLOW_OPPOSITION check — so a real
bearish crash could silently fail to register as bearish because trade
count also spiked during the panic.
"""
from __future__ import annotations

from agents.subagents.orderflow_agent import compute_bias_score, BIAS_WEIGHTS


def test_velocity_excluded_from_bias_weights():
    assert "velocity" not in BIAS_WEIGHTS, (
        "trade-count velocity has no buy/sell direction and must not carry "
        "a directional vote in the composite bias score"
    )


def test_heavy_selling_reads_bearish_despite_a_trade_count_spike():
    # OBI=-0.7, CVD=-0.8: heavy one-sided selling in both the book and tape.
    # velocity=4.0 mimics a real panic-driven spike in trade count (e.g.
    # 150 trades in the last 60s vs 30 in the prior 60s).
    bias, label = compute_bias_score(
        obi=-0.7,
        cvd=-0.8,
        buy_wall=0.0,
        sell_wall=0.0,
        vwap_dev=0.0,
        ema_cross=0.0,
        ha_streak=0,
        velocity=4.0,
    )

    assert label == "BEARISH", (
        f"a genuine panic-sell (OBI=-0.7, CVD=-0.8) must read BEARISH "
        f"regardless of a concurrent trade-count spike; got label={label}, "
        f"bias={bias}"
    )
    assert bias < -20


def test_bias_unaffected_by_velocity_magnitude():
    # Same book/tape reading, wildly different velocity values — the
    # composite bias must not move at all now that velocity carries no
    # directional weight.
    kwargs = dict(
        obi=-0.7, cvd=-0.8, buy_wall=0.0, sell_wall=0.0,
        vwap_dev=0.0, ema_cross=0.0, ha_streak=0,
    )
    bias_no_spike, _ = compute_bias_score(velocity=0.0, **kwargs)
    bias_big_spike, _ = compute_bias_score(velocity=4.0, **kwargs)

    assert bias_no_spike == bias_big_spike
