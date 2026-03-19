"""
operator_layer/types.py

Architect Chamber — operator-facing data contracts.

DESIGN RULES:
  1. Every field maps to a real data source (journal, positions.json, status.json,
     readiness_verdict.json). No invented values.
  2. Fields that are derived or computed are documented as such.
  3. Fields that are shadow-only (not real money) are tagged with a source_type.
  4. Optional fields are None when the underlying data is not available —
     never defaulted to 0 when missing would be misleading.

DATA SOURCES:
  - positions.json      → OpenPosition, ClosedTrade, EquityState
  - status.json         → SystemStatus (real-time cycle state)
  - shadow_journal_*.jsonl → DecisionEvent (one per journal record)
  - readiness_verdict.json → ReadinessState
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ── Decision Events ───────────────────────────────────────────────────────────

@dataclass
class DecisionEvent:
    """
    Single decision record as seen by the operator.

    Source: shadow_journal_YYYY-MM-DD.jsonl (one ShadowDecisionRecord → one DecisionEvent).

    Shadow-only: execution_adjusted_ev, fill_fraction, gross_ev fields describe
    what WOULD happen — no real money has moved.
    """
    # Identity
    record_id:               str
    run_id:                  str
    timestamp_utc:           datetime

    # Signal
    asset:                   str
    horizon_minutes:         int
    predicted_class:         str              # "UP" | "DOWN" | "NO_TRADE"
    calibration_quality:     str              # "good" | "moderate" | "weak" | "unknown"
    calibration_method:      str
    effective_yes_prob:      float
    effective_no_prob:       float
    bridge_intent_side:      str              # "YES" | "NO"

    # Market
    market_id:               str
    ask_yes:                 float
    ask_no:                  float
    snapshot_age_seconds:    float

    # Policy
    policy_profile:          str              # "live" | "paper_strict" | "paper_loose"
    intended_size_usdc:      float

    # Decision
    final_decision:          str              # "EXECUTE_YES" | "EXECUTE_NO" | "REJECT"
    rejection_reason:        Optional[str]    # e.g. "LOW_EDGE", "STALE_PRICING"
    passes_final_gate:       bool

    # EV (shadow-only — describes simulated execution)
    gross_ev:                Optional[float]
    net_ev_after_fee:        Optional[float]
    execution_adjusted_ev:   Optional[float]
    required_edge_threshold: Optional[float]
    fill_fraction:           Optional[float]

    # Flags
    pricing_sanity_notes:    Optional[str]    # set when suspicious pricing detected in paper_loose
    evidence_source:         str              # "live_shadow" | "demo" | "synthetic"


# ── Order Ledger ──────────────────────────────────────────────────────────────

@dataclass
class OrderLedgerEntry:
    """
    Single order record.

    Source: status.json orders array.
    Note: status.json holds only the last ~15 orders. For full history,
    the shadow journal is the authoritative audit trail.
    """
    order_id:               str
    market:                 str
    outcome:                str               # "YES" | "NO" | "UP" | "DOWN"
    amount:                 float
    price:                  float
    edge:                   float
    status:                 str               # "MATCHED" | "PENDING" | ...
    time:                   str               # ISO string from status.json


# ── Position Ledger ───────────────────────────────────────────────────────────

@dataclass
class OpenPosition:
    """
    Single open position.

    Source: positions.json → positions dict.
    unrealized_pnl is current_value - amount (derived from live price feed).
    current_mark sourced from positions.json current_price field.
    """
    position_id:            str               # market_id used as key
    market_id:              str
    question:               str
    outcome:                str               # "YES" | "NO" | "UP" | "DOWN"
    amount:                 float             # size in USDC
    entry_price:            float
    current_mark:           Optional[float]   # current price (None if not updated)
    current_value:          Optional[float]   # current_value from positions.json
    unrealized_pnl:         Optional[float]   # current_value - amount
    end_date_iso:           Optional[str]
    status:                 str               # "MATCHED" | "OPEN" | ...
    order_id:               str

    # Derived
    age_seconds:            Optional[float]   = None   # computed at view time
    risk_flags:             list              = field(default_factory=list)


# ── Closed Trade Ledger ───────────────────────────────────────────────────────

@dataclass
class ClosedTrade:
    """
    Single closed trade.

    Source: positions.json → closed array.

    Note: positions.json does NOT store the original open timestamp.
    opened_at is None for historical trades pre-dating the timestamp field.
    """
    trade_id:               str               # order_id
    market_id:              Optional[str]     # not always stored in closed array
    question:               str
    outcome:                str
    amount:                 float
    entry_price:            float
    close_price:            float
    realized_pnl:           float
    pnl_pct:                Optional[float]   # realized_pnl / amount * 100 (derived)
    result:                 str               # "WIN" | "LOSS" | "NEUTRAL"
    status:                 str               # "MATCHED" | ...
    payout:                 float

    # Not available from positions.json (would require join with shadow journal)
    opened_at:              Optional[datetime] = None
    closed_at:              Optional[datetime] = None
    original_policy_profile: Optional[str]    = None
    original_decision_id:   Optional[str]     = None
    close_reason:           Optional[str]     = None


# ── Equity / Capital State ────────────────────────────────────────────────────

@dataclass
class EquityState:
    """
    Portfolio-level capital and PnL summary.

    Source: positions.json + status.json.
    unrealized_pnl is sum of open position unrealized_pnl values.
    realized_pnl_day is positions.json → daily.pnl.
    realized_pnl_total is sum of closed trades pnl (derived).
    """
    timestamp_utc:           datetime
    cash_available:          float             # capital - sum(open amounts)
    capital_committed:       float             # sum(open position amounts)
    unrealized_pnl:          float             # sum of open position unrealized_pnl
    realized_pnl_day:        float             # from positions.json daily.pnl
    realized_pnl_total:      float             # sum of all closed pnl (derived)
    total_equity:            float             # cash_available + capital_committed + unrealized_pnl
    active_positions_count:  int
    initial_capital:         Optional[float]   = None   # from status.json
    blocked_reason:          Optional[str]     = None   # e.g. "DAILY_STOP_LOSS"


# ── Health / Alert State ──────────────────────────────────────────────────────

@dataclass
class HealthAlert:
    """A single health alert."""
    level:          str           # "WARNING" | "CRITICAL" | "INFO"
    category:       str           # "DRIFT" | "PRICING" | "JOURNAL" | "CYCLE" | "POSITION"
    message:        str
    metric_value:   Optional[float] = None
    threshold:      Optional[float] = None


@dataclass
class HealthState:
    """
    System health summary for the operator.

    Source:
      - journal integrity: JournalReader.read_all_with_integrity() stats
      - drift alerts: DriftMonitor (when daily_review runs)
      - suspicious_underround_rate: computed from journal records
      - stale_pricing_rate: computed from journal records
      - partial_fill_rate: computed from journal records
      - last_updated: wall clock at aggregation time
    """
    journal_healthy:             bool
    journal_bad_line_fraction:   Optional[float]    # from JournalIntegrityStats
    journal_total_lines:         Optional[int]

    drift_alerts:                list               # list[HealthAlert] — from DriftMonitor
    pricing_sanity_alerts:       list               # list[HealthAlert] — suspicious underround etc.

    # Rates computed from recent shadow records
    suspicious_underround_rate:  Optional[float]    # fraction with pricing_sanity_notes set
    stale_pricing_rate:          Optional[float]    # fraction with snapshot_age > threshold
    partial_fill_rate:           Optional[float]    # fraction with fill_fraction < 1.0

    # Policy divergence
    policy_divergence_alerts:    list               # list[HealthAlert]

    # Cycle health
    cycle_running:               bool
    last_cycle_at:               Optional[str]      # from status.json updated field
    cycles_completed:            Optional[int]      # from status.json cycle field

    # Blockers active
    blocker_active:              bool
    blocker_reasons:             list               # list[str]

    last_updated_utc:            datetime


# ── Readiness State ───────────────────────────────────────────────────────────

@dataclass
class ReadinessCheckRow:
    """A single readiness check result for display."""
    name:           str
    level:          str           # "OK" | "WARN" | "FAIL" | "BLOCKER"
    metric_value:   Optional[float]
    message:        str


@dataclass
class ProfileReadinessSummary:
    """Per-profile statistics for readiness panel."""
    profile:                str
    total_evaluated:        int
    execute_count:          int
    reject_count:           int
    execution_rate:         float
    mean_ev:                Optional[float]
    top_rejection_reasons:  list              # list[tuple[str, int]]


@dataclass
class ReadinessState:
    """
    Readiness verdict as seen by the operator.

    Source: data/readiness_verdict.json (written by monitoring/daily_review.py).
    If the file does not exist, status is "NOT_REVIEWED".

    pilot_constraints describes what a constrained pilot would look like
    (always present — even on NO_GO, so operator can plan).
    """
    status:                  str               # "TINY_PILOT_CANDIDATE" | "CONDITIONAL_REVIEW" | "NO_GO" | "INSUFFICIENT_EVIDENCE" | "NOT_REVIEWED"
    verdict_reason:          str
    evidence_sufficient:     bool
    last_reviewed_at:        Optional[datetime]

    blocker_count:           int
    fail_count:              int
    warn_count:              int

    checks:                  list              # list[ReadinessCheckRow]
    blockers:                list              # list[ReadinessCheckRow]
    fails:                   list              # list[ReadinessCheckRow]
    warns:                   list              # list[ReadinessCheckRow]

    # Evidence sufficiency numbers
    live_like_evaluated:     int
    live_like_executes:      int
    live_like_rejects:       int
    observation_days:        float
    evidence_gaps:           list              # list[str] — human messages

    # Per-profile summaries
    live_summary:            Optional[ProfileReadinessSummary]
    strict_summary:          Optional[ProfileReadinessSummary]
    loose_summary:           Optional[ProfileReadinessSummary]

    # Pilot constraints (always present)
    pilot_max_positions:     int
    pilot_asset:             str
    pilot_horizon_min:       int
    pilot_max_usdc:          float
    pilot_daily_loss_usdc:   float
    pilot_kill_usdc:         float


# ── Profile Comparison ────────────────────────────────────────────────────────

@dataclass
class ProfileComparisonRow:
    """
    A decision that diverges across profiles — useful for operator review.

    Source: shadow journal records grouped by run_id + market_id.
    """
    record_group_key:        str               # run_id + market_id + asset
    timestamp_utc:           datetime
    asset:                   str
    market_id:               str
    live_decision:           Optional[str]     # EXECUTE_YES / EXECUTE_NO / REJECT / None
    strict_decision:         Optional[str]
    loose_decision:          Optional[str]
    live_ev:                 Optional[float]
    strict_ev:               Optional[float]
    loose_ev:                Optional[float]
    divergence_type:         str               # "LOOSE_ONLY" | "STRICT_REJECTS_LIVE_PASS" | "ALL_AGREE" | "PARTIAL"


@dataclass
class ProfileComparisonSummary:
    """
    Cross-profile statistics summary.

    Source: all shadow journal records.
    """
    profiles_found:          list              # list[str]
    per_profile:             dict              # {profile_name: {"total": int, "execute": int, "reject": int, "exec_rate": float}}
    divergent_rows:          list              # list[ProfileComparisonRow]
    loose_only_count:        int               # passed only in paper_loose
    strict_not_live_count:   int               # paper_strict rejects but live passes (rare)
    total_records:           int


# ── Decision Chain (audit drilldown) ─────────────────────────────────────────

@dataclass
class DecisionChain:
    """
    Full audit chain for a single decision — used in drilldown panel.

    Source: a single ShadowDecisionRecord from journal.
    Shows each step in the decision pipeline explicitly.
    """
    record_id:          str
    timestamp_utc:      datetime
    asset:              str
    market_id:          str

    # Step 1: Bridge mapping
    bridge_step:        dict    # {intent_side, mapping_context, effective_yes_prob, effective_no_prob}

    # Step 2: Calibration
    calibration_step:   dict    # {method, quality, calibrated_up_prob, calibrated_down_prob, brier_score, ece}

    # Step 3: Market pricing sanity
    pricing_step:       dict    # {ask_yes, ask_no, bid_yes, bid_no, snapshot_age_seconds, sanity_notes}

    # Step 4: EV / edge computation
    ev_step:            dict    # {gross_ev, net_ev_after_fee, execution_adjusted_ev, required_threshold, fill_fraction}

    # Step 5: Policy gate
    policy_step:        dict    # {profile, passes_final_gate, final_decision, rejection_reason}

    # Replay reference
    replay_available:   bool    # True if record has evidence_source = "live_shadow"


# ── Top-level Chamber Summary ─────────────────────────────────────────────────

@dataclass
class ChamberSummary:
    """
    Single payload returned by /api/chamber/summary.

    The dashboard fetches this every N seconds.
    All sub-components are independently nullable if their data source
    is unavailable (e.g. no journal yet, no readiness review yet).
    """
    generated_utc:          datetime

    # Real-time state
    equity:                 EquityState
    health:                 HealthState
    readiness:              ReadinessState

    # Recent decisions (last 50, most recent first)
    recent_decisions:       list              # list[DecisionEvent]

    # Open positions
    open_positions:         list              # list[OpenPosition]

    # Closed trades (most recent first)
    closed_trades:          list              # list[ClosedTrade]

    # Profile comparison
    profile_comparison:     ProfileComparisonSummary

    # System metadata
    environment:            str               # "live" | "simulation" | "stopped"
    active_profile_mode:    str               # current control.json / orchestrator mode
    journal_files_found:    int               # how many shadow journal files exist
    total_shadow_records:   int               # total records across all journal files
