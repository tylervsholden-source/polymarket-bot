# SHADOW_REVIEW_TEMPLATE.md
# Phase 14: Shadow Review Template

Use this template for every review cycle. Fill in observed values. Do not skip sections.

---

## Review Header

```
Review Date:       ___________________
Reviewer:          ___________________
Review Period:     ___________________ to ___________________
Total Days Covered: ___________________
Profiles Reviewed: [ ] live  [ ] paper_strict  [ ] paper_loose
Journal Path:      ___________________
```

---

## Section 1: Evidence Sufficiency

Run: `check_evidence_sufficiency(records)`

```
Live-like evaluated:  ______ (required: 50)      PASS / FAIL
Live-like executes:   ______ (required: 10)      PASS / FAIL
Live-like rejects:    ______ (required: 20)      PASS / FAIL
Assets covered:       ______ (required: 1)       PASS / FAIL
Horizons covered:     ______ (required: 1)       PASS / FAIL
Observation days:     ______ (required: 3.0)     PASS / FAIL

Evidence sufficient: YES / NO

If NO → stop here. Continue shadow collection. Do not proceed to Section 2.
```

---

## Section 2: Decision Volume Summary

```
Profile       | Evaluated | Execute | Reject | Exec Rate
--------------|-----------|---------|--------|----------
live          |           |         |        |
paper_strict  |           |         |        |
paper_loose   |           |         |        |

Observations:
_____________________________________________________________
```

---

## Section 3: Rejection Composition

Fill in for live profile (primary). Note paper_strict and paper_loose differences.

```
Rejection Reason         | Live Count | Live % | paper_strict % | paper_loose %
-------------------------|------------|--------|----------------|---------------
WEAK_CALIBRATION         |            |        |                |
UNKNOWN_CALIBRATION      |            |        |                |
NEGATIVE_EDGE            |            |        |                |
LOW_CONFIDENCE           |            |        |                |
LOW_LIQUIDITY            |            |        |                |
STALE_PRICING            |            |        |                |
SUSPICIOUS_UNDERROUND    |            |        |                |
PARTIAL_FILL_REJECTED    |            |        |                |
INCONSISTENT_PROBS       |            |        |                |
OTHER                    |            |        |                |

Dominant rejection reason: ___________________________
Is dominance explained by market conditions?  YES / NO / UNCLEAR
Notes:
_____________________________________________________________
```

---

## Section 4: EV Realism

```
Profile      | Gross EV  | Exec EV   | Haircut Abs | Haircut %
-------------|-----------|-----------|-------------|----------
live         |           |           |             |
paper_strict |           |           |             |
paper_loose  |           |           |             |

EV haircut level:   GREEN (<30%) / WARN (30-60%) / FAIL (>60%) / BLOCKER (>80%)

Notes on EV gap:
_____________________________________________________________
```

---

## Section 5: Pricing Sanity

```
Suspicious underround count: ______  Rate: ______  Level: GREEN / WARN / FAIL / BLOCKER
Stale pricing count:         ______  Rate: ______  Level: GREEN / WARN / FAIL
paper_loose annotated executes (suspicious pricing): ______

Any suspicious underround concentration in specific asset/horizon?  YES / NO
Details:
_____________________________________________________________
```

---

## Section 6: Fillability

```
Full fill executes:          ______
Partial fill executes:       ______
Partial fill rejected:       ______  Rate: ______  Level: GREEN / WARN / FAIL

Is partial fill rejection concentrated in specific size/liquidity condition?  YES / NO
Details:
_____________________________________________________________
```

---

## Section 7: Profile Divergence

```
Divergence (paper_loose exec% - live exec%): ______pp
Level: GREEN (<20pp) / WARN (20-40pp) / FAIL (40-60pp) / BLOCKER (>60pp)

Divergence (paper_strict exec% - live exec%): ______pp
Is paper_strict close to live (< 25pp)?  YES / NO

paper_loose being read as performance evidence?  YES (problem) / NO (correct)
```

---

## Section 8: Drift / Stability

```
Rolling windows computed: ______  (window size: ______)

Stability flags:
[ ] rejection_rate_spike     — max adjacent window delta: ______pp
[ ] ev_trend_negative        — first window EV: ______, last window EV: ______
[ ] fillability_degrading    — fill rejection rate trend: ______
[ ] underround_rising        — underround rate trend: ______

Overall stability: STABLE / UNSTABLE
Notes:
_____________________________________________________________
```

---

## Section 9: Readiness Assessment

Run: `assess_readiness(live_metrics, evidence_result, loose_metrics)`

```
Readiness verdict: GO / CONDITIONAL / NO_GO

Checks triggered:
  BLOCKERS: _____________________________________________
  FAILS:    _____________________________________________
  WARNS:    _____________________________________________

Verdict reason: __________________________________________
```

---

## Section 10: Observations and Anomalies

```
Notable observations this period:
1. ___________________________________________________________
2. ___________________________________________________________
3. ___________________________________________________________

Asset/horizon anomalies:
_____________________________________________________________

Unexpected behavior:
_____________________________________________________________
```

---

## Section 11: Recommendation

Select ONE:

```
[ ] CONTINUE SHADOW
    Reason: Insufficient evidence or instability. Continue collecting.
    Next review: ___________________

[ ] TIGHTEN POLICY
    Reason: ___________________________________________________
    Suggested adjustment: _____________________________________

[ ] INVESTIGATE ANOMALY
    Reason: ___________________________________________________
    Investigation target: _____________________________________

[ ] CANDIDATE FOR PILOT READINESS REVIEW
    Reason: All checks PASS/WARN, evidence sufficient, behavior coherent.
    Formal readiness verdict: GO / CONDITIONAL
    Proposed pilot start: ___________________
    Pilot constraints applied: [ ] Confirmed (see LIVE_PILOT_READINESS.md)

[ ] NO-GO — ESCALATE
    Reason: Hard blocker(s) detected.
    Blocker(s): ______________________________________________
    Action required: _________________________________________
```

---

## Review Cadence

### Daily Summary (every day shadow is running)
- Check Sections 1–3 (evidence, volume, rejection composition)
- Flag any obvious anomalies
- Takes: ~5 minutes

### Rolling Multi-Day Summary (every 3 days)
- Complete all sections
- Run `compute_regime_review()` for stability analysis
- Compare to previous review
- Takes: ~20 minutes

### Pilot Readiness Review (when daily reviews consistently show stable, sufficient data)
- Complete all sections with full quantitative detail
- Run `assess_readiness()` for formal verdict
- Document all WARNs and FAILs with context
- Confirm pilot constraints before any GO verdict is acted upon
- Takes: ~45 minutes with formal sign-off

---

## What to Look For (Quick Reference)

**Stop immediately if:**
- Any BLOCKER is triggered
- Journal is missing records (gaps in ts_recorded_utc sequence)
- Replay consistency fails

**Investigate if:**
- Single rejection reason > 70% of all rejections
- paper_loose execution rate > 50pp above live
- EV is trending downward across rolling windows
- Pricing sanity annotated executes rising in paper_loose

**Healthy signal:**
- Rejection reasons are diverse (no single reason > 50% of all decisions)
- EV haircut is stable across rolling windows
- paper_strict and live are within 25pp of each other
- No drift flags from regime_review
