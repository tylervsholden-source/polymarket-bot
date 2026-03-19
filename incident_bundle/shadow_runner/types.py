"""
shadow_runner/types.py

Phase 13: Data models for shadow execution, journaling, and drift monitoring.

Design decisions:
  1. ShadowDecisionRecord carries ALL information needed to:
       - audit the decision
       - replay it (reconstruct inputs)
       - compute drift metrics
  2. Every candidate is recorded, whether EXECUTE or REJECT.
     Early rejects carry signal about what the pipeline is filtering out.
  3. Policy profile is recorded explicitly — not inferred from rejection_reason.
  4. snapshot_age_seconds is stored at record time (not re-derived on replay)
     so the replay can reconstruct exact staleness logic.
  5. ShadowRunConfig is metadata only — it does NOT hold the CalibrationConfig objects.
     The configs are passed to ShadowRunner at construction time.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


JOURNAL_SCHEMA_VERSION = "1"


# ── Input carriers ────────────────────────────────────────────────────────────

@dataclass
class SignalSnapshot:
    """
    Minimal, serialization-safe view of CalibratedSignal.

    Stores every field needed to reconstruct a CalibratedSignal for replay.
    All probabilities are floats in [0, 1].
    """
    # RawSignalOutput fields
    asset:                    str
    horizon_minutes:          int
    signal_timestamp_utc:     datetime
    predicted_class:          str              # "UP" | "DOWN" | "NO_TRADE"
    raw_confidence:           float
    class_probabilities:      Optional[dict]   # {"UP": 0.72, ...} or None
    model_version:            str              = "v0"

    # CalibratedSignal fields
    calibrated_up_prob:       float            = 0.0
    calibrated_down_prob:     float            = 0.0
    calibrated_no_trade_prob: float            = 0.0
    calibration_method:       str              = "identity"  # CalibrationMethod.value
    calibration_quality:      str              = "unknown"   # CalibrationQuality.value
    effective_yes_prob:       float            = 0.0
    effective_no_prob:        float            = 0.0
    mapping_context:          str              = ""
    bridge_intent_side:       str              = "YES"       # "YES" | "NO"
    brier_score:              Optional[float]  = None
    ece:                      Optional[float]  = None


@dataclass
class PricingSnapshot:
    """
    Serialization-safe view of MarketPricingSnapshot + derived staleness.
    """
    market_id:            str
    ask_yes:              float
    bid_yes:              float
    ask_no:               float
    bid_no:               float
    liquidity:            float
    pricing_timestamp_utc: datetime
    snapshot_age_seconds: float   # age at the moment decide() was called


# ── Decision result ───────────────────────────────────────────────────────────

@dataclass
class DecisionSummary:
    """
    Summary of TradeDecision output for journaling.
    Only scalar / string fields — no nested objects.
    """
    decision:                str             # TradeDecisionType.value
    rejection_reason:        Optional[str]   # CalibrationRejectionReason.value or None
    policy_mode:             str             # "live" | "paper_strict" | "paper_loose" | ...
    passes_final_gate:       bool
    intended_size_usdc_used: float
    pricing_sanity_notes:    Optional[str]   = None
    # Edge metrics — None if decision was REJECT before edge gate
    gross_ev:                Optional[float] = None
    net_ev_after_fee:        Optional[float] = None
    execution_adjusted_ev:   Optional[float] = None
    required_edge_threshold: Optional[float] = None
    fill_fraction:           Optional[float] = None


# ── Core journal record ───────────────────────────────────────────────────────

@dataclass
class ShadowDecisionRecord:
    """
    A single shadow run decision — one candidate × one policy profile.

    Immutable once created. Written to journal as one JSONL line.

    Fields that enable replay:
        signal, pricing, policy_profile, intended_size_usdc
        (these three reconstruct the exact decide() call)

    Fields that enable drift monitoring:
        decision, rejection_reason, execution_adjusted_ev
    """
    record_id:          str              # uuid4 — unique per record
    run_id:             str              # uuid4 — shared across all records in one runner.run() call
    ts_recorded_utc:    datetime         # wall clock at journal write time
    schema_version:     str              = JOURNAL_SCHEMA_VERSION

    # Inputs (for replay)
    signal:             SignalSnapshot   = field(default_factory=SignalSnapshot.__new__)
    pricing:            PricingSnapshot  = field(default_factory=PricingSnapshot.__new__)
    policy_profile:     str              = "paper_loose"  # config.mode value
    intended_size_usdc: float            = 20.0

    # Evidence provenance — used by readiness gate to reject non-live corpora
    # "live_shadow" : real market data, real-time shadow run (valid evidence)
    # "demo"        : demo/testnet run or replay with live data but intentional flag
    # "synthetic"   : generated from fixtures or test data (never valid evidence)
    evidence_source:    str              = "live_shadow"

    # Output
    decision_summary:   DecisionSummary  = field(default_factory=DecisionSummary.__new__)


# ── Run configuration ─────────────────────────────────────────────────────────

@dataclass
class ShadowRunConfig:
    """
    Metadata for a shadow run session.

    Does not hold CalibrationConfig objects — those are passed to ShadowRunner
    at construction and re-used for each candidate in the session.
    """
    run_id:           str      = field(default_factory=lambda: str(uuid.uuid4()))
    description:      str      = ""
    runner_version:   str      = "phase13.1"
    profile_names:    list     = field(default_factory=list)  # ["live", "paper_strict", ...]
    created_utc:      Optional[datetime] = None


# ── Replay result ─────────────────────────────────────────────────────────────

@dataclass
class ReplayResult:
    """
    Result of replaying a single ShadowDecisionRecord through the live decide() pipeline.
    """
    record_id:          str
    original_decision:  str         # from journal
    replayed_decision:  str         # from replay
    original_rejection: Optional[str]
    replayed_rejection: Optional[str]
    match:              bool        # True iff decision + rejection_reason both match
    mismatch_reason:    Optional[str] = None


# ── Drift monitoring types ─────────────────────────────────────────────────────

@dataclass
class RejectionMetrics:
    """Rejection rate and reason breakdown for a window of records."""
    total_records:         int
    execute_count:         int
    reject_count:          int
    rejection_rate:        float                  # reject_count / total
    reason_counts:         dict                   # {reason_str: int}
    policy_profile:        str


@dataclass
class EVMetrics:
    """EV distribution for EXECUTE decisions in a window."""
    execute_count:         int
    mean_exec_adj_ev:      float
    p25_exec_adj_ev:       float
    p75_exec_adj_ev:       float
    policy_profile:        str


@dataclass
class ProfileDivergenceMetrics:
    """
    Between-profile divergence: fraction of candidates where profiles disagree.

    Computed for pairs (e.g., live vs paper_strict, paper_strict vs paper_loose).
    """
    profile_a:             str
    profile_b:             str
    total_shared:          int   # candidates evaluated by both
    agreement_count:       int   # same TradeDecisionType
    divergence_rate:       float  # 1 - (agreement / total)


@dataclass
class DriftReport:
    """
    Output of DriftMonitor.check().

    Contains per-profile rejection and EV metrics for baseline and current windows,
    plus computed drift magnitudes.
    """
    baseline_window_size:  int
    current_window_size:   int
    profiles_checked:      list   # list of policy_mode strings

    # Per profile: {policy_mode: RejectionMetrics}
    baseline_rejection:    dict
    current_rejection:     dict
    # Rejection rate drift: {policy_mode: float}  (current_rate - baseline_rate)
    rejection_rate_delta:  dict

    # Per profile: {policy_mode: EVMetrics}  (None if no executes)
    baseline_ev:           dict
    current_ev:            dict
    # EV mean drift: {policy_mode: float}
    ev_mean_delta:         dict

    # Profile divergence — both baseline and current, plus delta
    # baseline_divergence: {"{profile_a}/{profile_b}": divergence_rate}
    baseline_divergence:   dict
    divergence:            list   # list[ProfileDivergenceMetrics] — current window
    # Divergence delta: {pair_label: current_rate - baseline_rate}
    divergence_delta:      dict

    ts_computed_utc:       datetime


@dataclass
class DriftAlert:
    """A single triggered alert from drift monitoring."""
    level:                 str        # "WARNING" | "CRITICAL"
    metric:                str        # e.g. "rejection_rate", "ev_mean"
    policy_profile:        str
    message:               str
    baseline_value:        float
    current_value:         float
    delta:                 float
    threshold:             float
