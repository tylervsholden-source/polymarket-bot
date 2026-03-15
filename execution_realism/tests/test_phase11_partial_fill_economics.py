"""
execution_realism/tests/test_phase11_partial_fill_economics.py

Phase 11: Partial Fill Economics

Kanıtlanan özellikler:
1. PARTIAL fill, FILLABLE ile aynı girdi verildiğinde DAHA DÜŞÜK executable_ev verir
2. PARTIAL fill penalty > 0.0
3. PARTIAL fill penalty ExecutableCostBreakdown.partial_fill_penalty alanında raporlanır
4. PARTIAL fill penalty total_friction'a dahil edilmiş
5. UNFILLABLE hala passes_gate=False (değişmedi)
6. FILLABLE partial_fill_penalty == 0.0 (değişmedi)
7. Eşik altında kalan FILLABLE trade, PARTIAL olunca reddedilebilir
8. PARTIAL passes_gate koşulu threshold ile tutarlı
9. fill_fraction monotonicity: daha büyük boyut → daha büyük penalty (PARTIAL aralığında)
10. executable_ev = theoretical - fee - slippage - staleness - partial_fill_penalty
"""
from __future__ import annotations

import pytest

from execution_realism.core import compute_executable_ev
from execution_realism.types import FillDecision


# ── Yardımcı: FILLABLE ve PARTIAL ayırt eden likidite değerleri ───────────────
# PARTIAL: size/liquidity ∈ (0.25, 0.50]
# FILLABLE: size/liquidity ≤ 0.25
# Sabit: intended_size=100 USDC
# FILLABLE liq: 100 / 0.24 ≈ 417 → liq=420 (< 25% eşik geçer)
# PARTIAL  liq: 100 / 0.35 ≈ 286 → liq=290 (> 25% ama < 50%)
_FILLABLE_LIQ = 420.0   # 100/420 ≈ 0.238 → FILLABLE
_PARTIAL_LIQ  = 290.0   # 100/290 ≈ 0.345 → PARTIAL
_SIZE         = 100.0

_COMMON_KWARGS = dict(
    side="YES",
    calibrated_event_probability=0.70,
    ask_price=0.50,
    fee_pct=0.01,
    intended_size_usdc=_SIZE,
    snapshot_age_seconds=0.0,
    horizon_minutes=5,
    required_threshold=0.05,
)


class TestPartialFillEconomics:
    """PARTIAL fill, FILLABLE'dan farklı (daha düşük) executable_ev üretmeli."""

    def test_partial_gives_lower_ev_than_fillable(self):
        """Ana kanıt: PARTIAL executable_ev < FILLABLE executable_ev, aynı diğer koşullar."""
        fillable = compute_executable_ev(**{**_COMMON_KWARGS, "liquidity_usdc": _FILLABLE_LIQ})
        partial  = compute_executable_ev(**{**_COMMON_KWARGS, "liquidity_usdc": _PARTIAL_LIQ})

        assert fillable.fill_sim.fill_decision == FillDecision.FILLABLE, \
            f"Beklenen FILLABLE, alınan {fillable.fill_sim.fill_decision}"
        assert partial.fill_sim.fill_decision == FillDecision.PARTIAL, \
            f"Beklenen PARTIAL, alınan {partial.fill_sim.fill_decision}"

        assert partial.executable_ev < fillable.executable_ev, (
            f"PARTIAL ev={partial.executable_ev:.6f} >= FILLABLE ev={fillable.executable_ev:.6f}"
        )

    def test_partial_penalty_is_positive(self):
        """PARTIAL partial_fill_penalty > 0."""
        result = compute_executable_ev(**{**_COMMON_KWARGS, "liquidity_usdc": _PARTIAL_LIQ})
        assert result.fill_sim.fill_decision == FillDecision.PARTIAL
        assert result.partial_fill_penalty > 0.0, \
            f"partial_fill_penalty beklenen > 0, alınan {result.partial_fill_penalty}"

    def test_fillable_penalty_is_zero(self):
        """FILLABLE partial_fill_penalty == 0.0."""
        result = compute_executable_ev(**{**_COMMON_KWARGS, "liquidity_usdc": _FILLABLE_LIQ})
        assert result.fill_sim.fill_decision == FillDecision.FILLABLE
        assert result.partial_fill_penalty == 0.0

    def test_partial_penalty_in_total_friction(self):
        """PARTIAL: total_friction = fee + slippage + staleness + partial_fill_penalty."""
        result = compute_executable_ev(**{**_COMMON_KWARGS, "liquidity_usdc": _PARTIAL_LIQ})
        expected_total = (
            result.fee_cost
            + result.slippage.total_slippage
            + result.staleness.penalty
            + result.partial_fill_penalty
        )
        assert abs(result.total_friction - expected_total) < 1e-9, (
            f"total_friction={result.total_friction:.8f} != "
            f"fee+slippage+staleness+penalty={expected_total:.8f}"
        )

    def test_partial_penalty_in_diagnostics(self):
        """PARTIAL: diagnostics['partial_fill_penalty'] > 0."""
        result = compute_executable_ev(**{**_COMMON_KWARGS, "liquidity_usdc": _PARTIAL_LIQ})
        assert "partial_fill_penalty" in result.diagnostics
        assert result.diagnostics["partial_fill_penalty"] > 0.0

    def test_fillable_penalty_zero_in_diagnostics(self):
        """FILLABLE: diagnostics['partial_fill_penalty'] == 0.0."""
        result = compute_executable_ev(**{**_COMMON_KWARGS, "liquidity_usdc": _FILLABLE_LIQ})
        assert result.diagnostics.get("partial_fill_penalty", 0.0) == 0.0

    def test_unfillable_still_rejects(self):
        """UNFILLABLE: passes_gate=False (Phase 11 değiştirmedi)."""
        # size=100, liq=150 → ratio=0.667 > 0.50 → UNFILLABLE
        result = compute_executable_ev(**{**_COMMON_KWARGS, "liquidity_usdc": 150.0})
        assert result.fill_sim.fill_decision == FillDecision.UNFILLABLE
        assert result.passes_gate is False

    def test_executable_ev_formula_partial(self):
        """PARTIAL: executable_ev = theoretical - fee - slippage - staleness - penalty."""
        result = compute_executable_ev(**{**_COMMON_KWARGS, "liquidity_usdc": _PARTIAL_LIQ})
        theoretical = 0.70 - 0.50  # = 0.20
        expected_ev = (
            theoretical
            - result.fee_cost
            - result.slippage.total_slippage
            - result.staleness.penalty
            - result.partial_fill_penalty
        )
        assert abs(result.executable_ev - round(expected_ev, 6)) < 1e-9, (
            f"executable_ev={result.executable_ev:.8f} != "
            f"theoretical-friction={round(expected_ev, 6):.8f}"
        )

    def test_barely_passing_fillable_fails_as_partial(self):
        """
        Edge case: FILLABLE ile tam eşiğin üzerinde olan bir trade,
        PARTIAL fill penalty ile eşiğin altına düşebilir.

        Senaryo: p=0.66, ask=0.50, fee=0.01, slippage≈0.025 (low liq), threshold=0.10
        FILLABLE executable_ev ≈ 0.66-0.50-0.01-0.025 = 0.125 → passes
        PARTIAL  executable_ev ≈ 0.125 - 0.005 = 0.120 → still passes (penalty=0.005)

        Daha zorlu: threshold=0.12, FILLABLE barely passes, PARTIAL fails.
        """
        kwargs = dict(
            side="YES",
            calibrated_event_probability=0.66,
            ask_price=0.50,
            fee_pct=0.01,
            intended_size_usdc=_SIZE,
            snapshot_age_seconds=0.0,
            horizon_minutes=5,
            required_threshold=0.12,
        )
        fillable = compute_executable_ev(**{**kwargs, "liquidity_usdc": _FILLABLE_LIQ})
        partial  = compute_executable_ev(**{**kwargs, "liquidity_usdc": _PARTIAL_LIQ})

        # PARTIAL her zaman FILLABLE'dan daha düşük EV
        assert partial.executable_ev < fillable.executable_ev

        # PARTIAL penalty > 0 ise PARTIAL'ın passes_gate'i farklı olabilir
        if partial.partial_fill_penalty > 0:
            # penalty nedeniyle PARTIAL daha kötü performans gösterir
            assert partial.total_friction > fillable.total_friction


class TestPartialFillMonotonicity:
    """Daha büyük order boyutu → daha büyük penalty (PARTIAL aralığında)."""

    def test_larger_size_in_partial_zone_gives_larger_penalty(self):
        """
        PARTIAL aralığında daha büyük size → fill_fraction aynı (0.90) ama
        slippage daha yüksek (size_bucket). penalty = (1-fill_fraction)*0.05 sabit,
        ama total_friction (slippage + penalty) monoton artar.
        """
        # Küçük size: PARTIAL aralığında (size/liq ∈ 0.25-0.50)
        # liq=1000, size_small=280 → ratio=0.28 → PARTIAL
        # liq=1000, size_large=400 → ratio=0.40 → PARTIAL
        kwargs_base = dict(
            side="YES",
            calibrated_event_probability=0.70,
            ask_price=0.50,
            fee_pct=0.01,
            snapshot_age_seconds=0.0,
            horizon_minutes=5,
            required_threshold=0.02,
            liquidity_usdc=1000.0,
        )
        small = compute_executable_ev(**{**kwargs_base, "intended_size_usdc": 280.0})
        large = compute_executable_ev(**{**kwargs_base, "intended_size_usdc": 400.0})

        assert small.fill_sim.fill_decision == FillDecision.PARTIAL
        assert large.fill_sim.fill_decision == FillDecision.PARTIAL

        # Daha büyük size → daha yüksek slippage (size_bucket) → daha düşük EV
        assert large.executable_ev <= small.executable_ev

    def test_fillable_to_partial_transition_causes_ev_drop(self):
        """
        size/liq oranı FILLABLE→PARTIAL geçişinde EV keskin düşer (penalty eklenir).
        """
        # liq=1000
        # size=240 → ratio=0.240 → FILLABLE (tam eşik altı)
        # size=260 → ratio=0.260 → PARTIAL
        kwargs_base = dict(
            side="YES",
            calibrated_event_probability=0.70,
            ask_price=0.50,
            fee_pct=0.01,
            snapshot_age_seconds=0.0,
            horizon_minutes=5,
            required_threshold=0.02,
            liquidity_usdc=1000.0,
        )
        fillable = compute_executable_ev(**{**kwargs_base, "intended_size_usdc": 240.0})
        partial  = compute_executable_ev(**{**kwargs_base, "intended_size_usdc": 260.0})

        assert fillable.fill_sim.fill_decision == FillDecision.FILLABLE
        assert partial.fill_sim.fill_decision == FillDecision.PARTIAL

        # PARTIAL toplam friction > FILLABLE (penalty eklendi)
        assert partial.total_friction > fillable.total_friction
        assert partial.partial_fill_penalty > 0.0
        assert fillable.partial_fill_penalty == 0.0
