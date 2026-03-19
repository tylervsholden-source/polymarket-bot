"""
tests/test_live_pilot_readiness.py

Phase 14: Live pilot readiness gate tests.

Covers:
- Blocker-triggered NO_GO
- Sufficient but bad metrics → NO_GO
- paper_loose optimistic but live-like weak → CONDITIONAL/NO_GO
- Clean shadow evidence → candidate for pilot (GO or CONDITIONAL)
- Tiny pilot constraints are pre-defined and populated in all reports
- format_readiness_report produces non-empty output
- Multiple FAILs → NO_GO
- Single FAIL → CONDITIONAL
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from shadow_runner.summary_metrics import ShadowSummaryMetrics, compute_summary_metrics
from shadow_runner.validation import EvidenceRequirements, check_evidence_sufficiency
from shadow_runner.readiness import (
    LIVE_PILOT_CONSTRAINTS,
    ReadinessVerdict,
    assess_readiness,
    format_readiness_report,
)

_BASE = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


def _metrics(
    profile: str = "live",
    total: int = 100,
    exec_count: int = 30,
    exec_rate: float = 0.30,
    mean_ev_haircut_pct: float = 0.20,
    underround_rate: float = 0.05,
    stale_rate: float = 0.03,
    partial_fill_rejection_rate: float = 0.05,
    rejection_rates: dict | None = None,
) -> ShadowSummaryMetrics:
    """Build a synthetic ShadowSummaryMetrics for testing."""
    if rejection_rates is None:
        rejection_rates = {"WEAK_CALIBRATION": 0.30, "NEGATIVE_EDGE": 0.35, "STALE_PRICING": 0.05}
    return ShadowSummaryMetrics(
        profile=profile,
        total_evaluated=total,
        execute_count=exec_count,
        reject_count=total - exec_count,
        execution_rate=exec_rate,
        rejection_counts={},
        rejection_rates=rejection_rates,
        mean_gross_ev=0.050,
        mean_executable_ev=0.050 * (1 - mean_ev_haircut_pct),
        mean_ev_haircut_abs=0.050 * mean_ev_haircut_pct,
        mean_ev_haircut_pct=mean_ev_haircut_pct,
        ev_haircut_sample_size=exec_count,
        full_fill_execute_count=exec_count,
        partial_fill_execute_count=0,
        partial_fill_rejected_count=int(total * partial_fill_rejection_rate),
        partial_fill_rejection_rate=partial_fill_rejection_rate,
        suspicious_underround_count=int(total * underround_rate),
        stale_pricing_count=int(total * stale_rate),
        suspicious_underround_rate=underround_rate,
        stale_pricing_rate=stale_rate,
        annotated_execute_count=0,
        assets_seen=["BTC"],
        horizons_seen=[15],
        ts_first=_BASE,
        ts_last=_BASE + timedelta(days=4),
        observation_days=4.0,
    )


def _sufficient_evidence(records_override: int = 100) -> "EvidenceSufficiencyResult":
    """Return a pre-built sufficient evidence result."""
    from shadow_runner.validation import (
        EvidenceSufficiencyResult, EvidenceRequirements
    )
    req = EvidenceRequirements(
        min_live_like_evaluated=50, min_live_like_executes=10,
        min_live_like_rejects=20, min_assets_covered=1,
        min_horizons_covered=1, min_observation_days=3.0,
    )
    return EvidenceSufficiencyResult(
        sufficient=True,
        gaps=[],
        live_like_evaluated=records_override,
        live_like_executes=30,
        live_like_rejects=70,
        assets_covered=["BTC"],
        horizons_covered=[15],
        observation_days=4.0,
        requirements_used=req,
    )


# ── Blocker-triggered NO_GO ────────────────────────────────────────────────────

class TestBlockerNoGo:

    def test_execution_rate_blocker(self):
        """Execution rate > 85% → BLOCKER → NO_GO."""
        live = _metrics(exec_rate=0.90)
        report = assess_readiness(live, _sufficient_evidence())
        assert report.verdict == ReadinessVerdict.NO_GO
        assert any(c.is_blocker for c in report.checks)

    def test_ev_haircut_blocker(self):
        """EV haircut > 80% → BLOCKER → NO_GO."""
        live = _metrics(mean_ev_haircut_pct=0.85)
        report = assess_readiness(live, _sufficient_evidence())
        assert report.verdict == ReadinessVerdict.NO_GO
        assert any(c.is_blocker for c in report.checks)

    def test_underround_rate_blocker(self):
        """Suspicious underround > 40% → BLOCKER → NO_GO."""
        live = _metrics(underround_rate=0.45)
        report = assess_readiness(live, _sufficient_evidence())
        assert report.verdict == ReadinessVerdict.NO_GO
        assert any(c.is_blocker for c in report.checks)

    def test_profile_divergence_blocker(self):
        """paper_loose vs live divergence > 60pp → BLOCKER."""
        live  = _metrics(exec_rate=0.10)
        loose = _metrics(profile="paper_loose", exec_rate=0.75)
        report = assess_readiness(live, _sufficient_evidence(), loose_metrics=loose)
        assert report.verdict == ReadinessVerdict.NO_GO
        assert any(c.is_blocker for c in report.checks)

    def test_blockers_listed_in_report(self):
        live = _metrics(exec_rate=0.90)
        report = assess_readiness(live, _sufficient_evidence())
        assert len(report.blockers) > 0
        for b in report.blockers:
            assert b.is_blocker


# ── Any failure → NO_GO ───────────────────────────────────────────────────────

class TestAnyFailNoGo:

    def test_two_fails_produce_no_go(self):
        """EV haircut > 60% AND underround > 25% → 2 FAILs → NO_GO."""
        live = _metrics(mean_ev_haircut_pct=0.65, underround_rate=0.28)
        report = assess_readiness(live, _sufficient_evidence())
        assert report.verdict == ReadinessVerdict.NO_GO
        assert len(report.fails) >= 2

    def test_single_fail_produces_no_go(self):
        """Only underround > 25% → 1 FAIL → NO_GO (Task 5.3: any FAIL blocks pilot)."""
        live = _metrics(underround_rate=0.28, mean_ev_haircut_pct=0.20)
        report = assess_readiness(live, _sufficient_evidence())
        assert report.verdict == ReadinessVerdict.NO_GO
        assert len(report.fails) == 1


# ── Paper_loose optimism warning ──────────────────────────────────────────────

class TestPaperLooseOptimism:

    def test_high_loose_divergence_produces_fail(self):
        """paper_loose >> live-like → > 40pp divergence → FAIL."""
        live  = _metrics(exec_rate=0.15)
        loose = _metrics(profile="paper_loose", exec_rate=0.60)
        report = assess_readiness(live, _sufficient_evidence(), loose_metrics=loose)
        # 45pp divergence → FAIL
        assert report.verdict in {ReadinessVerdict.NO_GO, ReadinessVerdict.CONDITIONAL}
        divergence_check = next(c for c in report.checks if c.name == "paper_loose_vs_live_divergence")
        assert divergence_check.metric_value is not None
        assert divergence_check.metric_value > 0.40

    def test_moderate_loose_divergence_produces_warn(self):
        """paper_loose somewhat above live → 25pp → WARN."""
        live  = _metrics(exec_rate=0.30)
        loose = _metrics(profile="paper_loose", exec_rate=0.55)
        report = assess_readiness(live, _sufficient_evidence(), loose_metrics=loose)
        divergence_check = next(c for c in report.checks if c.name == "paper_loose_vs_live_divergence")
        from monitoring.readiness_checks import CheckLevel
        assert divergence_check.level == CheckLevel.WARN

    def test_aligned_profiles_produce_green(self):
        """paper_loose and live-like aligned → divergence < 20pp → GREEN."""
        live  = _metrics(exec_rate=0.35)
        loose = _metrics(profile="paper_loose", exec_rate=0.45)
        report = assess_readiness(live, _sufficient_evidence(), loose_metrics=loose)
        divergence_check = next(c for c in report.checks if c.name == "paper_loose_vs_live_divergence")
        from monitoring.readiness_checks import CheckLevel
        assert divergence_check.level == CheckLevel.GREEN


# ── GO verdict ────────────────────────────────────────────────────────────────

class TestGoVerdict:

    def test_clean_metrics_produce_go_or_conditional(self):
        """All metrics within thresholds → GO or CONDITIONAL (warn from divergence)."""
        live  = _metrics(exec_rate=0.30, mean_ev_haircut_pct=0.20,
                         underround_rate=0.05, stale_rate=0.03,
                         partial_fill_rejection_rate=0.05)
        loose = _metrics(profile="paper_loose", exec_rate=0.40)
        report = assess_readiness(live, _sufficient_evidence(), loose_metrics=loose)
        assert report.verdict in {ReadinessVerdict.GO, ReadinessVerdict.CONDITIONAL}
        assert len(report.blockers) == 0

    def test_go_report_includes_pilot_constraints(self):
        live  = _metrics(exec_rate=0.30, mean_ev_haircut_pct=0.20,
                         underround_rate=0.05, stale_rate=0.03,
                         partial_fill_rejection_rate=0.05)
        loose = _metrics(profile="paper_loose", exec_rate=0.40)
        report = assess_readiness(live, _sufficient_evidence(), loose_metrics=loose)
        assert report.pilot_constraints is LIVE_PILOT_CONSTRAINTS
        assert report.pilot_constraints.max_nominal_usdc == 10.0
        assert report.pilot_constraints.max_open_positions == 1

    def test_pilot_constraints_always_present(self):
        """Pilot constraints appear even in NO_GO reports."""
        live = _metrics(exec_rate=0.95)  # blocker
        report = assess_readiness(live, _sufficient_evidence())
        assert report.verdict == ReadinessVerdict.NO_GO
        assert report.pilot_constraints is not None
        assert report.pilot_constraints.pilot_asset == "BTC"


# ── Execution rate FAIL (new threshold) ───────────────────────────────────────

class TestExecutionRateFail:

    def test_exec_rate_between_60_and_85_is_fail(self):
        """Execution rate 61-84% → FAIL (was WARN before audit fix)."""
        live = _metrics(exec_rate=0.70)
        report = assess_readiness(live, _sufficient_evidence())
        exec_check = next(c for c in report.checks if c.name == "execution_rate")
        from monitoring.readiness_checks import CheckLevel
        assert exec_check.level == CheckLevel.FAIL

    def test_exec_rate_fail_counts_toward_no_go(self):
        """exec_rate FAIL + another FAIL = 2 FAILs → NO_GO."""
        live = _metrics(exec_rate=0.70, underround_rate=0.28)
        report = assess_readiness(live, _sufficient_evidence())
        assert report.verdict == ReadinessVerdict.NO_GO
        assert len(report.fails) >= 2

    def test_exec_rate_60pct_exactly_is_green(self):
        """Execution rate exactly 60% is within normal range → GREEN."""
        live = _metrics(exec_rate=0.60)
        report = assess_readiness(live, _sufficient_evidence())
        exec_check = next(c for c in report.checks if c.name == "execution_rate")
        from monitoring.readiness_checks import CheckLevel
        assert exec_check.level == CheckLevel.GREEN


# ── paper_strict divergence (new check) ───────────────────────────────────────

class TestStrictDivergenceCheck:

    def test_strict_divergence_green_when_aligned(self):
        """paper_strict close to live → GREEN."""
        live   = _metrics(exec_rate=0.30)
        strict = _metrics(profile="paper_strict", exec_rate=0.38)  # 8pp divergence
        report = assess_readiness(live, _sufficient_evidence(), strict_metrics=strict)
        strict_check = next(
            (c for c in report.checks if c.name == "paper_strict_vs_live_divergence"), None
        )
        assert strict_check is not None
        from monitoring.readiness_checks import CheckLevel
        assert strict_check.level == CheckLevel.GREEN

    def test_strict_divergence_fail_when_large(self):
        """paper_strict >> live (30pp) → FAIL."""
        live   = _metrics(exec_rate=0.20)
        strict = _metrics(profile="paper_strict", exec_rate=0.52)  # 32pp divergence
        report = assess_readiness(live, _sufficient_evidence(), strict_metrics=strict)
        strict_check = next(
            (c for c in report.checks if c.name == "paper_strict_vs_live_divergence"), None
        )
        assert strict_check is not None
        from monitoring.readiness_checks import CheckLevel
        assert strict_check.level == CheckLevel.FAIL

    def test_strict_check_skipped_when_not_provided(self):
        """No strict_metrics → paper_strict check absent from report, verdict capped at CONDITIONAL."""
        live = _metrics(exec_rate=0.30)
        report = assess_readiness(live, _sufficient_evidence())
        names = [c.name for c in report.checks]
        assert "paper_strict_vs_live_divergence" not in names
        # Task 2.3: missing strict_metrics caps verdict at CONDITIONAL_REVIEW
        assert report.verdict != ReadinessVerdict.TINY_PILOT_CANDIDATE


# ── Regime stability (new check) ──────────────────────────────────────────────

class TestRegimeStabilityCheck:

    def _stable_regime(self):
        """Minimal duck-typed stable RegimeReview."""
        from types import SimpleNamespace
        flags = SimpleNamespace(
            rejection_rate_spike=False,
            ev_trend_negative=False,
            fillability_degrading=False,
            underround_rising=False,
        )
        return SimpleNamespace(
            is_stable=True,
            flags=flags,
            rolling_snapshots=[object(), object(), object()],
            summary="Stable over 3 windows.",
        )

    def _unstable_regime(self, flag_count: int = 2):
        from types import SimpleNamespace
        flags = SimpleNamespace(
            rejection_rate_spike=flag_count >= 1,
            ev_trend_negative=flag_count >= 2,
            fillability_degrading=False,
            underround_rising=False,
        )
        return SimpleNamespace(
            is_stable=False,
            flags=flags,
            rolling_snapshots=[object()],
            summary="Instability detected.",
        )

    def test_stable_regime_is_green(self):
        live = _metrics()
        report = assess_readiness(live, _sufficient_evidence(), regime_review=self._stable_regime())
        regime_check = next(c for c in report.checks if c.name == "regime_stability")
        from monitoring.readiness_checks import CheckLevel
        assert regime_check.level == CheckLevel.GREEN

    def test_two_flags_is_fail(self):
        live = _metrics()
        report = assess_readiness(live, _sufficient_evidence(), regime_review=self._unstable_regime(2))
        regime_check = next(c for c in report.checks if c.name == "regime_stability")
        from monitoring.readiness_checks import CheckLevel
        assert regime_check.level == CheckLevel.FAIL

    def test_one_flag_is_warn(self):
        live = _metrics()
        report = assess_readiness(live, _sufficient_evidence(), regime_review=self._unstable_regime(1))
        regime_check = next(c for c in report.checks if c.name == "regime_stability")
        from monitoring.readiness_checks import CheckLevel
        assert regime_check.level == CheckLevel.WARN

    def test_regime_check_skipped_when_not_provided(self):
        live = _metrics()
        report = assess_readiness(live, _sufficient_evidence())
        names = [c.name for c in report.checks]
        assert "regime_stability" not in names

    def test_regime_fail_contributes_to_no_go(self):
        """2-flag regime FAIL + 1 existing FAIL → 2 FAILs → NO_GO."""
        live = _metrics(underround_rate=0.28)  # 1 FAIL
        report = assess_readiness(
            live, _sufficient_evidence(),
            regime_review=self._unstable_regime(2),  # 1 more FAIL
        )
        assert report.verdict == ReadinessVerdict.NO_GO


# ── Journal integrity (new check) ─────────────────────────────────────────────

class TestJournalIntegrityCheck:

    def _stats(self, total: int, bad_json: int = 0, version_skips: int = 0):
        from types import SimpleNamespace
        bad_total = bad_json + version_skips
        return SimpleNamespace(
            total_lines=total,
            parsed_ok=total - bad_total,
            bad_json_count=bad_json,
            version_skip_count=version_skips,
            bad_line_fraction=bad_total / total if total > 0 else 0.0,
        )

    def test_clean_journal_is_green(self):
        live = _metrics()
        report = assess_readiness(
            live, _sufficient_evidence(),
            journal_integrity=self._stats(100, 0, 0),
        )
        jc = next(c for c in report.checks if c.name == "journal_integrity")
        from monitoring.readiness_checks import CheckLevel
        assert jc.level == CheckLevel.GREEN

    def test_few_bad_lines_is_warn(self):
        live = _metrics()
        report = assess_readiness(
            live, _sufficient_evidence(),
            journal_integrity=self._stats(100, bad_json=3),  # 3% < 5%
        )
        jc = next(c for c in report.checks if c.name == "journal_integrity")
        from monitoring.readiness_checks import CheckLevel
        assert jc.level == CheckLevel.WARN

    def test_many_bad_lines_is_fail(self):
        live = _metrics()
        report = assess_readiness(
            live, _sufficient_evidence(),
            journal_integrity=self._stats(100, bad_json=7),  # 7% → FAIL (5-10% range)
        )
        jc = next(c for c in report.checks if c.name == "journal_integrity")
        from monitoring.readiness_checks import CheckLevel
        assert jc.level == CheckLevel.FAIL

    def test_extreme_bad_lines_is_blocker(self):
        live = _metrics()
        report = assess_readiness(
            live, _sufficient_evidence(),
            journal_integrity=self._stats(100, bad_json=12),  # 12% >= 10% → BLOCKER
        )
        jc = next(c for c in report.checks if c.name == "journal_integrity")
        from monitoring.readiness_checks import CheckLevel
        assert jc.level == CheckLevel.BLOCKER
        assert report.verdict == ReadinessVerdict.NO_GO

    def test_journal_check_skipped_when_not_provided(self):
        live = _metrics()
        report = assess_readiness(live, _sufficient_evidence())
        names = [c.name for c in report.checks]
        assert "journal_integrity" not in names


# ── Formatting ────────────────────────────────────────────────────────────────

class TestFormatting:

    def test_format_report_non_empty(self):
        live = _metrics()
        report = assess_readiness(live, _sufficient_evidence())
        text = format_readiness_report(report)
        assert len(text) > 100
        assert report.verdict.value in text

    def test_format_report_contains_verdict(self):
        live = _metrics(exec_rate=0.90)
        report = assess_readiness(live, _sufficient_evidence())
        text = format_readiness_report(report)
        assert "NO_GO" in text

    def test_format_go_includes_constraints(self):
        live  = _metrics(exec_rate=0.30, mean_ev_haircut_pct=0.15,
                         underround_rate=0.04, stale_rate=0.02,
                         partial_fill_rejection_rate=0.05)
        loose = _metrics(profile="paper_loose", exec_rate=0.40)
        report = assess_readiness(live, _sufficient_evidence(), loose_metrics=loose)
        if report.verdict == ReadinessVerdict.GO:
            text = format_readiness_report(report)
            assert "BTC" in text
            assert "10.00" in text

    def test_insufficient_evidence_verdict(self):
        from shadow_runner.validation import EvidenceSufficiencyResult, EvidenceRequirements
        req = EvidenceRequirements()
        ev_result = EvidenceSufficiencyResult(
            sufficient=False,
            gaps=[],
            live_like_evaluated=0,
            live_like_executes=0,
            live_like_rejects=0,
            assets_covered=[],
            horizons_covered=[],
            observation_days=0.0,
            requirements_used=req,
        )
        live = _metrics(total=0, exec_count=0, exec_rate=0.0)
        report = assess_readiness(live, ev_result)
        assert report.verdict == ReadinessVerdict.INSUFFICIENT_EVIDENCE
        assert not report.evidence_sufficient
