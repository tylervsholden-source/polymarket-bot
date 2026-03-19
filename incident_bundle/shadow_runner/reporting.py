"""
shadow_runner/reporting.py

Phase 13: Policy profile comparison reports.

Generates human-readable and structured reports comparing decision outcomes
across policy profiles (live vs paper_strict vs paper_loose) for the same
set of candidates.

Design:
  - Input: list[ShadowDecisionRecord] from a single run (same run_id)
  - Groups records by (market_id, signal_timestamp_utc) to find multi-profile
    evaluations of the same candidate.
  - Reports: per-profile summary + cross-profile comparison table.

Key comparisons reported:
  1. Execution rate per profile (what fraction executes)
  2. Rejection reason breakdown per profile
  3. Cross-profile agreement rate (live & strict agree / total)
  4. Candidates that execute in paper_loose but not live (observation-only zone)
  5. EV distribution per profile (mean / p25 / p75 for executes)
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from shadow_runner.types import ShadowDecisionRecord


@dataclass
class ProfileSummary:
    """Summary statistics for one policy profile."""
    policy_profile:       str
    total_candidates:     int
    execute_count:        int
    reject_count:         int
    execution_rate:       float                  # execute_count / total
    rejection_reasons:    dict                   # {reason: count}
    ev_mean:              Optional[float]        # mean exec_adj_ev for executes
    ev_p25:               Optional[float]
    ev_p75:               Optional[float]
    sanity_note_count:    int                    # records with pricing_sanity_notes set


@dataclass
class CrossProfileComparison:
    """
    Pairwise comparison between two profiles on shared candidates.

    A 'shared candidate' is any (market_id + signal_timestamp_utc) pair that
    was evaluated by both profiles.
    """
    profile_a:                   str
    profile_b:                   str
    shared_candidates:           int
    both_execute:                int             # A=EXECUTE and B=EXECUTE
    both_reject:                 int             # A=REJECT and B=REJECT
    a_executes_b_rejects:        int             # A passes, B fails
    b_executes_a_rejects:        int             # B passes, A fails
    agreement_rate:              float           # (both_execute + both_reject) / shared
    divergence_rate:             float           # 1 - agreement_rate


@dataclass
class PolicyComparisonReport:
    """
    Full policy comparison report for a shadow run.
    """
    run_id:                     str
    generated_utc:              datetime
    total_records:              int
    profiles_found:             list             # list of policy_profile strings
    per_profile:                dict             # {profile: ProfileSummary}
    cross_profile:              list             # list[CrossProfileComparison]
    # Candidates that only pass in paper_loose (not in live or paper_strict)
    loose_only_execute_count:   int
    # Candidates that pass in paper_strict but not live
    strict_not_live_count:      int


def generate_comparison_report(
    records: list[ShadowDecisionRecord],
    run_id: Optional[str] = None,
) -> PolicyComparisonReport:
    """
    Generate a PolicyComparisonReport from a list of shadow run records.

    Records may span multiple runs; if run_id is provided, only records
    matching that run_id are included.
    """
    if run_id is not None:
        records = [r for r in records if r.run_id == run_id]

    if not records:
        return PolicyComparisonReport(
            run_id=run_id or "",
            generated_utc=datetime.now(timezone.utc),
            total_records=0,
            profiles_found=[],
            per_profile={},
            cross_profile=[],
            loose_only_execute_count=0,
            strict_not_live_count=0,
        )

    actual_run_id = records[0].run_id

    # Group by policy profile
    by_profile: dict[str, list[ShadowDecisionRecord]] = {}
    for r in records:
        by_profile.setdefault(r.policy_profile, []).append(r)

    profiles = sorted(by_profile.keys())

    # Per-profile summaries
    per_profile: dict[str, ProfileSummary] = {}
    for profile, recs in by_profile.items():
        per_profile[profile] = _profile_summary(profile, recs)

    # Cross-profile comparison: all pairs
    cross: list[CrossProfileComparison] = []
    for i, pa in enumerate(profiles):
        for pb in profiles[i+1:]:
            cmp = _cross_comparison(pa, by_profile[pa], pb, by_profile[pb])
            cross.append(cmp)

    # Loose-only and strict-not-live counts
    loose_only, strict_not_live = _divergence_counts(by_profile)

    return PolicyComparisonReport(
        run_id=actual_run_id,
        generated_utc=datetime.now(timezone.utc),
        total_records=len(records),
        profiles_found=profiles,
        per_profile=per_profile,
        cross_profile=cross,
        loose_only_execute_count=loose_only,
        strict_not_live_count=strict_not_live,
    )


def format_report(report: PolicyComparisonReport) -> str:
    """
    Format a PolicyComparisonReport as a human-readable text block.
    """
    lines = [
        f"Shadow Run Policy Comparison Report",
        f"run_id:    {report.run_id}",
        f"generated: {report.generated_utc.isoformat()}",
        f"records:   {report.total_records}",
        f"profiles:  {', '.join(report.profiles_found)}",
        "",
        "=== Per-Profile Summary ===",
    ]
    for profile in report.profiles_found:
        s = report.per_profile.get(profile)
        if s is None:
            continue
        lines.append(f"\n[{profile}]")
        lines.append(f"  candidates:     {s.total_candidates}")
        lines.append(f"  execute:        {s.execute_count}  ({s.execution_rate:.1%})")
        lines.append(f"  reject:         {s.reject_count}")
        if s.rejection_reasons:
            for reason, cnt in sorted(s.rejection_reasons.items(), key=lambda x: -x[1]):
                lines.append(f"    {reason}: {cnt}")
        if s.ev_mean is not None:
            lines.append(
                f"  exec_adj_ev:    mean={s.ev_mean:.4f}  "
                f"p25={s.ev_p25:.4f}  p75={s.ev_p75:.4f}"
            )
        if s.sanity_note_count:
            lines.append(f"  sanity notes:   {s.sanity_note_count} executes with suspicious pricing")

    if report.cross_profile:
        lines.append("\n=== Cross-Profile Comparison ===")
        for cmp in report.cross_profile:
            lines.append(
                f"\n{cmp.profile_a} vs {cmp.profile_b} "
                f"({cmp.shared_candidates} shared candidates)"
            )
            lines.append(f"  agreement:       {cmp.agreement_rate:.1%}")
            lines.append(f"  divergence:      {cmp.divergence_rate:.1%}")
            lines.append(f"  both execute:    {cmp.both_execute}")
            lines.append(f"  both reject:     {cmp.both_reject}")
            lines.append(f"  {cmp.profile_a} exec / {cmp.profile_b} reject: {cmp.a_executes_b_rejects}")
            lines.append(f"  {cmp.profile_b} exec / {cmp.profile_a} reject: {cmp.b_executes_a_rejects}")

    if report.loose_only_execute_count:
        lines.append(
            f"\n*** OBSERVATION ZONE: {report.loose_only_execute_count} candidate(s) "
            f"execute only in paper_loose — do not use as live signal ***"
        )
    if report.strict_not_live_count:
        lines.append(
            f"\n  {report.strict_not_live_count} candidate(s) execute in paper_strict "
            f"but not live — edge_threshold or size difference"
        )

    return "\n".join(lines)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _candidate_key(r: ShadowDecisionRecord) -> tuple:
    """Unique key for a candidate (asset + market_id + signal_timestamp_utc)."""
    return (
        r.signal.asset,
        r.pricing.market_id,
        r.signal.signal_timestamp_utc,
    )


def _is_execute(r: ShadowDecisionRecord) -> bool:
    return r.decision_summary.decision in ("EXECUTE_YES", "EXECUTE_NO")


def _profile_summary(profile: str, recs: list[ShadowDecisionRecord]) -> ProfileSummary:
    executes = [r for r in recs if _is_execute(r)]
    rejects  = [r for r in recs if not _is_execute(r)]

    reasons: Counter = Counter()
    for r in rejects:
        reason = r.decision_summary.rejection_reason or "UNKNOWN"
        reasons[reason] += 1

    ev_values = [
        r.decision_summary.execution_adjusted_ev
        for r in executes
        if r.decision_summary.execution_adjusted_ev is not None
    ]
    ev_mean, ev_p25, ev_p75 = _ev_stats(ev_values)

    sanity_notes = sum(
        1 for r in executes
        if r.decision_summary.pricing_sanity_notes is not None
    )

    total = len(recs)
    return ProfileSummary(
        policy_profile=profile,
        total_candidates=total,
        execute_count=len(executes),
        reject_count=len(rejects),
        execution_rate=len(executes) / total if total > 0 else 0.0,
        rejection_reasons=dict(reasons),
        ev_mean=ev_mean,
        ev_p25=ev_p25,
        ev_p75=ev_p75,
        sanity_note_count=sanity_notes,
    )


def _cross_comparison(
    pa: str, recs_a: list[ShadowDecisionRecord],
    pb: str, recs_b: list[ShadowDecisionRecord],
) -> CrossProfileComparison:
    key_to_a = {_candidate_key(r): r for r in recs_a}
    key_to_b = {_candidate_key(r): r for r in recs_b}
    shared_keys = set(key_to_a) & set(key_to_b)

    both_exec = both_rej = a_only = b_only = 0
    for key in shared_keys:
        ra, rb = key_to_a[key], key_to_b[key]
        ea, eb = _is_execute(ra), _is_execute(rb)
        if ea and eb:
            both_exec += 1
        elif not ea and not eb:
            both_rej += 1
        elif ea and not eb:
            a_only += 1
        else:
            b_only += 1

    n = len(shared_keys)
    agreement = (both_exec + both_rej) / n if n > 0 else 1.0
    return CrossProfileComparison(
        profile_a=pa,
        profile_b=pb,
        shared_candidates=n,
        both_execute=both_exec,
        both_reject=both_rej,
        a_executes_b_rejects=a_only,
        b_executes_a_rejects=b_only,
        agreement_rate=agreement,
        divergence_rate=1.0 - agreement,
    )


def _divergence_counts(
    by_profile: dict[str, list[ShadowDecisionRecord]],
) -> tuple[int, int]:
    """
    Returns (loose_only_count, strict_not_live_count).

    loose_only: executes in paper_loose but not in live or paper_strict.
    strict_not_live: executes in paper_strict but not in live.
    """
    def key_to_exec(recs: list[ShadowDecisionRecord]) -> set:
        return {_candidate_key(r) for r in recs if _is_execute(r)}

    loose_exec  = key_to_exec(by_profile.get("paper_loose", []))
    strict_exec = key_to_exec(by_profile.get("paper_strict", []))
    live_exec   = key_to_exec(by_profile.get("live", []))

    loose_only = len(loose_exec - strict_exec - live_exec)
    strict_not_live = len(strict_exec - live_exec)

    return loose_only, strict_not_live


def _ev_stats(
    values: list[float],
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    if not values:
        return None, None, None
    s = sorted(values)
    n = len(s)
    mean = sum(s) / n
    p25  = s[max(0, int(n * 0.25))]
    p75  = s[min(n - 1, int(n * 0.75))]
    return mean, p25, p75
