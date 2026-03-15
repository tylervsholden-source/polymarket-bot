"""
shadow_runner/replay.py

Phase 13: Deterministic replay verification.

Design:
  Given a ShadowDecisionRecord, reconstruct the exact decide() inputs from the
  stored fields and re-run the decision pipeline. Compare output to the stored
  decision.

Design decisions:
  1. Replay RE-COMPUTES — it re-runs decide() from reconstructed inputs.
     It does NOT compare raw stored JSON blobs. Re-computing proves that:
       a) The decide() pipeline is deterministic.
       b) The journal stores enough information to reproduce a decision.
  2. now_utc source: ts_recorded_utc is used directly as now_utc.
     ShadowRunner.evaluate() stores ts_recorded_utc = now_utc (the exact value
     passed to decide()). Using it avoids any float round-trip through
     snapshot_age_seconds, which would risk sub-microsecond precision loss.
  3. intended_size_usdc: stored in the record. Re-used exactly.
  4. Config reconstruction: replay requires a mapping from policy_profile
     string to CalibrationConfig. Callers pass a dict[str, CalibrationConfig].
     If the profile is not in the dict, ReplayResult.match=False with
     mismatch_reason="unknown_profile".
  5. Partial match tolerance: replay asserts both decision type AND rejection
     reason match. EV values are not compared (floating-point noise is
     acceptable; what matters is the gate-pass decision).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from calibration.decision_policy import decide
from calibration.types import CalibrationConfig

from shadow_runner.runner import (
    reconstruct_calibrated_signal,
    reconstruct_pricing_snapshot,
)
from shadow_runner.types import ReplayResult, ShadowDecisionRecord


def replay_record(
    record: ShadowDecisionRecord,
    config_map: dict[str, CalibrationConfig],
) -> ReplayResult:
    """
    Replay a single ShadowDecisionRecord and compare to stored output.

    Parameters
    ----------
    record : ShadowDecisionRecord
        Record loaded from the journal.
    config_map : dict[str, CalibrationConfig]
        Mapping from policy_profile string to CalibrationConfig.
        Must contain an entry for record.policy_profile.

    Returns
    -------
    ReplayResult
        match=True iff both decision type and rejection_reason match.
    """
    profile = record.policy_profile
    config = config_map.get(profile)
    if config is None:
        return ReplayResult(
            record_id=record.record_id,
            original_decision=record.decision_summary.decision,
            replayed_decision="(skipped)",
            original_rejection=record.decision_summary.rejection_reason,
            replayed_rejection=None,
            match=False,
            mismatch_reason=f"unknown_profile: {profile!r} not in config_map",
        )

    # Reconstruct inputs
    cal_signal = reconstruct_calibrated_signal(record.signal)
    pricing    = reconstruct_pricing_snapshot(record.pricing)

    # Use ts_recorded_utc directly as now_utc.
    # ShadowRunner.evaluate() sets ts_recorded_utc = now_utc (the exact value
    # passed to decide()). Using it directly is exact and avoids any float
    # round-trip through snapshot_age_seconds → timedelta(seconds=float).
    now_utc = record.ts_recorded_utc
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    intended = record.intended_size_usdc

    # Re-run decide()
    try:
        replayed = decide(
            cal_signal,
            pricing,
            config=config,
            now_utc=now_utc,
            intended_size_usdc=intended if intended > 0 else None,
        )
    except Exception as exc:
        return ReplayResult(
            record_id=record.record_id,
            original_decision=record.decision_summary.decision,
            replayed_decision="(exception)",
            original_rejection=record.decision_summary.rejection_reason,
            replayed_rejection=None,
            match=False,
            mismatch_reason=f"decide() raised {type(exc).__name__}: {exc}",
        )

    orig_decision  = record.decision_summary.decision
    orig_rejection = record.decision_summary.rejection_reason
    rep_decision   = replayed.decision.value
    rep_rejection  = replayed.rejection_reason.value if replayed.rejection_reason else None

    match = orig_decision == rep_decision and orig_rejection == rep_rejection
    mismatch_reason: Optional[str] = None
    if not match:
        if orig_decision != rep_decision:
            mismatch_reason = (
                f"decision mismatch: original={orig_decision!r} "
                f"replayed={rep_decision!r}"
            )
        else:
            mismatch_reason = (
                f"rejection_reason mismatch: original={orig_rejection!r} "
                f"replayed={rep_rejection!r}"
            )

    return ReplayResult(
        record_id=record.record_id,
        original_decision=orig_decision,
        replayed_decision=rep_decision,
        original_rejection=orig_rejection,
        replayed_rejection=rep_rejection,
        match=match,
        mismatch_reason=mismatch_reason,
    )


def replay_batch(
    records: list[ShadowDecisionRecord],
    config_map: dict[str, CalibrationConfig],
) -> list[ReplayResult]:
    """Replay a batch of records. Returns one ReplayResult per record."""
    return [replay_record(r, config_map) for r in records]


def assert_replay_consistency(
    records: list[ShadowDecisionRecord],
    config_map: dict[str, CalibrationConfig],
) -> None:
    """
    Replay all records and raise AssertionError if any mismatch.

    Useful in test suites. Reports all mismatches at once (not just the first).
    """
    results = replay_batch(records, config_map)
    failures = [r for r in results if not r.match]
    if failures:
        lines = [
            f"  [{i+1}] record_id={f.record_id}: {f.mismatch_reason}"
            for i, f in enumerate(failures)
        ]
        raise AssertionError(
            f"Replay consistency check failed: "
            f"{len(failures)}/{len(results)} records did not replay consistently.\n"
            + "\n".join(lines)
        )
