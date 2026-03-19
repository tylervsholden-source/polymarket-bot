# Dashboard UI Specification

**File:** `architect_chamber/index.html`
**URL:** `http://localhost:8080/chamber`
**Polling:** `GET /api/chamber/summary` every 5 seconds

---

## Design Principles

1. Operator-first. Not a trading toy. Dense, readable, honest.
2. No fabricated values. If data is not available, show "–" not "0".
3. Color coding communicates state, not decoration:
   - Green = healthy / positive / execute
   - Red = blocker / negative / loss
   - Yellow = warning / conditional
   - Blue = accent / neutral / info
   - Muted = N/A / no data
4. Dark theme. Monospace font throughout.
5. All numbers explicitly labeled with units ($ / % / seconds).

---

## Tab Structure

### Overview
Quick health summary for the operator arriving at the dashboard.
- Equity cards (4): total equity, cash, unrealized PnL, day PnL
- Shadow stats (4): total records, files, observation days, cycles
- Recent 10 decisions (clickable)
- Readiness verdict + evidence progress bar
- Active alerts

### Decisions
Full decision feed with filters.
- Filter by: profile, decision type (execute/reject), asset, market ID
- Columns: time, asset, horizon, decision badge, profile, calibration quality, EV, rejection reason, snapshot age
- Click any row → Decision Chain drilldown modal

### Positions
- Open position count and total unrealized PnL
- Position table with risk flags

### Closed Trades
- Win/loss/neutral summary stats
- Trade blotter sorted most recent first

### Profile Comparison
- Per-profile stat cards with colored borders (red=live, yellow=strict, blue=loose)
- Divergent decision table
- Cross-profile summary (loose-only count is highlighted if elevated)

### Health
- Journal / underround / stale / partial fill metric cards
- All active alerts with levels and thresholds
- Cycle status detail

### Readiness
- Verdict box (colored by verdict type)
- Evidence sufficiency numbers with "need X" indicators
- Readiness checks table (all checks, not just fails)
- Pilot constraints panel (always visible)
- Per-profile readiness summaries
- Evidence gaps list

---

## Decision Chain Modal

Opened by clicking any decision row or the ⛓ icon.

Fetches `GET /api/chamber/decision/<record_id>` and renders 5 pipeline steps:

**Step 1 — Bridge Mapping**
Shows how the signal's predicted direction was mapped to YES/NO.
- intent_side, effective_yes_prob, effective_no_prob, mapping_context

**Step 2 — Calibration**
Shows calibration quality and method. If quality is "weak" or "unknown",
operator knows the probability estimate is not reliable.
- method, quality (color-coded), calibrated_up_prob, calibrated_down_prob, Brier score, ECE

**Step 3 — Pricing Sanity**
Shows raw market pricing at decision time.
- ask_yes, bid_yes, ask_no, bid_no, snapshot_age (yellow if > 60s), sanity_notes

**Step 4 — EV / Edge Computation**
Shows the actual EV numbers that drove the decision.
- gross_ev, net_ev_after_fee, execution_adjusted_ev (green if positive), required_threshold, fill_fraction

**Step 5 — Policy Gate**
Shows the final policy verdict.
- profile, passes_final_gate, final_decision (badge), rejection_reason (red if set)

This panel makes the oversight chain explicit. The operator can see exactly
why a decision was EXECUTE or REJECT — no black-box theater.

---

## Readiness Verdict Color Coding

| Verdict | Color |
|---|---|
| TINY_PILOT_CANDIDATE | Green border |
| CONDITIONAL_REVIEW | Yellow border |
| NO_GO | Red border |
| INSUFFICIENT_EVIDENCE | Blue border |
| NOT_REVIEWED | Muted border |

---

## Divergence Type Labels

| Type | Meaning |
|---|---|
| LOOSE_ONLY | paper_loose executes, live and strict both reject. **Warning signal.** |
| STRICT_REJECTS_LIVE_PASS | paper_strict rejects but live passes. Uncommon. |
| PARTIAL | Some profiles agree, some disagree. |
| ALL_AGREE | All profiles reach same decision. Not shown in divergence table. |

---

## Operator Workflows Supported

1. **Inspect recent YES/NO decisions**: Decisions tab → filter by profile=live, decision=EXECUTE
2. **Review a REJECT chain**: Click any REJECT row → Chain modal → Step 5 shows rejection_reason
3. **Inspect open positions**: Positions tab → risk flags visible, unrealized PnL color-coded
4. **Inspect a closed trade**: Trades tab → realized PnL, PnL%, result badge
5. **Compare profiles**: Profile Comparison tab → per-profile exec rates, divergent rows
6. **Check readiness blockers**: Readiness tab → checks table + evidence gaps
7. **Check health/drift alerts**: Health tab → alert list with metric values vs thresholds
