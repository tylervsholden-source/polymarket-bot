"""
calibration/edge_estimator.py

Kalibre edilmiş olasılık + Polymarket fiyatı → EdgeEstimate.

Matematik:
    gross_EV             = p - q
    net_EV               = p - q - c          (fee sonrası)
    execution_adjusted   = p - q - c - s      (slippage sonrası — gerçek maliyet)
    expected_edge        = execution_adjusted  (karar metriği)

    p: calibrated_event_probability (P(bu taraf resolve olur))
    q: ask_yes veya ask_no (giriş fiyatı)
    c: assumed_taker_fee_pct (tek yön maliyet)
    s: assumed_slippage_pct (gerçekçi dolum maliyeti)

passes_edge_gate = execution_adjusted_ev >= required_edge_threshold

Tüm fiyatlar [0, 1] normalize birimidir.
YES tarafı YES fiyatlarını kullanır; NO tarafı NO fiyatlarını.
Çapraz karıştırma (YES conf vs NO price) yoktur.
"""
from __future__ import annotations

from calibration.types import (
    CalibratedSignal,
    EdgeEstimate,
    MarketPricingSnapshot,
    CalibrationConfig,
    DEFAULT_CAL_CONFIG,
)


def estimate_yes_edge(
    cal_signal: CalibratedSignal,
    pricing: MarketPricingSnapshot,
    config: CalibrationConfig = DEFAULT_CAL_CONFIG,
) -> EdgeEstimate:
    """
    YES tarafı için edge tahmini.

    Giriş fiyatı  : pricing.ask_yes
    Olasılık      : cal_signal.effective_yes_prob
    """
    p = cal_signal.effective_yes_prob
    q = pricing.ask_yes
    c = config.assumed_taker_fee_pct
    s = (
        config.assumed_slippage_yes_pct
        if config.assumed_slippage_yes_pct is not None
        else config.assumed_slippage_pct
    )

    gross_ev             = p - q
    net_ev               = gross_ev - c
    execution_adjusted   = net_ev - s
    edge                 = execution_adjusted  # karar metriği: slippage dahil
    passes               = edge >= config.min_execution_adjusted_edge

    return EdgeEstimate(
        side_considered="YES",
        calibrated_event_probability=p,
        market_entry_price=q,
        assumed_cost=c,
        gross_expected_value=round(gross_ev, 6),
        net_expected_value=round(net_ev, 6),
        execution_adjusted_ev=round(execution_adjusted, 6),
        expected_edge=round(edge, 6),
        required_edge_threshold=config.min_execution_adjusted_edge,
        passes_edge_gate=passes,
        diagnostics={
            "ask_yes":    q,
            "bid_yes":    pricing.bid_yes,
            "spread_yes": pricing.spread_yes,
            "slippage":   s,
            "method":     cal_signal.calibration_method.value,
            "quality":    cal_signal.calibration_quality.value,
        },
    )


def estimate_no_edge(
    cal_signal: CalibratedSignal,
    pricing: MarketPricingSnapshot,
    config: CalibrationConfig = DEFAULT_CAL_CONFIG,
) -> EdgeEstimate:
    """
    NO tarafı için edge tahmini.

    Giriş fiyatı  : pricing.ask_no
    Olasılık      : cal_signal.effective_no_prob
    """
    p = cal_signal.effective_no_prob
    q = pricing.ask_no
    c = config.assumed_taker_fee_pct
    s = (
        config.assumed_slippage_no_pct
        if config.assumed_slippage_no_pct is not None
        else config.assumed_slippage_pct
    )

    gross_ev             = p - q
    net_ev               = gross_ev - c
    execution_adjusted   = net_ev - s
    edge                 = execution_adjusted  # karar metriği: slippage dahil
    passes               = edge >= config.min_execution_adjusted_edge

    return EdgeEstimate(
        side_considered="NO",
        calibrated_event_probability=p,
        market_entry_price=q,
        assumed_cost=c,
        gross_expected_value=round(gross_ev, 6),
        net_expected_value=round(net_ev, 6),
        execution_adjusted_ev=round(execution_adjusted, 6),
        expected_edge=round(edge, 6),
        required_edge_threshold=config.min_execution_adjusted_edge,
        passes_edge_gate=passes,
        diagnostics={
            "ask_no":    q,
            "bid_no":    pricing.bid_no,
            "spread_no": pricing.spread_no,
            "slippage":  s,
            "method":    cal_signal.calibration_method.value,
            "quality":   cal_signal.calibration_quality.value,
        },
    )


def estimate_yes_edge_realistic(
    cal_signal: CalibratedSignal,
    pricing: MarketPricingSnapshot,
    config: CalibrationConfig = DEFAULT_CAL_CONFIG,
    *,
    snapshot_age_seconds: float = 0.0,
    intended_size_usdc: float = 20.0,
    policy_mode: str = "default",
) -> EdgeEstimate:
    """
    YES edge with full execution realism (slippage model + staleness penalty).
    Replaces constant slippage with realistic cost model.

    policy_mode is passed to compute_executable_ev for Phase 11 partial fill policy:
      "live"    → PARTIAL fill forces passes_gate=False
      "paper"   → PARTIAL fill applies 50bps EV penalty; may still pass
      "default" → same as "paper" (backward compatible)
    """
    from execution_realism.core import compute_executable_ev

    p = cal_signal.effective_yes_prob
    q = pricing.ask_yes
    c = config.assumed_taker_fee_pct

    breakdown = compute_executable_ev(
        side="YES",
        calibrated_event_probability=p,
        ask_price=q,
        fee_pct=c,
        intended_size_usdc=intended_size_usdc,
        liquidity_usdc=pricing.liquidity,
        snapshot_age_seconds=snapshot_age_seconds,
        horizon_minutes=cal_signal.raw.horizon_minutes,
        required_threshold=config.min_execution_adjusted_edge,
        policy_mode=policy_mode,
    )

    gross_ev = p - q
    net_ev   = gross_ev - c

    return EdgeEstimate(
        side_considered="YES",
        calibrated_event_probability=p,
        market_entry_price=q,
        assumed_cost=c,
        gross_expected_value=round(gross_ev, 6),
        net_expected_value=round(net_ev, 6),
        execution_adjusted_ev=breakdown.executable_ev,
        expected_edge=breakdown.executable_ev,
        required_edge_threshold=config.min_execution_adjusted_edge,
        passes_edge_gate=breakdown.passes_gate,
        diagnostics={
            "ask_yes": q,
            "bid_yes": pricing.bid_yes,
            "spread_yes": pricing.spread_yes,
            "slippage": breakdown.slippage.total_slippage,
            "slippage_detail": breakdown.slippage.diagnostics,
            "staleness_zone": breakdown.staleness.zone.value,
            "staleness_penalty": breakdown.staleness.penalty,
            "fill_decision": breakdown.fill_sim.fill_decision.value,
            "theoretical_hold_ev": breakdown.theoretical_hold_ev,
            "executable_ev": breakdown.executable_ev,
            "total_friction": breakdown.total_friction,
            "method": cal_signal.calibration_method.value,
            "quality": cal_signal.calibration_quality.value,
        },
        executable_cost_breakdown=breakdown,
    )


def estimate_no_edge_realistic(
    cal_signal: CalibratedSignal,
    pricing: MarketPricingSnapshot,
    config: CalibrationConfig = DEFAULT_CAL_CONFIG,
    *,
    snapshot_age_seconds: float = 0.0,
    intended_size_usdc: float = 20.0,
    policy_mode: str = "default",
) -> EdgeEstimate:
    """
    NO edge with full execution realism (slippage model + staleness penalty).
    Replaces constant slippage with realistic cost model.

    policy_mode is passed to compute_executable_ev for Phase 11 partial fill policy.
    """
    from execution_realism.core import compute_executable_ev

    p = cal_signal.effective_no_prob
    q = pricing.ask_no
    c = config.assumed_taker_fee_pct

    breakdown = compute_executable_ev(
        side="NO",
        calibrated_event_probability=p,
        ask_price=q,
        fee_pct=c,
        intended_size_usdc=intended_size_usdc,
        liquidity_usdc=pricing.liquidity,
        snapshot_age_seconds=snapshot_age_seconds,
        horizon_minutes=cal_signal.raw.horizon_minutes,
        required_threshold=config.min_execution_adjusted_edge,
        policy_mode=policy_mode,
    )

    gross_ev = p - q
    net_ev   = gross_ev - c

    return EdgeEstimate(
        side_considered="NO",
        calibrated_event_probability=p,
        market_entry_price=q,
        assumed_cost=c,
        gross_expected_value=round(gross_ev, 6),
        net_expected_value=round(net_ev, 6),
        execution_adjusted_ev=breakdown.executable_ev,
        expected_edge=breakdown.executable_ev,
        required_edge_threshold=config.min_execution_adjusted_edge,
        passes_edge_gate=breakdown.passes_gate,
        diagnostics={
            "ask_no": q,
            "bid_no": pricing.bid_no,
            "spread_no": pricing.spread_no,
            "slippage": breakdown.slippage.total_slippage,
            "slippage_detail": breakdown.slippage.diagnostics,
            "staleness_zone": breakdown.staleness.zone.value,
            "staleness_penalty": breakdown.staleness.penalty,
            "fill_decision": breakdown.fill_sim.fill_decision.value,
            "theoretical_hold_ev": breakdown.theoretical_hold_ev,
            "executable_ev": breakdown.executable_ev,
            "total_friction": breakdown.total_friction,
            "method": cal_signal.calibration_method.value,
            "quality": cal_signal.calibration_quality.value,
        },
        executable_cost_breakdown=breakdown,
    )


def estimate_both_edges(
    cal_signal: CalibratedSignal,
    pricing: MarketPricingSnapshot,
    config: CalibrationConfig = DEFAULT_CAL_CONFIG,
) -> tuple[EdgeEstimate, EdgeEstimate]:
    """
    Her iki taraf için edge tahminlerini döndürür.

    Döndürür: (yes_edge, no_edge)
    decision_policy bu ikisinden birini seçer.
    """
    yes_edge = estimate_yes_edge(cal_signal, pricing, config)
    no_edge  = estimate_no_edge(cal_signal, pricing, config)
    return yes_edge, no_edge
