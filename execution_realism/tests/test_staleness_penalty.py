"""
execution_realism/tests/test_staleness_penalty.py

Tests for compute_staleness_penalty: horizon-aware staleness zones and penalties.
"""
from __future__ import annotations

import pytest

from execution_realism.staleness_penalty import compute_staleness_penalty
from execution_realism.types import StalenessZone


class TestHorizon5m:
    """5-minute horizon: fresh ≤ 30s, aging 31-90s, stale 91-180s, expired > 180s."""

    def test_fresh(self):
        result = compute_staleness_penalty(age_seconds=10.0, horizon_minutes=5)
        assert result.zone == StalenessZone.FRESH
        assert result.penalty == 0.0
        assert result.should_reject is False

    def test_aging(self):
        result = compute_staleness_penalty(age_seconds=60.0, horizon_minutes=5)
        assert result.zone == StalenessZone.AGING
        assert result.penalty == 0.005
        assert result.should_reject is False

    def test_stale(self):
        result = compute_staleness_penalty(age_seconds=130.0, horizon_minutes=5)
        assert result.zone == StalenessZone.STALE
        assert result.penalty == 0.015
        assert result.should_reject is False

    def test_expired(self):
        result = compute_staleness_penalty(age_seconds=200.0, horizon_minutes=5)
        assert result.zone == StalenessZone.EXPIRED
        assert result.should_reject is True

    def test_at_boundary_30s_is_fresh(self):
        """age=30s exactly → ≤ 30 → FRESH"""
        result = compute_staleness_penalty(age_seconds=30.0, horizon_minutes=5)
        assert result.zone == StalenessZone.FRESH

    def test_just_over_30s_is_aging(self):
        """age=31s → > 30 → AGING"""
        result = compute_staleness_penalty(age_seconds=31.0, horizon_minutes=5)
        assert result.zone == StalenessZone.AGING


class TestHorizon15m:
    """15-minute horizon: fresh ≤ 60s, aging 61-180s, stale 181-300s, expired > 300s."""

    def test_fresh(self):
        result = compute_staleness_penalty(age_seconds=30.0, horizon_minutes=15)
        assert result.zone == StalenessZone.FRESH
        assert result.penalty == 0.0
        assert result.should_reject is False

    def test_aging(self):
        result = compute_staleness_penalty(age_seconds=120.0, horizon_minutes=15)
        assert result.zone == StalenessZone.AGING
        assert result.penalty == 0.003
        assert result.should_reject is False

    def test_stale(self):
        result = compute_staleness_penalty(age_seconds=250.0, horizon_minutes=15)
        assert result.zone == StalenessZone.STALE
        assert result.penalty == 0.010
        assert result.should_reject is False

    def test_expired(self):
        result = compute_staleness_penalty(age_seconds=350.0, horizon_minutes=15)
        assert result.zone == StalenessZone.EXPIRED
        assert result.should_reject is True

    def test_at_boundary_60s_is_fresh(self):
        """age=60s exactly → ≤ 60 → FRESH"""
        result = compute_staleness_penalty(age_seconds=60.0, horizon_minutes=15)
        assert result.zone == StalenessZone.FRESH

    def test_just_over_60s_is_aging(self):
        """age=61s → > 60 → AGING"""
        result = compute_staleness_penalty(age_seconds=61.0, horizon_minutes=15)
        assert result.zone == StalenessZone.AGING


class TestHorizonTighterComparison:
    """5m is tighter than 15m: same age → different zones."""

    def test_age_100s_5m_stale_15m_aging(self):
        """age=100s: 5m horizon → STALE (100>90), 15m horizon → AGING (100≤180)"""
        result_5m = compute_staleness_penalty(age_seconds=100.0, horizon_minutes=5)
        result_15m = compute_staleness_penalty(age_seconds=100.0, horizon_minutes=15)
        assert result_5m.zone == StalenessZone.STALE
        assert result_15m.zone == StalenessZone.AGING

    def test_age_30s_5m_fresh_15m_fresh(self):
        """age=30s: both fresh"""
        result_5m = compute_staleness_penalty(age_seconds=30.0, horizon_minutes=5)
        result_15m = compute_staleness_penalty(age_seconds=30.0, horizon_minutes=15)
        assert result_5m.zone == StalenessZone.FRESH
        assert result_15m.zone == StalenessZone.FRESH

    def test_age_200s_5m_expired_15m_stale(self):
        """age=200s: 5m → EXPIRED (200>180), 15m → STALE (181≤200≤300)"""
        result_5m = compute_staleness_penalty(age_seconds=200.0, horizon_minutes=5)
        result_15m = compute_staleness_penalty(age_seconds=200.0, horizon_minutes=15)
        assert result_5m.zone == StalenessZone.EXPIRED
        assert result_15m.zone == StalenessZone.STALE
        assert result_5m.should_reject is True
        assert result_15m.should_reject is False


class TestUnsupportedHorizon:
    """Unsupported horizon → EXPIRED with should_reject=True."""

    def test_unsupported_horizon_10(self):
        result = compute_staleness_penalty(age_seconds=10.0, horizon_minutes=10)
        assert result.zone == StalenessZone.EXPIRED
        assert result.should_reject is True

    def test_unsupported_horizon_30(self):
        result = compute_staleness_penalty(age_seconds=10.0, horizon_minutes=30)
        assert result.should_reject is True

    def test_unsupported_horizon_penalty(self):
        result = compute_staleness_penalty(age_seconds=10.0, horizon_minutes=60)
        assert result.penalty == 0.030


class TestAgeTracking:
    """age_seconds is recorded correctly in result."""

    def test_age_recorded(self):
        result = compute_staleness_penalty(age_seconds=42.5, horizon_minutes=15)
        assert result.age_seconds == 42.5
