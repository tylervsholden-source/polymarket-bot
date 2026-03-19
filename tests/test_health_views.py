"""
tests/test_health_views.py

Tests for operator_layer/health.py.

Covers:
- Alert aggregation from journal records
- Blocker aggregation
- Journal integrity visibility
- Cycle state derivation from control/status data
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone

from operator_layer.health import (
    JOURNAL_BAD_LINE_WARN,
    PARTIAL_FILL_WARN,
    STALE_PRICING_WARN,
    SUSPICIOUS_UNDERROUND_WARN,
    build_health_state,
)
from operator_layer.types import HealthAlert, HealthState


# ── Helpers ────────────────────────────────────────────────────────────────────

def _rec(decision="REJECT", profile="live", sanity_notes=None,
         snapshot_age=5.0, fill_fraction=None, evidence_source="live_shadow"):
    return {
        "policy_profile": profile,
        "evidence_source": evidence_source,
        "decision": {
            "decision": decision,
            "pricing_sanity_notes": sanity_notes,
            "fill_fraction": fill_fraction,
        },
        "pricing": {"snapshot_age_seconds": snapshot_age},
    }


def _integrity(total=100, bad=0):
    bad_frac = bad / total if total > 0 else 0.0
    return {
        "total_lines": total,
        "parsed_ok": total - bad,
        "bad_json_count": bad,
        "bad_line_fraction": bad_frac,
        "files_checked": 1,
    }


def _status(running=True, cycle=42, updated="2026-03-15T12:00:00Z"):
    return {"running": running, "cycle": cycle, "updated": updated}


def _control(live=False, sim=True):
    return {"live_trading": live, "simulation_running": sim}


# ── Journal integrity ──────────────────────────────────────────────────────────

class TestJournalIntegrity:
    def test_clean_journal_is_healthy(self):
        h = build_health_state(_integrity(100, 0), [], _status(), _control())
        assert h.journal_healthy is True
        assert h.journal_bad_line_fraction == 0.0
        assert h.journal_total_lines == 100

    def test_high_bad_fraction_triggers_alert(self):
        h = build_health_state(_integrity(100, 5), [], _status(), _control())
        assert h.journal_healthy is False
        journal_alerts = [a for a in h.drift_alerts + h.pricing_sanity_alerts
                          if a.category == "JOURNAL"]
        assert len(journal_alerts) > 0

    def test_bad_line_fraction_at_threshold(self):
        """Exactly at threshold (JOURNAL_BAD_LINE_WARN) should be healthy."""
        bad = int(100 * JOURNAL_BAD_LINE_WARN)
        h = build_health_state(_integrity(100, bad), [], _status(), _control())
        # at threshold: 2% bad → healthy (<=)
        assert h.journal_healthy is True

    def test_blocker_set_when_journal_unhealthy(self):
        h = build_health_state(_integrity(100, 10), [], _status(), _control())
        assert h.blocker_active is True
        assert "JOURNAL_INTEGRITY" in h.blocker_reasons

    def test_no_journal_yet(self):
        h = build_health_state(_integrity(0, 0), [], _status(), _control())
        assert h.journal_healthy is True  # 0 lines → bad_frac=0 → healthy
        assert h.journal_total_lines == 0


# ── Pricing sanity rates ───────────────────────────────────────────────────────

class TestPricingRates:
    def test_suspicious_underround_alert_fires(self):
        recs = [_rec(sanity_notes="underround") for _ in range(25)] + \
               [_rec() for _ in range(75)]
        h = build_health_state(_integrity(), recs, _status(), _control())
        assert h.suspicious_underround_rate > SUSPICIOUS_UNDERROUND_WARN
        assert len(h.pricing_sanity_alerts) > 0

    def test_no_alert_when_rate_low(self):
        recs = [_rec(sanity_notes="underround") for _ in range(5)] + \
               [_rec() for _ in range(95)]
        h = build_health_state(_integrity(), recs, _status(), _control())
        assert h.suspicious_underround_rate <= SUSPICIOUS_UNDERROUND_WARN

    def test_stale_pricing_alert_fires(self):
        recs = [_rec(snapshot_age=120.0) for _ in range(20)] + \
               [_rec(snapshot_age=5.0) for _ in range(80)]
        h = build_health_state(_integrity(), recs, _status(), _control())
        assert h.stale_pricing_rate > STALE_PRICING_WARN
        stale_alerts = [a for a in h.pricing_sanity_alerts if "stale" in a.message.lower()]
        assert len(stale_alerts) > 0

    def test_partial_fill_alert_fires(self):
        exec_recs = [_rec(decision="EXECUTE_YES", fill_fraction=0.5) for _ in range(40)] + \
                    [_rec(decision="EXECUTE_YES", fill_fraction=1.0) for _ in range(60)]
        h = build_health_state(_integrity(), exec_recs, _status(), _control())
        assert h.partial_fill_rate > PARTIAL_FILL_WARN

    def test_rates_none_when_no_records(self):
        h = build_health_state(_integrity(0, 0), [], _status(), _control())
        assert h.suspicious_underround_rate is None
        assert h.stale_pricing_rate is None
        assert h.partial_fill_rate is None


# ── Cycle state ────────────────────────────────────────────────────────────────

class TestCycleState:
    def test_running_when_simulation_on(self):
        h = build_health_state(_integrity(), [], _status(running=False), _control(sim=True))
        assert h.cycle_running is True

    def test_running_when_live_on(self):
        h = build_health_state(_integrity(), [], _status(running=False), _control(live=True, sim=False))
        assert h.cycle_running is True

    def test_stopped_when_both_off(self):
        h = build_health_state(_integrity(), [], _status(running=False), _control(live=False, sim=False))
        assert h.cycle_running is False

    def test_cycle_count_from_status(self):
        h = build_health_state(_integrity(), [], _status(cycle=99), _control())
        assert h.cycles_completed == 99

    def test_last_cycle_at_from_status(self):
        h = build_health_state(_integrity(), [], _status(updated="2026-03-15T10:00:00Z"), _control())
        assert h.last_cycle_at == "2026-03-15T10:00:00Z"


# ── Synthetic records excluded ─────────────────────────────────────────────────

class TestSyntheticExclusion:
    def test_synthetic_records_not_counted(self):
        """Synthetic records should not affect health rate computations."""
        recs = [_rec(sanity_notes="underround", evidence_source="synthetic") for _ in range(100)]
        h = build_health_state(_integrity(), recs, _status(), _control())
        # No live_shadow records → rates should be None or 0
        assert h.suspicious_underround_rate is None or h.suspicious_underround_rate == 0.0
