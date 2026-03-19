# Operator Data Contracts

**Module:** `operator_layer/types.py`

All Architect Chamber data structures with explicit source annotations.

---

## 1. DecisionEvent

Source: `shadow_journal_YYYY-MM-DD.jsonl` → one `ShadowDecisionRecord` per row.

**SHADOW-ONLY.** Describes what the pipeline would decide. No real money moved.

| Field | Type | Source | Note |
|---|---|---|---|
| record_id | str | journal record_id | UUID4 |
| run_id | str | journal run_id | Shared across one orchestrator session |
| timestamp_utc | datetime | ts_recorded_utc | Wall clock at journal write |
| asset | str | signal.asset | e.g. "BTC" |
| horizon_minutes | int | signal.horizon_minutes | e.g. 15 |
| predicted_class | str | signal.predicted_class | "UP" / "DOWN" / "NO_TRADE" |
| calibration_quality | str | signal.calibration_quality | "good" / "moderate" / "weak" / "unknown" |
| calibration_method | str | signal.calibration_method | "platt" / "identity" etc. |
| effective_yes_prob | float | signal.effective_yes_prob | Post-bridge YES probability |
| effective_no_prob | float | signal.effective_no_prob | Post-bridge NO probability |
| bridge_intent_side | str | signal.bridge_intent_side | "YES" or "NO" |
| market_id | str | pricing.market_id | Polymarket market token ID |
| ask_yes | float | pricing.ask_yes | Market ask price for YES |
| ask_no | float | pricing.ask_no | Market ask price for NO |
| snapshot_age_seconds | float | pricing.snapshot_age_seconds | How old the pricing was |
| policy_profile | str | policy_profile | "live" / "paper_strict" / "paper_loose" |
| intended_size_usdc | float | intended_size_usdc | Trade size used in EV calc |
| final_decision | str | decision_summary.decision | "EXECUTE_YES" / "EXECUTE_NO" / "REJECT" |
| rejection_reason | str? | decision_summary.rejection_reason | Null if execute |
| passes_final_gate | bool | decision_summary.passes_final_gate | |
| execution_adjusted_ev | float? | decision_summary.execution_adjusted_ev | **SHADOW** — simulated EV |
| fill_fraction | float? | decision_summary.fill_fraction | **SHADOW** — simulated fill |
| pricing_sanity_notes | str? | decision_summary.pricing_sanity_notes | Set if suspicious pricing in paper_loose |
| evidence_source | str | evidence_source | "live_shadow" / "demo" / "synthetic" |

---

## 2. OpenPosition

Source: `positions.json` → `positions` dict.

**REAL** (actual position or simulation position with real-ish capital tracking).

| Field | Type | Source | Note |
|---|---|---|---|
| position_id | str | market_id key | |
| market_id | str | positions key | |
| question | str | question | Human-readable market title |
| outcome | str | outcome | "YES" / "NO" / "UP" / "DOWN" |
| amount | float | amount | USDC committed |
| entry_price | float | entry_price | |
| current_mark | float? | current_price | None if not updated |
| current_value | float? | current_value | Derived from current_price × shares |
| unrealized_pnl | float? | unrealized_pnl | current_value - amount |
| status | str | status | "MATCHED" / "OPEN" |
| order_id | str | order_id | On-chain order ID |
| age_seconds | float? | **DERIVED** | Computed from wall clock at view time |
| risk_flags | list[str] | **DERIVED** | "DEEP_LOSS_>30PCT" if pnl < -30% of amount |

---

## 3. ClosedTrade

Source: `positions.json` → `closed` array.

**REAL** (settled positions).

| Field | Type | Source | Note |
|---|---|---|---|
| trade_id | str | order_id | |
| market_id | str? | — | **NOT IN positions.json** — always None |
| question | str | question | |
| outcome | str | outcome | |
| amount | float | amount | |
| entry_price | float | entry_price | |
| close_price | float | close_price | |
| realized_pnl | float | pnl | |
| pnl_pct | float? | **DERIVED** | `realized_pnl / amount * 100` |
| result | str | result | "WIN" / "LOSS" / "NEUTRAL" |
| payout | float | payout | |
| opened_at | datetime? | — | **NOT IN positions.json** — always None |
| closed_at | datetime? | — | **NOT IN positions.json** — always None |
| original_policy_profile | str? | — | **NOT IN positions.json** — always None |
| original_decision_id | str? | — | **NOT IN positions.json** — always None |

---

## 4. EquityState

Source: `positions.json` + `status.json`.

**REAL** (actual capital tracking).

| Field | Type | Source | Note |
|---|---|---|---|
| timestamp_utc | datetime | **DERIVED** | Wall clock at view time |
| cash_available | float | **DERIVED** | `capital - sum(open amounts)` |
| capital_committed | float | **DERIVED** | `sum(open position amounts)` |
| unrealized_pnl | float | **DERIVED** | `sum(open unrealized_pnl)` |
| realized_pnl_day | float | positions.json daily.pnl | |
| realized_pnl_total | float | **DERIVED** | `sum(closed pnl)` |
| total_equity | float | **DERIVED** | `cash + committed + unrealized` |
| active_positions_count | int | **DERIVED** | `len(open_positions)` |
| initial_capital | float? | status.json initial_capital | |
| blocked_reason | str? | **DERIVED** | "DAILY_STOP_LOSS" if daily loss ≥ 15% |

---

## 5. HealthState

Source: journal integrity stats + recent shadow records + status.json + control.json.

**MIXED** (journal/cycle data is real; rates are derived from shadow corpus).

| Field | Type | Source | Note |
|---|---|---|---|
| journal_healthy | bool | **DERIVED** | `bad_line_fraction <= 0.02` |
| journal_bad_line_fraction | float? | ledgers.read_journal_integrity_stats() | |
| suspicious_underround_rate | float? | **DERIVED** | From recent shadow records |
| stale_pricing_rate | float? | **DERIVED** | snapshot_age > 60s fraction |
| partial_fill_rate | float? | **DERIVED** | fill_fraction < 1.0 on executes |
| cycle_running | bool | status.json + control.json | |
| cycles_completed | int? | status.json cycle | |
| last_cycle_at | str? | status.json updated | |
| blocker_active | bool | **DERIVED** | journal unhealthy → blocker |

---

## 6. ReadinessState

Source: `data/readiness_verdict.json` (written by `monitoring/daily_review.py`).
Profile summaries derived from recent shadow records at view time.

| Field | Type | Source | Note |
|---|---|---|---|
| status | str | verdict | "TINY_PILOT_CANDIDATE" / "NO_GO" / "CONDITIONAL_REVIEW" / "INSUFFICIENT_EVIDENCE" / "NOT_REVIEWED" |
| verdict_reason | str | verdict_reason | |
| evidence_sufficient | bool | evidence_sufficient | |
| last_reviewed_at | datetime? | generated_utc | None if not reviewed |
| blocker_count | int | len(blockers) | |
| fail_count | int | len(fails) | |
| warn_count | int | len(warns) | |
| live_like_evaluated | int | evidence_result | |
| observation_days | float | evidence_result | |
| live_summary | ProfileReadinessSummary? | **DERIVED** | From recent records |
| pilot_* fields | various | pilot_constraints | Always present — even on NO_GO |

---

## 7. ProfileComparisonSummary

Source: all shadow journal records.

**SHADOW-ONLY.**

| Field | Type | Note |
|---|---|---|
| profiles_found | list[str] | Profiles actually seen in journal |
| per_profile | dict | {profile: {total, execute, reject, exec_rate}} |
| divergent_rows | list[ProfileComparisonRow] | Decisions where profiles disagree |
| loose_only_count | int | paper_loose executes but live rejects |
| strict_not_live_count | int | paper_strict passes but live rejects (rare) |

---

## 8. DecisionChain

Source: single shadow journal record. Used in drilldown modal.

Shows 5 pipeline steps explicitly:
1. `bridge_step` — intent side, effective probs, mapping context
2. `calibration_step` — method, quality, calibrated probs, Brier, ECE
3. `pricing_step` — ask/bid values, snapshot age, sanity notes
4. `ev_step` — gross EV, net EV, execution-adjusted EV, threshold, fill
5. `policy_step` — profile, passes gate, final decision, rejection reason
