"""
operator_layer/health.py

System health aggregation.

Inputs:
  - journal integrity stats (from ledgers.read_journal_integrity_stats)
  - recent shadow records (list[dict] from ledgers.read_journal_records)
  - status snapshot (from ledgers.read_status_snapshot)
  - control state (from ledgers.read_control_state)

Outputs: HealthState

ALERT THRESHOLDS (explicit, not magic numbers):
  SUSPICIOUS_UNDERROUND_WARN  = 0.20  (>20% of records have sanity notes)
  STALE_PRICING_WARN          = 0.15  (>15% of records have stale pricing)
  PARTIAL_FILL_WARN           = 0.30  (>30% of executes had fill_fraction < 1)
  JOURNAL_BAD_LINE_WARN       = 0.02  (>2% bad lines in journal)
"""
from __future__ import annotations

from datetime import datetime, timezone

from operator_layer.types import HealthAlert, HealthState

SUSPICIOUS_UNDERROUND_WARN = 0.20
STALE_PRICING_WARN         = 0.15
PARTIAL_FILL_WARN          = 0.30
JOURNAL_BAD_LINE_WARN      = 0.02


def _rate(count: int, total: int) -> float:
    return count / total if total > 0 else 0.0


def build_health_state(
    integrity_stats: dict,
    recent_records: list[dict],
    status_data: dict,
    control_data: dict,
) -> HealthState:
    """
    Build HealthState from all available health data sources.

    Parameters
    ----------
    integrity_stats : dict
        Output of ledgers.read_journal_integrity_stats().
    recent_records : list[dict]
        Recent shadow journal records (raw dicts).
    status_data : dict
        Output of ledgers.read_status_snapshot().
    control_data : dict
        Output of ledgers.read_control_state().
    """
    alerts: list[HealthAlert] = []

    # ── Journal integrity ──────────────────────────────────────────────────────
    bad_frac = integrity_stats.get("bad_line_fraction", 0.0)
    total_lines = integrity_stats.get("total_lines", 0)
    journal_healthy = bad_frac <= JOURNAL_BAD_LINE_WARN

    if not journal_healthy:
        alerts.append(HealthAlert(
            level="WARNING",
            category="JOURNAL",
            message=f"Journal has {bad_frac:.1%} bad lines ({integrity_stats.get('bad_json_count', 0)} lines)",
            metric_value=bad_frac,
            threshold=JOURNAL_BAD_LINE_WARN,
        ))

    # ── Record-level rate computation ──────────────────────────────────────────
    # Filter to live_shadow records only
    live_recs = [r for r in recent_records
                 if r.get("evidence_source", "live_shadow") == "live_shadow"]
    n = len(live_recs)

    # Suspicious underround rate: records where pricing_sanity_notes is not null/empty
    sanity_note_count = sum(
        1 for r in live_recs
        if r.get("decision", {}).get("pricing_sanity_notes")
    )
    suspicious_rate = _rate(sanity_note_count, n)

    # Stale pricing rate: snapshot_age_seconds > 60s
    stale_count = sum(
        1 for r in live_recs
        if r.get("pricing", {}).get("snapshot_age_seconds", 0) > 60.0
    )
    stale_rate = _rate(stale_count, n)

    # Partial fill rate: executes with fill_fraction < 1.0
    exec_recs = [r for r in live_recs
                 if r.get("decision", {}).get("decision") != "REJECT"]
    n_exec = len(exec_recs)
    partial_count = sum(
        1 for r in exec_recs
        if r.get("decision", {}).get("fill_fraction") is not None
        and r.get("decision", {}).get("fill_fraction", 1.0) < 1.0
    )
    partial_rate = _rate(partial_count, n_exec)

    # ── Pricing alerts ─────────────────────────────────────────────────────────
    pricing_alerts: list[HealthAlert] = []
    if suspicious_rate > SUSPICIOUS_UNDERROUND_WARN:
        pricing_alerts.append(HealthAlert(
            level="WARNING",
            category="PRICING",
            message=f"Suspicious underround in {suspicious_rate:.1%} of recent decisions",
            metric_value=suspicious_rate,
            threshold=SUSPICIOUS_UNDERROUND_WARN,
        ))
    if stale_rate > STALE_PRICING_WARN:
        pricing_alerts.append(HealthAlert(
            level="WARNING",
            category="PRICING",
            message=f"Stale pricing (>60s) in {stale_rate:.1%} of recent decisions",
            metric_value=stale_rate,
            threshold=STALE_PRICING_WARN,
        ))
    if partial_rate > PARTIAL_FILL_WARN:
        pricing_alerts.append(HealthAlert(
            level="WARNING",
            category="PRICING",
            message=f"High partial fill rate: {partial_rate:.1%} of executes",
            metric_value=partial_rate,
            threshold=PARTIAL_FILL_WARN,
        ))

    # ── Cycle health ───────────────────────────────────────────────────────────
    running = status_data.get("running", False)
    live_trading = control_data.get("live_trading", False)
    sim_running = control_data.get("simulation_running", False)

    if live_trading:
        env = "live"
    elif sim_running or running:
        env = "simulation"
    else:
        env = "stopped"

    # ── Profile divergence (simple: if paper_loose execute rate >> live rate) ──
    divergence_alerts: list[HealthAlert] = []
    if n >= 20:
        live_recs_live_profile = [
            r for r in live_recs if r.get("policy_profile") == "live"
        ]
        loose_recs = [
            r for r in live_recs if r.get("policy_profile") == "paper_loose"
        ]
        if live_recs_live_profile and loose_recs:
            live_exec_rate = _rate(
                sum(1 for r in live_recs_live_profile
                    if r.get("decision", {}).get("decision") != "REJECT"),
                len(live_recs_live_profile),
            )
            loose_exec_rate = _rate(
                sum(1 for r in loose_recs
                    if r.get("decision", {}).get("decision") != "REJECT"),
                len(loose_recs),
            )
            divergence = loose_exec_rate - live_exec_rate
            if divergence > 0.30:
                divergence_alerts.append(HealthAlert(
                    level="WARNING",
                    category="DRIFT",
                    message=(
                        f"paper_loose executes {divergence:.1%} more than live profile. "
                        f"Many decisions only pass in loose mode."
                    ),
                    metric_value=divergence,
                    threshold=0.30,
                ))

    # ── Blockers ───────────────────────────────────────────────────────────────
    blocker_reasons: list[str] = []
    if not journal_healthy:
        blocker_reasons.append("JOURNAL_INTEGRITY")

    journal_alerts = [a for a in alerts if a.category == "JOURNAL"]
    all_alerts = alerts + pricing_alerts + divergence_alerts

    return HealthState(
        journal_healthy=journal_healthy,
        journal_bad_line_fraction=bad_frac,
        journal_total_lines=total_lines,
        drift_alerts=[a for a in all_alerts if a.category == "DRIFT"] + journal_alerts,
        pricing_sanity_alerts=pricing_alerts,
        suspicious_underround_rate=suspicious_rate if n > 0 else None,
        stale_pricing_rate=stale_rate if n > 0 else None,
        partial_fill_rate=partial_rate if n_exec > 0 else None,
        policy_divergence_alerts=divergence_alerts,
        cycle_running=running or live_trading or sim_running,
        last_cycle_at=status_data.get("updated"),
        cycles_completed=status_data.get("cycle"),
        blocker_active=len(blocker_reasons) > 0,
        blocker_reasons=blocker_reasons,
        last_updated_utc=datetime.now(timezone.utc),
    )
