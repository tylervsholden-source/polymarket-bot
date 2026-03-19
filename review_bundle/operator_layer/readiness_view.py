"""
operator_layer/readiness_view.py

Readiness state builder.

Source: data/readiness_verdict.json (written by monitoring/daily_review.py).

Design:
  - If readiness_verdict.json exists and is valid, build full ReadinessState.
  - If not present, return a ReadinessState with status="NOT_REVIEWED".
  - Never invoke assess_readiness() directly — that is the daily_review's job.
    This module only reads and formats the already-computed verdict.
  - Profile summaries are computed from recent shadow records to give the
    operator a live view even without a fresh daily review.
"""
from __future__ import annotations

import dataclasses
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

from operator_layer.types import (
    ProfileReadinessSummary,
    ReadinessCheckRow,
    ReadinessState,
)


_NOT_REVIEWED_STATE = ReadinessState(
    status="NOT_REVIEWED",
    verdict_reason="No readiness review has been run yet. Run daily_review.py to generate a verdict.",
    evidence_sufficient=False,
    last_reviewed_at=None,
    blocker_count=0,
    fail_count=0,
    warn_count=0,
    checks=[],
    blockers=[],
    fails=[],
    warns=[],
    live_like_evaluated=0,
    live_like_executes=0,
    live_like_rejects=0,
    observation_days=0.0,
    evidence_gaps=["No review file found."],
    live_summary=None,
    strict_summary=None,
    loose_summary=None,
    pilot_max_positions=1,
    pilot_asset="BTC",
    pilot_horizon_min=15,
    pilot_max_usdc=10.0,
    pilot_daily_loss_usdc=5.0,
    pilot_kill_usdc=3.0,
)


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return None


def _build_check_row(check_dict: dict) -> ReadinessCheckRow:
    return ReadinessCheckRow(
        name=check_dict.get("name", ""),
        level=check_dict.get("level", "OK"),
        metric_value=check_dict.get("metric_value"),
        message=check_dict.get("message", ""),
    )


def _build_profile_summary(
    profile: str,
    records: list[dict],
) -> Optional[ProfileReadinessSummary]:
    """Build per-profile summary from raw journal records."""
    prof_recs = [
        r for r in records
        if r.get("policy_profile") == profile
        and r.get("evidence_source", "live_shadow") == "live_shadow"
    ]
    if not prof_recs:
        return None

    n = len(prof_recs)
    exec_recs = [r for r in prof_recs
                 if r.get("decision", {}).get("decision") != "REJECT"]
    rej_recs  = [r for r in prof_recs
                 if r.get("decision", {}).get("decision") == "REJECT"]

    # Mean EV
    evs = [
        r["decision"]["execution_adjusted_ev"]
        for r in exec_recs
        if r.get("decision", {}).get("execution_adjusted_ev") is not None
    ]
    mean_ev = sum(evs) / len(evs) if evs else None

    # Top rejection reasons
    reasons = Counter(
        r.get("decision", {}).get("rejection_reason", "UNKNOWN")
        for r in rej_recs
    )

    return ProfileReadinessSummary(
        profile=profile,
        total_evaluated=n,
        execute_count=len(exec_recs),
        reject_count=len(rej_recs),
        execution_rate=len(exec_recs) / n if n > 0 else 0.0,
        mean_ev=mean_ev,
        top_rejection_reasons=reasons.most_common(5),
    )


def build_readiness_state(
    verdict_data: dict,
    recent_records: list[dict],
) -> ReadinessState:
    """
    Build ReadinessState from readiness_verdict.json contents and recent records.

    Parameters
    ----------
    verdict_data : dict
        Output of ledgers.read_readiness_verdict().
    recent_records : list[dict]
        Recent shadow journal records for computing profile summaries.
    """
    verdict = verdict_data.get("verdict", "NOT_REVIEWED")
    if verdict == "NOT_REVIEWED":
        # Enrich with whatever we can compute from records
        state = dataclasses.replace(
            _NOT_REVIEWED_STATE,
            live_summary=_build_profile_summary("live", recent_records),
            strict_summary=_build_profile_summary("paper_strict", recent_records),
            loose_summary=_build_profile_summary("paper_loose", recent_records),
        )
        return state

    # Parse checks
    all_checks    = [_build_check_row(c) for c in verdict_data.get("checks", [])]
    blocker_rows  = [_build_check_row(c) for c in verdict_data.get("blockers", [])]
    fail_rows     = [_build_check_row(c) for c in verdict_data.get("fails", [])]
    warn_rows     = [_build_check_row(c) for c in verdict_data.get("warns", [])]

    # Evidence result
    ev_result = verdict_data.get("evidence_result", {})
    gaps_list = [g.get("message", "") for g in ev_result.get("gaps", [])]

    # Pilot constraints from stored verdict (or fall back to canonical values)
    pc = verdict_data.get("pilot_constraints", {})

    return ReadinessState(
        status=verdict,
        verdict_reason=verdict_data.get("verdict_reason", ""),
        evidence_sufficient=verdict_data.get("evidence_sufficient", False),
        last_reviewed_at=_parse_dt(verdict_data.get("generated_utc")),
        blocker_count=len(blocker_rows),
        fail_count=len(fail_rows),
        warn_count=len(warn_rows),
        checks=all_checks,
        blockers=blocker_rows,
        fails=fail_rows,
        warns=warn_rows,
        live_like_evaluated=ev_result.get("live_like_evaluated", 0),
        live_like_executes=ev_result.get("live_like_executes", 0),
        live_like_rejects=ev_result.get("live_like_rejects", 0),
        observation_days=ev_result.get("observation_days", 0.0),
        evidence_gaps=gaps_list,
        live_summary=_build_profile_summary("live", recent_records),
        strict_summary=_build_profile_summary("paper_strict", recent_records),
        loose_summary=_build_profile_summary("paper_loose", recent_records),
        pilot_max_positions=pc.get("max_open_positions", 1),
        pilot_asset=pc.get("pilot_asset", "BTC"),
        pilot_horizon_min=pc.get("pilot_horizon_minutes", 15),
        pilot_max_usdc=pc.get("max_nominal_usdc", 10.0),
        pilot_daily_loss_usdc=pc.get("daily_max_loss_usdc", 5.0),
        pilot_kill_usdc=pc.get("single_loss_kill_usdc", 3.0),
    )
