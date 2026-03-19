"""
operator_layer/aggregator.py

Central aggregator — the single entry point for the Architect Chamber dashboard.

Usage:
    from operator_layer.aggregator import build_chamber_summary
    summary = build_chamber_summary()

This module:
  1. Reads all data sources via ledgers.py
  2. Calls pnl.py, health.py, readiness_view.py builders
  3. Converts raw journal records to DecisionEvent objects
  4. Computes profile comparison summary
  5. Assembles ChamberSummary

The dashboard calls build_chamber_summary() and serializes it.
No UI or business logic belongs here — only assembly.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from operator_layer.health import build_health_state
from operator_layer.ledgers import (
    find_journal_files,
    read_control_state,
    read_journal_integrity_stats,
    read_journal_records,
    read_positions_ledger,
    read_readiness_verdict,
    read_status_snapshot,
)
from operator_layer.pnl import build_closed_trades, build_equity_state, build_open_positions
from operator_layer.readiness_view import build_readiness_state
from operator_layer.types import (
    ChamberSummary,
    DecisionChain,
    DecisionEvent,
    ProfileComparisonRow,
    ProfileComparisonSummary,
)


# ── Journal record → DecisionEvent ────────────────────────────────────────────

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


def record_to_decision_event(rec: dict) -> Optional[DecisionEvent]:
    """
    Convert a raw journal record dict to a DecisionEvent.

    Returns None if the record is malformed (missing required fields).
    """
    ds = rec.get("decision", {})
    sig = rec.get("signal", {})
    pricing = rec.get("pricing", {})

    ts = _parse_dt(rec.get("ts_recorded_utc"))
    if ts is None:
        return None

    decision = ds.get("decision", "REJECT")
    # Normalize: "EXECUTE_YES" and "EXECUTE_NO" are both "EXECUTE" variants
    # keep as-is for the dashboard

    return DecisionEvent(
        record_id=rec.get("record_id", ""),
        run_id=rec.get("run_id", ""),
        timestamp_utc=ts,
        asset=sig.get("asset", ""),
        horizon_minutes=sig.get("horizon_minutes", 0),
        predicted_class=sig.get("predicted_class", ""),
        calibration_quality=sig.get("calibration_quality", "unknown"),
        calibration_method=sig.get("calibration_method", "identity"),
        effective_yes_prob=sig.get("effective_yes_prob", 0.0),
        effective_no_prob=sig.get("effective_no_prob", 0.0),
        bridge_intent_side=sig.get("bridge_intent_side", "YES"),
        market_id=pricing.get("market_id", ""),
        ask_yes=pricing.get("ask_yes", 0.0),
        ask_no=pricing.get("ask_no", 0.0),
        snapshot_age_seconds=pricing.get("snapshot_age_seconds", 0.0),
        policy_profile=rec.get("policy_profile", "paper_loose"),
        intended_size_usdc=rec.get("intended_size_usdc", 20.0),
        final_decision=decision,
        rejection_reason=ds.get("rejection_reason"),
        passes_final_gate=ds.get("passes_final_gate", False),
        gross_ev=ds.get("gross_ev"),
        net_ev_after_fee=ds.get("net_ev_after_fee"),
        execution_adjusted_ev=ds.get("execution_adjusted_ev"),
        required_edge_threshold=ds.get("required_edge_threshold"),
        fill_fraction=ds.get("fill_fraction"),
        pricing_sanity_notes=ds.get("pricing_sanity_notes"),
        evidence_source=rec.get("evidence_source", "live_shadow"),
    )


# ── Profile comparison ────────────────────────────────────────────────────────

def build_profile_comparison(records: list[dict]) -> ProfileComparisonSummary:
    """
    Compute cross-profile comparison from raw journal records.

    Groups records by (run_id, market_id) to find same-candidate
    decisions under different profiles.
    """
    # Per-profile stats
    profile_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "execute": 0, "reject": 0})
    for r in records:
        profile = r.get("policy_profile", "unknown")
        ps = profile_stats[profile]
        ps["total"] += 1
        decision = r.get("decision", {}).get("decision", "REJECT")
        if decision != "REJECT":
            ps["execute"] += 1
        else:
            ps["reject"] += 1

    per_profile: dict = {}
    for profile, ps in profile_stats.items():
        n = ps["total"]
        per_profile[profile] = {
            "total": n,
            "execute": ps["execute"],
            "reject": ps["reject"],
            "exec_rate": ps["execute"] / n if n > 0 else 0.0,
        }

    # Group by (run_id, market_id) for cross-profile rows
    groups: dict[str, dict] = defaultdict(dict)
    for r in records:
        run_id    = r.get("run_id", "")
        market_id = r.get("pricing", {}).get("market_id", "")
        asset     = r.get("signal", {}).get("asset", "")
        key       = f"{run_id}::{market_id}"
        profile   = r.get("policy_profile", "unknown")
        groups[key][profile] = r

    # Build divergent rows
    divergent_rows: list[ProfileComparisonRow] = []
    loose_only_count  = 0
    strict_not_live   = 0

    for key, profile_map in groups.items():
        if len(profile_map) < 2:
            continue

        live_rec   = profile_map.get("live")
        strict_rec = profile_map.get("paper_strict")
        loose_rec  = profile_map.get("paper_loose")

        def _dec(rec: Optional[dict]) -> Optional[str]:
            if rec is None:
                return None
            return rec.get("decision", {}).get("decision")

        def _ev(rec: Optional[dict]) -> Optional[float]:
            if rec is None:
                return None
            return rec.get("decision", {}).get("execution_adjusted_ev")

        live_d   = _dec(live_rec)
        strict_d = _dec(strict_rec)
        loose_d  = _dec(loose_rec)

        live_exec   = live_d   is not None and live_d   != "REJECT"
        strict_exec = strict_d is not None and strict_d != "REJECT"
        loose_exec  = loose_d  is not None and loose_d  != "REJECT"

        # Determine divergence type
        all_agree = (live_d == strict_d == loose_d) or len(profile_map) == 1
        loose_only = loose_exec and not live_exec and not strict_exec
        strict_not_live_case = strict_exec and not live_exec

        if loose_only:
            loose_only_count += 1
            dtype = "LOOSE_ONLY"
        elif strict_not_live_case:
            strict_not_live += 1
            dtype = "STRICT_REJECTS_LIVE_PASS"
        elif all_agree:
            dtype = "ALL_AGREE"
        else:
            dtype = "PARTIAL"

        # Only include non-trivial rows in the divergent list
        if dtype not in ("ALL_AGREE",):
            # pick any record for timestamp/asset/market
            sample = live_rec or strict_rec or loose_rec
            ts = _parse_dt(sample.get("ts_recorded_utc")) if sample else datetime.now(timezone.utc)
            divergent_rows.append(ProfileComparisonRow(
                record_group_key=key,
                timestamp_utc=ts or datetime.now(timezone.utc),
                asset=sample.get("signal", {}).get("asset", "") if sample else "",
                market_id=sample.get("pricing", {}).get("market_id", "") if sample else "",
                live_decision=live_d,
                strict_decision=strict_d,
                loose_decision=loose_d,
                live_ev=_ev(live_rec),
                strict_ev=_ev(strict_rec),
                loose_ev=_ev(loose_rec),
                divergence_type=dtype,
            ))

    # Sort divergent rows most-recent first
    divergent_rows.sort(key=lambda r: r.timestamp_utc, reverse=True)

    return ProfileComparisonSummary(
        profiles_found=list(profile_stats.keys()),
        per_profile=per_profile,
        divergent_rows=divergent_rows[:100],   # cap at 100 rows for dashboard
        loose_only_count=loose_only_count,
        strict_not_live_count=strict_not_live,
        total_records=len(records),
    )


# ── Decision chain (drilldown) ────────────────────────────────────────────────

def build_decision_chain(rec: dict) -> DecisionChain:
    """Build a DecisionChain from a raw journal record for drilldown view."""
    sig     = rec.get("signal", {})
    pricing = rec.get("pricing", {})
    ds      = rec.get("decision", {})
    ts      = _parse_dt(rec.get("ts_recorded_utc")) or datetime.now(timezone.utc)

    return DecisionChain(
        record_id=rec.get("record_id", ""),
        timestamp_utc=ts,
        asset=sig.get("asset", ""),
        market_id=pricing.get("market_id", ""),
        bridge_step={
            "intent_side":       sig.get("bridge_intent_side", "YES"),
            "mapping_context":   sig.get("mapping_context", ""),
            "effective_yes_prob": sig.get("effective_yes_prob", 0.0),
            "effective_no_prob":  sig.get("effective_no_prob", 0.0),
        },
        calibration_step={
            "method":               sig.get("calibration_method", "identity"),
            "quality":              sig.get("calibration_quality", "unknown"),
            "calibrated_up_prob":   sig.get("calibrated_up_prob", 0.0),
            "calibrated_down_prob": sig.get("calibrated_down_prob", 0.0),
            "brier_score":          sig.get("brier_score"),
            "ece":                  sig.get("ece"),
        },
        pricing_step={
            "ask_yes":              pricing.get("ask_yes", 0.0),
            "bid_yes":              pricing.get("bid_yes", 0.0),
            "ask_no":               pricing.get("ask_no", 0.0),
            "bid_no":               pricing.get("bid_no", 0.0),
            "snapshot_age_seconds": pricing.get("snapshot_age_seconds", 0.0),
            "sanity_notes":         ds.get("pricing_sanity_notes"),
        },
        ev_step={
            "gross_ev":              ds.get("gross_ev"),
            "net_ev_after_fee":      ds.get("net_ev_after_fee"),
            "execution_adjusted_ev": ds.get("execution_adjusted_ev"),
            "required_threshold":    ds.get("required_edge_threshold"),
            "fill_fraction":         ds.get("fill_fraction"),
        },
        policy_step={
            "profile":          rec.get("policy_profile", ""),
            "passes_final_gate": ds.get("passes_final_gate", False),
            "final_decision":    ds.get("decision", "REJECT"),
            "rejection_reason":  ds.get("rejection_reason"),
        },
        replay_available=rec.get("evidence_source", "live_shadow") == "live_shadow",
    )


# ── Main summary builder ──────────────────────────────────────────────────────

def build_chamber_summary(max_decisions: int = 50) -> ChamberSummary:
    """
    Build the full ChamberSummary payload for the dashboard.

    Parameters
    ----------
    max_decisions : int
        How many recent decisions to include in the feed.

    Returns
    -------
    ChamberSummary
        Fully assembled operator view. All fields sourced from real data.
    """
    # ── Read all sources ───────────────────────────────────────────────────────
    positions_data  = read_positions_ledger()
    status_data     = read_status_snapshot()
    control_data    = read_control_state()
    verdict_data    = read_readiness_verdict()
    journal_files   = find_journal_files()
    integrity_stats = read_journal_integrity_stats(journal_files)
    # Read more records for profile comparison; cap decisions display separately
    all_records     = read_journal_records(max_records=1000, journal_files=journal_files)

    # ── Build positions and equity ─────────────────────────────────────────────
    open_positions = build_open_positions(positions_data["positions"])
    closed_trades  = build_closed_trades(positions_data["closed"])
    equity         = build_equity_state(positions_data, status_data, open_positions, closed_trades)

    # ── Build health ───────────────────────────────────────────────────────────
    health = build_health_state(integrity_stats, all_records, status_data, control_data)

    # ── Build readiness ────────────────────────────────────────────────────────
    readiness = build_readiness_state(verdict_data, all_records)

    # ── Build decision events ──────────────────────────────────────────────────
    recent_decisions: list[DecisionEvent] = []
    for rec in all_records[:max_decisions]:
        event = record_to_decision_event(rec)
        if event is not None:
            recent_decisions.append(event)

    # ── Build profile comparison ───────────────────────────────────────────────
    profile_comparison = build_profile_comparison(all_records)

    # ── Environment / mode ────────────────────────────────────────────────────
    live_trading = control_data.get("live_trading", False)
    sim_running  = control_data.get("simulation_running", False)
    running      = status_data.get("running", False)

    if live_trading:
        environment = "live"
    elif sim_running or running:
        environment = "simulation"
    else:
        environment = "stopped"

    active_profile = "live" if live_trading else "paper_loose"

    return ChamberSummary(
        generated_utc=datetime.now(timezone.utc),
        equity=equity,
        health=health,
        readiness=readiness,
        recent_decisions=recent_decisions,
        open_positions=open_positions,
        closed_trades=closed_trades,
        profile_comparison=profile_comparison,
        environment=environment,
        active_profile_mode=active_profile,
        journal_files_found=len(journal_files),
        total_shadow_records=len(all_records),
    )


def get_decision_chain(record_id: str) -> Optional[DecisionChain]:
    """
    Retrieve a single decision chain by record_id for drilldown.

    Scans journal files for the matching record.
    Returns None if not found.
    """
    for rec in read_journal_records(max_records=10000):
        if rec.get("record_id") == record_id:
            return build_decision_chain(rec)
    return None
