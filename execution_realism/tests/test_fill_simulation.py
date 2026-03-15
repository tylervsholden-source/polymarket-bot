"""
execution_realism/tests/test_fill_simulation.py

Tests for simulate_fill: fill decision, fill fraction, edge cases.
"""
from __future__ import annotations

import pytest

from execution_realism.fill_simulator import simulate_fill
from execution_realism.types import FillDecision


class TestFillDecisions:

    def test_small_size_fillable(self):
        """size=$10, liquidity=$5000 → ratio=0.2% → FILLABLE"""
        result = simulate_fill(ask_price=0.44, intended_size_usdc=10.0, liquidity_usdc=5000.0)
        assert result.fill_decision == FillDecision.FILLABLE

    def test_medium_size_partial(self):
        """size=$1500, liquidity=$5000 → ratio=30% > 25% → PARTIAL"""
        result = simulate_fill(ask_price=0.44, intended_size_usdc=1500.0, liquidity_usdc=5000.0)
        assert result.fill_decision == FillDecision.PARTIAL

    def test_large_size_unfillable(self):
        """size=$3000, liquidity=$5000 → ratio=60% > 50% → UNFILLABLE"""
        result = simulate_fill(ask_price=0.44, intended_size_usdc=3000.0, liquidity_usdc=5000.0)
        assert result.fill_decision == FillDecision.UNFILLABLE

    def test_zero_size_fillable(self):
        """size=$0, liquidity=$5000 → ratio=0% → FILLABLE"""
        result = simulate_fill(ask_price=0.44, intended_size_usdc=0.0, liquidity_usdc=5000.0)
        assert result.fill_decision == FillDecision.FILLABLE

    def test_zero_liquidity_unfillable(self):
        """liquidity=$0 → UNFILLABLE regardless of size"""
        result = simulate_fill(ask_price=0.44, intended_size_usdc=10.0, liquidity_usdc=0.0)
        assert result.fill_decision == FillDecision.UNFILLABLE


class TestFillFractions:

    def test_fillable_fraction_is_one(self):
        """FILLABLE → expected_fill_fraction = 1.0"""
        result = simulate_fill(ask_price=0.44, intended_size_usdc=10.0, liquidity_usdc=5000.0)
        assert result.expected_fill_fraction == 1.0

    def test_partial_fraction_is_090(self):
        """PARTIAL → expected_fill_fraction = 0.90"""
        result = simulate_fill(ask_price=0.44, intended_size_usdc=1500.0, liquidity_usdc=5000.0)
        assert result.expected_fill_fraction == 0.90

    def test_unfillable_fraction_is_zero(self):
        """UNFILLABLE → expected_fill_fraction = 0.0"""
        result = simulate_fill(ask_price=0.44, intended_size_usdc=3000.0, liquidity_usdc=5000.0)
        assert result.expected_fill_fraction == 0.0

    def test_zero_liquidity_fraction_is_zero(self):
        result = simulate_fill(ask_price=0.44, intended_size_usdc=10.0, liquidity_usdc=0.0)
        assert result.expected_fill_fraction == 0.0


class TestEntryPrice:

    def test_entry_price_is_ask(self):
        """Fill always happens at ask price (no price improvement)."""
        result = simulate_fill(ask_price=0.65, intended_size_usdc=10.0, liquidity_usdc=5000.0)
        assert result.entry_price == 0.65

    def test_partial_entry_price_is_ask(self):
        result = simulate_fill(ask_price=0.65, intended_size_usdc=1500.0, liquidity_usdc=5000.0)
        assert result.entry_price == 0.65

    def test_unfillable_entry_price_is_ask(self):
        result = simulate_fill(ask_price=0.65, intended_size_usdc=3000.0, liquidity_usdc=5000.0)
        assert result.entry_price == 0.65


class TestBoundaryRatios:

    def test_exactly_25pct_is_fillable(self):
        """size/liquidity = exactly 25% → not > 0.25 → FILLABLE"""
        result = simulate_fill(ask_price=0.44, intended_size_usdc=1250.0, liquidity_usdc=5000.0)
        assert result.fill_decision == FillDecision.FILLABLE

    def test_just_over_25pct_is_partial(self):
        """size/liquidity = 25.1% > 25% → PARTIAL"""
        result = simulate_fill(ask_price=0.44, intended_size_usdc=1255.0, liquidity_usdc=5000.0)
        assert result.fill_decision == FillDecision.PARTIAL

    def test_exactly_50pct_is_partial(self):
        """size/liquidity = exactly 50% → not > 0.50 → PARTIAL"""
        result = simulate_fill(ask_price=0.44, intended_size_usdc=2500.0, liquidity_usdc=5000.0)
        assert result.fill_decision == FillDecision.PARTIAL

    def test_just_over_50pct_is_unfillable(self):
        """size/liquidity = 50.1% > 50% → UNFILLABLE"""
        result = simulate_fill(ask_price=0.44, intended_size_usdc=2505.0, liquidity_usdc=5000.0)
        assert result.fill_decision == FillDecision.UNFILLABLE
