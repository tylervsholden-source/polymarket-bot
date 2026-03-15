"""
shadow_runner/runner.py

Phase 13: ShadowRunner — multi-profile shadow execution.

Design:
  - Accepts a list of CalibrationConfig objects (one per policy profile).
  - For each candidate (CalibratedSignal + MarketPricingSnapshot), runs the FULL
    real decide() pipeline under every profile.
  - Never places real orders — shadow execution only.
  - Returns one ShadowDecisionRecord per (candidate × profile).
  - Records are written to journal via JournalWriter (optional).

Design decisions:
  1. Shadow runner uses the SAME decide() as live — no stub, no mock.
     Any divergence from live behavior is a bug in shadow_runner, not intended.
  2. Parallel multi-profile: every profile sees the same inputs; results are
     independent. Order of profiles in output matches order passed to constructor.
  3. Runner never swallows exceptions from decide(). If decide() raises
     (e.g., live mode without intended_size_usdc), the exception propagates.
     Callers must ensure configs are compatible with the inputs they provide.
  4. intended_size_usdc is per-candidate, not per-profile.
     All profiles for a given candidate share the same trade size.
  5. now_utc is captured ONCE per candidate before iterating profiles,
     so all profiles for a candidate share the same clock value.
     This is required for deterministic replay.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from calibration.decision_policy import decide
from calibration.types import (
    CalibratedSignal,
    CalibrationConfig,
    CalibrationMethod,
    CalibrationQuality,
    MarketPricingSnapshot,
    TradeDecision,
)

from shadow_runner.types import (
    DecisionSummary,
    PricingSnapshot,
    ShadowDecisionRecord,
    ShadowRunConfig,
    SignalSnapshot,
)


class ShadowRunner:
    """
    Runs the full decision pipeline in shadow mode across multiple policy profiles.

    Parameters
    ----------
    configs : list[CalibrationConfig]
        One config per policy profile to run in parallel.
        Order is preserved in output records.
    run_config : ShadowRunConfig | None
        Metadata for this run session (run_id, description, etc.).
        If None, a default ShadowRunConfig is created with a fresh run_id.

    Usage
    -----
        runner = ShadowRunner([LIVE_CAL_CONFIG, PAPER_STRICT_CAL_CONFIG])
        records = runner.evaluate(cal_signal, pricing, intended_size_usdc=50.0)
        # returns list of 2 ShadowDecisionRecord, one per config
    """

    def __init__(
        self,
        configs: list[CalibrationConfig],
        run_config: Optional[ShadowRunConfig] = None,
    ) -> None:
        if not configs:
            raise ValueError("ShadowRunner requires at least one CalibrationConfig")
        self._configs = configs
        self._run_config = run_config or ShadowRunConfig(
            profile_names=[c.mode for c in configs],
        )

    @property
    def run_id(self) -> str:
        return self._run_config.run_id

    def evaluate(
        self,
        cal_signal: CalibratedSignal,
        pricing: MarketPricingSnapshot,
        intended_size_usdc: Optional[float] = None,
        now_utc: Optional[datetime] = None,
    ) -> list[ShadowDecisionRecord]:
        """
        Run all profiles against a single candidate.

        Parameters
        ----------
        cal_signal : CalibratedSignal
            Output of probability_mapper.
        pricing : MarketPricingSnapshot
            Current market pricing.
        intended_size_usdc : float | None
            Trade size in USDC. Required when any config has mode="live".
        now_utc : datetime | None
            Reference clock. Captured once and shared across all profiles
            for this candidate. None → datetime.now(UTC).

        Returns
        -------
        list[ShadowDecisionRecord]
            One record per config, in the same order as self._configs.
        """
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)

        signal_snap = _extract_signal_snapshot(cal_signal)
        snapshot_age = _snapshot_age(pricing.timestamp_utc, now_utc)
        pricing_snap = _extract_pricing_snapshot(pricing, snapshot_age)

        records: list[ShadowDecisionRecord] = []
        for config in self._configs:
            decision = decide(
                cal_signal,
                pricing,
                config=config,
                now_utc=now_utc,
                intended_size_usdc=intended_size_usdc,
            )
            summary = _extract_decision_summary(decision)
            record = ShadowDecisionRecord(
                record_id=str(uuid.uuid4()),
                run_id=self.run_id,
                ts_recorded_utc=now_utc,
                signal=signal_snap,
                pricing=pricing_snap,
                policy_profile=config.mode,
                intended_size_usdc=intended_size_usdc if intended_size_usdc is not None else 20.0,
                decision_summary=summary,
            )
            records.append(record)

        return records

    def evaluate_batch(
        self,
        candidates: list[tuple[CalibratedSignal, MarketPricingSnapshot]],
        intended_size_usdc: Optional[float] = None,
        now_utc: Optional[datetime] = None,
    ) -> list[ShadowDecisionRecord]:
        """
        Evaluate a batch of candidates.

        Each candidate gets its own now_utc snapshot if now_utc is None.
        If now_utc is explicitly provided, all candidates share it (for batch testing).

        Returns flat list: [cand0_prof0, cand0_prof1, cand1_prof0, cand1_prof1, ...]
        """
        results: list[ShadowDecisionRecord] = []
        for cal_signal, pricing in candidates:
            t = now_utc if now_utc is not None else datetime.now(timezone.utc)
            results.extend(
                self.evaluate(
                    cal_signal, pricing,
                    intended_size_usdc=intended_size_usdc,
                    now_utc=t,
                )
            )
        return results


# ── Extraction helpers ────────────────────────────────────────────────────────

def _extract_signal_snapshot(cal: CalibratedSignal) -> SignalSnapshot:
    raw = cal.raw
    return SignalSnapshot(
        asset=raw.asset,
        horizon_minutes=raw.horizon_minutes,
        signal_timestamp_utc=raw.timestamp_utc,
        predicted_class=raw.predicted_class,
        raw_confidence=raw.raw_confidence,
        class_probabilities=raw.class_probabilities,
        model_version=raw.model_version,
        calibrated_up_prob=cal.calibrated_up_prob,
        calibrated_down_prob=cal.calibrated_down_prob,
        calibrated_no_trade_prob=cal.calibrated_no_trade_prob,
        calibration_method=cal.calibration_method.value,
        calibration_quality=cal.calibration_quality.value,
        effective_yes_prob=cal.effective_yes_prob,
        effective_no_prob=cal.effective_no_prob,
        mapping_context=cal.mapping_context,
        bridge_intent_side=cal.bridge_intent_side,
        brier_score=cal.brier_score,
        ece=cal.ece,
    )


def _extract_pricing_snapshot(
    pricing: MarketPricingSnapshot,
    snapshot_age_seconds: float,
) -> PricingSnapshot:
    return PricingSnapshot(
        market_id=pricing.market_id,
        ask_yes=pricing.ask_yes,
        bid_yes=pricing.bid_yes,
        ask_no=pricing.ask_no,
        bid_no=pricing.bid_no,
        liquidity=pricing.liquidity,
        pricing_timestamp_utc=pricing.timestamp_utc,
        snapshot_age_seconds=snapshot_age_seconds,
    )


def _extract_decision_summary(decision: TradeDecision) -> DecisionSummary:
    edge = decision.edge_estimate
    return DecisionSummary(
        decision=decision.decision.value,
        rejection_reason=(
            decision.rejection_reason.value
            if decision.rejection_reason is not None
            else None
        ),
        policy_mode=decision.policy_mode,
        passes_final_gate=decision.passes_final_gate,
        intended_size_usdc_used=decision.intended_size_usdc_used,
        pricing_sanity_notes=decision.pricing_sanity_notes,
        gross_ev=edge.gross_expected_value if edge is not None else None,
        net_ev_after_fee=decision.net_ev_after_fee,
        execution_adjusted_ev=edge.execution_adjusted_ev if edge is not None else None,
        required_edge_threshold=decision.final_gate_threshold,
        fill_fraction=decision.fill_fraction,
    )


def _snapshot_age(pricing_ts: datetime, now_utc: datetime) -> float:
    if pricing_ts.tzinfo is None:
        pricing_ts = pricing_ts.replace(tzinfo=timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return max(0.0, (now_utc - pricing_ts).total_seconds())


# ── Reconstruction helpers (for replay) ──────────────────────────────────────

def reconstruct_calibrated_signal(snap: SignalSnapshot) -> CalibratedSignal:
    """
    Reconstruct a CalibratedSignal from a SignalSnapshot (for replay).

    All fields are restored exactly as stored — no recalculation.
    """
    from calibration.types import RawSignalOutput

    raw = RawSignalOutput(
        asset=snap.asset,
        horizon_minutes=snap.horizon_minutes,
        timestamp_utc=snap.signal_timestamp_utc,
        predicted_class=snap.predicted_class,
        raw_confidence=snap.raw_confidence,
        class_probabilities=snap.class_probabilities,
        model_version=snap.model_version,
    )
    return CalibratedSignal(
        raw=raw,
        calibrated_up_prob=snap.calibrated_up_prob,
        calibrated_down_prob=snap.calibrated_down_prob,
        calibrated_no_trade_prob=snap.calibrated_no_trade_prob,
        calibration_method=CalibrationMethod(snap.calibration_method),
        calibration_quality=CalibrationQuality(snap.calibration_quality),
        effective_yes_prob=snap.effective_yes_prob,
        effective_no_prob=snap.effective_no_prob,
        mapping_context=snap.mapping_context,
        bridge_intent_side=snap.bridge_intent_side,  # type: ignore[arg-type]
        brier_score=snap.brier_score,
        ece=snap.ece,
    )


def reconstruct_pricing_snapshot(snap: PricingSnapshot) -> MarketPricingSnapshot:
    """
    Reconstruct a MarketPricingSnapshot from a PricingSnapshot (for replay).
    """
    return MarketPricingSnapshot(
        market_id=snap.market_id,
        ask_yes=snap.ask_yes,
        bid_yes=snap.bid_yes,
        ask_no=snap.ask_no,
        bid_no=snap.bid_no,
        liquidity=snap.liquidity,
        timestamp_utc=snap.pricing_timestamp_utc,
    )
