"""
shadow_runner/validation.py

Phase 14: Evidence sufficiency gate.

Before any live pilot readiness discussion is allowed, the shadow corpus
must meet minimum evidence requirements. This module answers one question:

    "Do we have enough shadow data to meaningfully evaluate the system?"

Design decisions:
  1. Evidence requirements are explicit dataclass fields, not magic numbers.
     Default values are our chosen thresholds with documented rationale.
  2. Insufficiency is NOT a blocker in the same sense as a hard no-go.
     It simply means: "not enough data yet — keep collecting."
  3. The check operates on raw record lists, not ShadowSummaryMetrics.
     Rationale: avoids double-filtering and makes it easy to verify counts.
  4. Observation duration is measured from ts_recorded_utc span.
     Three calendar days minimum: enough to observe temporal variation without
     requiring a week of data before any assessment is possible.
  5. Asset/horizon coverage: defaults require only 1 asset and 1 horizon.
     Rationale: early shadow runs may intentionally cover only BTC/15m.
     Override to require 2 assets once multi-asset coverage begins.

Rationale for default thresholds:
  - min_live_like_evaluated = 100:
      Gives ±10 pp CI (95%, binomial) on a 30% rejection rate.
      50 records gave ±14 pp — too wide for a live pilot gate.
  - min_live_like_executes = 20:
      Minimum to see a meaningful EV distribution; below this the mean
      is dominated by individual outliers.
  - min_live_like_rejects = 20:
      Minimum to see rejection reason composition; below this any single
      reason can look dominant just by chance.
  - min_observation_days = 5:
      Five calendar days captures weekday/weekend variation and at least
      two distinct market sessions. Three days was insufficient to rule out
      lucky single-session performance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from shadow_runner.types import ShadowDecisionRecord


@dataclass
class EvidenceRequirements:
    """
    Minimum thresholds required before readiness review is attempted.

    All fields have documented defaults. Override for non-default configurations.
    """
    # Live-like profile decisions
    min_live_like_evaluated: int   = 100    # see module docstring rationale
    min_live_like_executes:  int   = 20
    min_live_like_rejects:   int   = 20

    # Coverage
    min_assets_covered:      int   = 1      # at least 1 asset (e.g., BTC)
    min_horizons_covered:    int   = 1      # at least 1 horizon (e.g., 15m)

    # Temporal span
    min_observation_days:    float = 5.0    # calendar days

    # Profile used as "live-like" reference
    live_like_profile:       str   = "live"


@dataclass
class EvidenceGap:
    """A single unmet evidence requirement."""
    dimension: str      # e.g. "live_like_evaluated"
    required: float
    actual: float
    message: str


@dataclass
class EvidenceSufficiencyResult:
    """
    Output of check_evidence_sufficiency().

    sufficient=True means the corpus meets all minimum requirements.
    sufficient=False means at least one requirement is unmet — gaps lists them.
    """
    sufficient: bool
    gaps: list                      # list[EvidenceGap]
    live_like_evaluated: int
    live_like_executes:  int
    live_like_rejects:   int
    assets_covered:      list       # list[str]
    horizons_covered:    list       # list[int]
    observation_days:    float
    requirements_used:   EvidenceRequirements


def check_evidence_sufficiency(
    records: list[ShadowDecisionRecord],
    requirements: Optional[EvidenceRequirements] = None,
) -> EvidenceSufficiencyResult:
    """
    Check whether the record corpus meets minimum evidence requirements.

    Parameters
    ----------
    records : list[ShadowDecisionRecord]
        All shadow records to evaluate — any profile, any time range.
    requirements : EvidenceRequirements | None
        Requirements to enforce. Defaults to EvidenceRequirements() (standard thresholds).

    Returns
    -------
    EvidenceSufficiencyResult
        sufficient=True iff all gaps are empty.
    """
    if requirements is None:
        requirements = EvidenceRequirements()

    profile = requirements.live_like_profile
    live_recs = [r for r in records if r.policy_profile == profile]

    # Only "live_shadow" records count as valid evidence.
    # "synthetic" and "demo" records are excluded — a corpus of only test fixtures
    # or demo runs must not satisfy the readiness gate.
    live_recs = [r for r in live_recs if getattr(r, "evidence_source", "live_shadow") == "live_shadow"]

    exec_recs = [r for r in live_recs if r.decision_summary.decision != "REJECT"]
    rej_recs  = [r for r in live_recs if r.decision_summary.decision == "REJECT"]

    n_eval    = len(live_recs)
    n_exec    = len(exec_recs)
    n_rej     = len(rej_recs)

    assets   = sorted(set(r.signal.asset for r in live_recs))
    horizons = sorted(set(r.signal.horizon_minutes for r in live_recs))

    # Temporal span
    ts_list  = [r.ts_recorded_utc for r in live_recs]
    if len(ts_list) >= 2:
        from datetime import timezone
        ts_first = min(ts_list)
        ts_last  = max(ts_list)
        if ts_first.tzinfo is None:
            ts_first = ts_first.replace(tzinfo=timezone.utc)
        if ts_last.tzinfo is None:
            ts_last = ts_last.replace(tzinfo=timezone.utc)
        obs_days = (ts_last - ts_first).total_seconds() / 86400.0
    else:
        obs_days = 0.0

    gaps: list[EvidenceGap] = []

    if n_eval < requirements.min_live_like_evaluated:
        gaps.append(EvidenceGap(
            dimension="live_like_evaluated",
            required=requirements.min_live_like_evaluated,
            actual=n_eval,
            message=(
                f"Need {requirements.min_live_like_evaluated} live-like evaluated decisions "
                f"for meaningful statistics; have {n_eval}."
            ),
        ))

    if n_exec < requirements.min_live_like_executes:
        gaps.append(EvidenceGap(
            dimension="live_like_executes",
            required=requirements.min_live_like_executes,
            actual=n_exec,
            message=(
                f"Need {requirements.min_live_like_executes} live-like execute decisions "
                f"to observe EV distribution; have {n_exec}."
            ),
        ))

    if n_rej < requirements.min_live_like_rejects:
        gaps.append(EvidenceGap(
            dimension="live_like_rejects",
            required=requirements.min_live_like_rejects,
            actual=n_rej,
            message=(
                f"Need {requirements.min_live_like_rejects} live-like reject decisions "
                f"to observe rejection composition; have {n_rej}."
            ),
        ))

    if len(assets) < requirements.min_assets_covered:
        gaps.append(EvidenceGap(
            dimension="assets_covered",
            required=requirements.min_assets_covered,
            actual=len(assets),
            message=(
                f"Need {requirements.min_assets_covered} asset(s) covered; "
                f"have {len(assets)}: {assets}."
            ),
        ))

    if len(horizons) < requirements.min_horizons_covered:
        gaps.append(EvidenceGap(
            dimension="horizons_covered",
            required=requirements.min_horizons_covered,
            actual=len(horizons),
            message=(
                f"Need {requirements.min_horizons_covered} horizon(s) covered; "
                f"have {len(horizons)}: {horizons}."
            ),
        ))

    if obs_days < requirements.min_observation_days:
        gaps.append(EvidenceGap(
            dimension="observation_days",
            required=requirements.min_observation_days,
            actual=obs_days,
            message=(
                f"Need {requirements.min_observation_days:.1f} calendar days of observation; "
                f"have {obs_days:.2f}. A short sample cannot distinguish stable from lucky."
            ),
        ))

    return EvidenceSufficiencyResult(
        sufficient=len(gaps) == 0,
        gaps=gaps,
        live_like_evaluated=n_eval,
        live_like_executes=n_exec,
        live_like_rejects=n_rej,
        assets_covered=assets,
        horizons_covered=horizons,
        observation_days=obs_days,
        requirements_used=requirements,
    )
