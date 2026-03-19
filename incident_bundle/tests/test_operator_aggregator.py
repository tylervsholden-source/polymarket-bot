"""
tests/test_operator_aggregator.py

Tests for operator_layer/aggregator.py.

Covers:
- record_to_decision_event: correct field mapping, None on bad record
- build_profile_comparison: per-profile stats, loose_only_count, divergent rows
- build_decision_chain: all 5 chain steps populated correctly
- Missing/partial source data handled without crash
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from operator_layer.aggregator import (
    build_decision_chain,
    build_profile_comparison,
    record_to_decision_event,
)
from operator_layer.types import DecisionChain, DecisionEvent, ProfileComparisonSummary


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _make_record(
    decision: str = "REJECT",
    profile: str = "live",
    asset: str = "BTC",
    market_id: str = "mkt-001",
    run_id: str = "run-001",
    rejection_reason: str = "LOW_EDGE",
    ev: float = None,
    ts: str = "2026-03-15T12:00:00Z",
    evidence_source: str = "live_shadow",
) -> dict:
    return {
        "record_id": str(uuid.uuid4()),
        "run_id": run_id,
        "ts_recorded_utc": ts,
        "schema_version": "1",
        "policy_profile": profile,
        "intended_size_usdc": 20.0,
        "evidence_source": evidence_source,
        "signal": {
            "asset": asset,
            "horizon_minutes": 15,
            "signal_timestamp_utc": ts,
            "predicted_class": "UP",
            "raw_confidence": 0.72,
            "class_probabilities": {"UP": 0.72, "DOWN": 0.18, "NO_TRADE": 0.10},
            "model_version": "v0",
            "calibrated_up_prob": 0.68,
            "calibrated_down_prob": 0.22,
            "calibrated_no_trade_prob": 0.10,
            "calibration_method": "platt",
            "calibration_quality": "good",
            "effective_yes_prob": 0.68,
            "effective_no_prob": 0.32,
            "mapping_context": "UP→YES",
            "bridge_intent_side": "YES",
            "brier_score": 0.18,
            "ece": 0.04,
        },
        "pricing": {
            "market_id": market_id,
            "ask_yes": 0.55,
            "bid_yes": 0.54,
            "ask_no": 0.46,
            "bid_no": 0.45,
            "liquidity": 5000.0,
            "pricing_timestamp_utc": ts,
            "snapshot_age_seconds": 5.0,
        },
        "decision": {
            "decision": decision,
            "rejection_reason": rejection_reason if decision == "REJECT" else None,
            "policy_mode": profile,
            "passes_final_gate": decision != "REJECT",
            "intended_size_usdc_used": 20.0,
            "pricing_sanity_notes": None,
            "gross_ev": ev,
            "net_ev_after_fee": ev,
            "execution_adjusted_ev": ev,
            "required_edge_threshold": 0.03,
            "fill_fraction": 1.0 if decision != "REJECT" else None,
        },
    }


# ── record_to_decision_event ───────────────────────────────────────────────────

class TestRecordToDecisionEvent:
    def test_reject_record_maps_correctly(self):
        rec = _make_record(decision="REJECT", profile="live", asset="BTC")
        ev = record_to_decision_event(rec)
        assert ev is not None
        assert isinstance(ev, DecisionEvent)
        assert ev.final_decision == "REJECT"
        assert ev.asset == "BTC"
        assert ev.policy_profile == "live"
        assert ev.market_id == "mkt-001"
        assert ev.rejection_reason == "LOW_EDGE"
        assert ev.passes_final_gate is False

    def test_execute_yes_record_maps_ev(self):
        rec = _make_record(decision="EXECUTE_YES", profile="paper_loose", ev=0.047)
        ev = record_to_decision_event(rec)
        assert ev is not None
        assert ev.final_decision == "EXECUTE_YES"
        assert abs(ev.execution_adjusted_ev - 0.047) < 1e-9
        assert ev.passes_final_gate is True
        assert ev.rejection_reason is None

    def test_missing_ts_returns_none(self):
        rec = _make_record()
        rec["ts_recorded_utc"] = None
        assert record_to_decision_event(rec) is None

    def test_bad_ts_returns_none(self):
        rec = _make_record()
        rec["ts_recorded_utc"] = "not-a-date"
        assert record_to_decision_event(rec) is None

    def test_evidence_source_preserved(self):
        rec = _make_record(evidence_source="synthetic")
        ev = record_to_decision_event(rec)
        assert ev.evidence_source == "synthetic"

    def test_signal_fields_populated(self):
        rec = _make_record()
        ev = record_to_decision_event(rec)
        assert ev.calibration_quality == "good"
        assert ev.calibration_method == "platt"
        assert abs(ev.effective_yes_prob - 0.68) < 1e-9
        assert ev.bridge_intent_side == "YES"
        assert ev.horizon_minutes == 15

    def test_pricing_fields_populated(self):
        rec = _make_record()
        ev = record_to_decision_event(rec)
        assert abs(ev.ask_yes - 0.55) < 1e-9
        assert abs(ev.snapshot_age_seconds - 5.0) < 1e-9


# ── build_profile_comparison ───────────────────────────────────────────────────

class TestBuildProfileComparison:
    def _make_trio(self, run_id: str, market_id: str, ts: str,
                   live_dec="REJECT", strict_dec="REJECT", loose_dec="EXECUTE_YES",
                   live_ev=None, strict_ev=None, loose_ev=0.04):
        return [
            _make_record(decision=live_dec,   profile="live",         market_id=market_id, run_id=run_id, ts=ts, ev=live_ev),
            _make_record(decision=strict_dec, profile="paper_strict", market_id=market_id, run_id=run_id, ts=ts, ev=strict_ev),
            _make_record(decision=loose_dec,  profile="paper_loose",  market_id=market_id, run_id=run_id, ts=ts, ev=loose_ev),
        ]

    def test_per_profile_totals(self):
        records = (
            self._make_trio("r1", "m1", "2026-03-15T10:00:00Z") +
            self._make_trio("r2", "m2", "2026-03-15T11:00:00Z")
        )
        cmp = build_profile_comparison(records)
        assert cmp.per_profile["live"]["total"] == 2
        assert cmp.per_profile["paper_loose"]["total"] == 2
        assert cmp.per_profile["paper_loose"]["execute"] == 2
        assert cmp.per_profile["live"]["execute"] == 0

    def test_loose_only_counted(self):
        records = self._make_trio("r1", "m1", "2026-03-15T10:00:00Z",
                                  live_dec="REJECT", strict_dec="REJECT", loose_dec="EXECUTE_YES")
        cmp = build_profile_comparison(records)
        assert cmp.loose_only_count >= 1

    def test_all_agree_execute(self):
        records = self._make_trio("r1", "m1", "2026-03-15T10:00:00Z",
                                  live_dec="EXECUTE_YES", strict_dec="EXECUTE_YES", loose_dec="EXECUTE_YES",
                                  live_ev=0.04, strict_ev=0.04, loose_ev=0.04)
        cmp = build_profile_comparison(records)
        assert cmp.loose_only_count == 0
        # All-agree rows not added to divergent_rows
        assert all(r.divergence_type != "ALL_AGREE" for r in cmp.divergent_rows)

    def test_empty_records(self):
        cmp = build_profile_comparison([])
        assert isinstance(cmp, ProfileComparisonSummary)
        assert cmp.total_records == 0
        assert cmp.loose_only_count == 0
        assert cmp.divergent_rows == []

    def test_single_profile_only(self):
        records = [_make_record(decision="REJECT", profile="live")]
        cmp = build_profile_comparison(records)
        assert "live" in cmp.profiles_found
        assert cmp.total_records == 1

    def test_profiles_found_list(self):
        records = (
            self._make_trio("r1", "m1", "2026-03-15T10:00:00Z")
        )
        cmp = build_profile_comparison(records)
        assert set(cmp.profiles_found) == {"live", "paper_strict", "paper_loose"}


# ── build_decision_chain ──────────────────────────────────────────────────────

class TestBuildDecisionChain:
    def test_all_steps_populated(self):
        rec = _make_record(decision="EXECUTE_YES", ev=0.05)
        chain = build_decision_chain(rec)
        assert isinstance(chain, DecisionChain)
        assert chain.bridge_step["intent_side"] == "YES"
        assert abs(chain.bridge_step["effective_yes_prob"] - 0.68) < 1e-9
        assert chain.calibration_step["quality"] == "good"
        assert chain.calibration_step["method"] == "platt"
        assert abs(chain.pricing_step["ask_yes"] - 0.55) < 1e-9
        assert abs(chain.pricing_step["snapshot_age_seconds"] - 5.0) < 1e-9
        assert abs(chain.ev_step["execution_adjusted_ev"] - 0.05) < 1e-9
        assert chain.policy_step["final_decision"] == "EXECUTE_YES"
        assert chain.policy_step["passes_final_gate"] is True

    def test_reject_chain_has_reason(self):
        rec = _make_record(decision="REJECT", rejection_reason="STALE_PRICING")
        chain = build_decision_chain(rec)
        assert chain.policy_step["final_decision"] == "REJECT"
        assert chain.policy_step["rejection_reason"] == "STALE_PRICING"
        assert chain.policy_step["passes_final_gate"] is False

    def test_replay_available_live_shadow(self):
        rec = _make_record(evidence_source="live_shadow")
        chain = build_decision_chain(rec)
        assert chain.replay_available is True

    def test_replay_not_available_synthetic(self):
        rec = _make_record(evidence_source="synthetic")
        chain = build_decision_chain(rec)
        assert chain.replay_available is False

    def test_sanity_notes_in_pricing_step(self):
        rec = _make_record(decision="EXECUTE_YES", ev=0.03)
        rec["decision"]["pricing_sanity_notes"] = "suspicious underround: ask_sum=0.91"
        chain = build_decision_chain(rec)
        assert "suspicious" in chain.pricing_step["sanity_notes"]
