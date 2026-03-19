"""
config/policies.py — Phase 12 Policy Profile Reference

SINGLE SOURCE OF TRUTH FOR POLICY DIFFERENCES
==============================================

Import policy profiles from calibration.types:

    from calibration.types import (
        LIVE_CAL_CONFIG,
        PAPER_STRICT_CAL_CONFIG,
        PAPER_LOOSE_CAL_CONFIG,
        PAPER_CAL_CONFIG,        # legacy alias → paper_loose behaviour
    )

This file is a documentation module. It has no runtime behaviour.
All threshold values below are read from calibration.types constants.

──────────────────────────────────────────────────────────────────────────────
POLICY PROFILE COMPARISON TABLE (Phase 12)
──────────────────────────────────────────────────────────────────────────────

Parameter                        LIVE        PAPER_STRICT  PAPER_LOOSE
────────────────────────────────────────────────────────────────────────
mode string                      "live"      "paper_strict" "paper_loose"
min_execution_adjusted_edge      0.030       0.025         0.020
min_calibrated_confidence        0.550       0.550         0.550
min_prob_sum                     0.900       0.750         0.500
reject_on_weak_calibration       True        True          False
reject_on_unknown_calibration    True        True          False
require_class_probabilities      True        False         False
max_snapshot_age_seconds         60          120           300

── Binary market sanity (Phase 12) ──────────────────────────────────────────
min_ask_sum (ask_yes+ask_no)     0.97        0.93          0.88 (annotate)
max_ask_sum                      1.10        1.15          1.25 (annotate)
check_bid_overround              True        True          False
max_bid_sum (bid_yes+bid_no)     1.00        1.00          N/A
min_single_ask                   0.05        0.03          N/A (no check)
allow_suspicious_underround      False→REJECT False→REJECT True→annotate only

── Fill behaviour (Phase 11) ────────────────────────────────────────────────
PARTIAL fill                     REJECT      EV penalty    EV penalty
partial_fill_penalty             N/A         50bps         50bps

── Hard structural limits (all modes, INVALID_PRICING) ──────────────────────
ask_sum < 0.85                   REJECT      REJECT        REJECT
bid_sum > 1.01                   REJECT      REJECT        REJECT
ask < bid                        REJECT      REJECT        REJECT
price outside [0,1]              REJECT      REJECT        REJECT
timestamp in future              REJECT      REJECT        REJECT

──────────────────────────────────────────────────────────────────────────────
WHEN TO USE EACH PROFILE
──────────────────────────────────────────────────────────────────────────────

LIVE:
  For real capital deployment only. Every check at maximum strictness.
  Caller MUST pass explicit intended_size_usdc or ValueError is raised.

PAPER_STRICT:
  For honest paper evaluation before going live. Close to live in every
  dimension. If a trade fails paper_strict, it will almost certainly fail live.
  Weak calibration and unknown calibration are still rejected. Suspicious
  underround is still rejected. Use this to verify strategy quality honestly.

PAPER_LOOSE:
  For hypothesis exploration, debugging, and market scanning. Allows
  weak calibration, missing class_probs, and suspicious pricing (annotated).
  Results from paper_loose CANNOT be used to justify going live. If a trade
  only passes in paper_loose, that is a warning, not a signal.

PAPER (legacy):
  Alias for paper_loose behaviour. mode="paper". Preserved for backward
  compatibility with Phase 10/11 tests. New code should use PAPER_LOOSE_CAL_CONFIG.

──────────────────────────────────────────────────────────────────────────────
INTERPRETING PRICING_SANITY_NOTES
──────────────────────────────────────────────────────────────────────────────

TradeDecision.pricing_sanity_notes is set when:
  - policy profile is paper_loose / paper / default
  - AND _check_binary_sanity() detects a suspicious condition

This means the trade passed in paper_loose DESPITE suspicious pricing.
The field is None for live/paper_strict (those reject outright) and None
when pricing is clean.

Interpreting: if pricing_sanity_notes is not None on an EXECUTE decision,
the apparent edge may be a pricing artefact. Treat this result with suspicion.

──────────────────────────────────────────────────────────────────────────────
POLICY CHOICES MADE EXPLICIT (Phase 12 requirement)
──────────────────────────────────────────────────────────────────────────────

1. Live vs paper_strict ask_sum gap: 0.97 vs 0.93.
   Rationale: live requires tighter bands because residual exposure from
   partial fills combines with underround risk. paper_strict is close to live
   but allows slightly wider pricing for realistic research conditions.

2. Bid-side sanity: enforced in live and paper_strict (max_bid_sum=1.00),
   not checked in paper_loose. Rationale: bid_sum > 1.00 is near-arb territory
   which cannot exist in real liquid markets — if it appears, data is suspect.
   paper_loose ignores this for exploratory scanning purposes.

3. Low liquidity does NOT tighten binary sanity thresholds in this phase.
   Rationale: liquidity is already gated at step 8 (LOW_LIQUIDITY rejection).
   Adding liquidity-conditional sanity would create overlapping rejection logic.
   Deferred to Phase 13 if thin-market fake-edge becomes a confirmed problem.

4. paper_loose allows suspicious underround but annotates it.
   Rationale: completely blocking suspicious pricing in exploratory mode would
   make it impossible to study why those markets look interesting. The annotation
   is the honest signal that the result is suspect.

5. paper_loose is NOT allowed to produce trades that will be executed on live
   capital. The only valid use of paper_loose output is research observation.
"""
# This file is intentionally a documentation module.
# It has no runtime behaviour — import calibration.types directly.
