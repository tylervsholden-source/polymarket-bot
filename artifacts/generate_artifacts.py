"""Generate synthetic snapshot artifacts for review."""
import json, uuid, os
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
os.makedirs(f"{BASE}/replay_examples", exist_ok=True)
os.makedirs(f"{BASE}/decision_examples", exist_ok=True)

NOW = datetime(2026, 3, 15, 20, 0, 0, tzinfo=timezone.utc)
RUN_ID = str(uuid.uuid4())

SYNTH = "SYNTHETIC — generated for snapshot review. NOT real live_shadow evidence."

def make_signal(asset="BTC", horizon=15, predicted="UP", cal_quality="good",
                cal_method="platt", up_prob=0.67, eff_yes=0.67, eff_no=0.33, bridge="YES", ts=None):
    t = (ts or NOW).isoformat()
    return {
        "asset": asset, "horizon_minutes": horizon,
        "signal_timestamp_utc": t, "predicted_class": predicted,
        "raw_confidence": 0.72, "class_probabilities": {"UP":0.72,"DOWN":0.18,"NO_TRADE":0.10},
        "model_version": "v0",
        "calibrated_up_prob": up_prob, "calibrated_down_prob": round(1-up_prob-0.05,4),
        "calibrated_no_trade_prob": 0.05,
        "calibration_method": cal_method, "calibration_quality": cal_quality,
        "effective_yes_prob": eff_yes, "effective_no_prob": eff_no,
        "mapping_context": f"{predicted}→{bridge}",
        "bridge_intent_side": bridge, "brier_score": 0.18, "ece": 0.04
    }

def make_pricing(market_id, ask_yes=0.55, bid_yes=0.54, ask_no=0.46, bid_no=0.45,
                 liq=8000, age=5.0, ts=None):
    t = (ts or NOW).isoformat()
    return {
        "market_id": market_id, "ask_yes": ask_yes, "bid_yes": bid_yes,
        "ask_no": ask_no, "bid_no": bid_no, "liquidity": liq,
        "pricing_timestamp_utc": t, "snapshot_age_seconds": age
    }

def make_ds(decision="EXECUTE_YES", rejection=None, profile="live",
            gross_ev=0.052, net_ev=0.042, exec_ev=0.038,
            threshold=0.030, fill=1.0, sanity=None):
    is_exec = decision != "REJECT"
    return {
        "decision": decision, "rejection_reason": rejection, "policy_mode": profile,
        "passes_final_gate": is_exec, "intended_size_usdc_used": 20.0,
        "pricing_sanity_notes": sanity,
        "gross_ev": gross_ev if is_exec else None,
        "net_ev_after_fee": net_ev if is_exec else None,
        "execution_adjusted_ev": exec_ev if is_exec else None,
        "required_edge_threshold": threshold,
        "fill_fraction": fill if is_exec else None
    }

def make_record(decision="EXECUTE_YES", profile="live", asset="BTC", market_id=None,
                offset_min=0, rejection=None, sanity=None,
                gross_ev=0.052, net_ev=0.042, exec_ev=0.038,
                threshold=0.030, fill=1.0, evidence_source="live_shadow", cal_quality="good"):
    ts = NOW - timedelta(minutes=offset_min)
    mid = market_id or ("0x" + uuid.uuid4().hex[:40])
    return {
        "record_id": str(uuid.uuid4()), "run_id": RUN_ID,
        "ts_recorded_utc": ts.isoformat(), "schema_version": "1",
        "signal": make_signal(asset=asset, cal_quality=cal_quality, ts=ts),
        "pricing": make_pricing(mid, ts=ts),
        "policy_profile": profile, "intended_size_usdc": 20.0,
        "evidence_source": evidence_source,
        "decision_summary": make_ds(decision=decision, rejection=rejection, profile=profile,
                                    gross_ev=gross_ev, net_ev=net_ev, exec_ev=exec_ev,
                                    threshold=threshold, fill=fill, sanity=sanity)
    }

REJECTION_ROTATION = [
    "LOW_EDGE","LOW_EDGE","STALE_PRICING","LOW_CALIBRATION_CONFIDENCE",
    "LOW_PROB_SUM","LOW_EDGE","LOW_LIQUIDITY","PARTIAL_FILL_REJECT"
]

# ── 1. live_like journal ──────────────────────────────────────────────────────
live_recs = []
for i in range(12):
    live_recs.append(make_record("EXECUTE_YES","live","BTC", offset_min=i*5))
for i in range(5):
    live_recs.append(make_record("EXECUTE_NO","live","ETH", offset_min=i*7+60))
for i in range(43):
    live_recs.append(make_record("REJECT","live","BTC", offset_min=i*3+120,
                                  rejection=REJECTION_ROTATION[i%8],
                                  gross_ev=0.018, net_ev=0.008, exec_ev=0.008))

with open(f"{BASE}/shadow_journal_live_like.jsonl","w") as f:
    f.write(json.dumps({"_note": SYNTH}) + "\n")
    for r in live_recs:
        f.write(json.dumps(r) + "\n")

# ── 2. paper_strict journal ───────────────────────────────────────────────────
strict_recs = []
for i in range(14):
    strict_recs.append(make_record("EXECUTE_YES","paper_strict","BTC",offset_min=i*5))
for i in range(46):
    strict_recs.append(make_record("REJECT","paper_strict","ETH",offset_min=i*3+120,
                                    rejection="LOW_EDGE",
                                    gross_ev=0.019,net_ev=0.009,exec_ev=0.009,threshold=0.025))

with open(f"{BASE}/shadow_journal_paper_strict.jsonl","w") as f:
    f.write(json.dumps({"_note": SYNTH}) + "\n")
    for r in strict_recs:
        f.write(json.dumps(r) + "\n")

# ── 3. paper_loose journal ────────────────────────────────────────────────────
loose_recs = []
for i in range(28):
    loose_recs.append(make_record("EXECUTE_YES","paper_loose","BTC",offset_min=i*4,exec_ev=0.024))
for i in range(32):
    loose_recs.append(make_record("REJECT","paper_loose","SOL",offset_min=i*4+120,
                                   rejection="LOW_EDGE",exec_ev=0.024,
                                   sanity=("suspicious underround: ask_sum=0.91" if i%5==0 else None)))

with open(f"{BASE}/shadow_journal_paper_loose.jsonl","w") as f:
    f.write(json.dumps({"_note": SYNTH}) + "\n")
    for r in loose_recs:
        f.write(json.dumps(r) + "\n")

# ── 4. capital_state_sample.json ─────────────────────────────────────────────
with open(f"{BASE}/capital_state_sample.json","w") as f:
    json.dump({
        "_note": "SYNTHETIC — represents initial 100 USDT state before first trade",
        "timestamp_utc": NOW.isoformat(),
        "total_capital_usdc": 100.0,
        "cash_available_usdc": 100.0,
        "capital_committed_usdc": 0.0,
        "unrealized_pnl_usdc": 0.0,
        "realized_pnl_day_usdc": 0.0,
        "realized_pnl_total_usdc": 0.0,
        "total_equity_usdc": 100.0,
        "active_positions_count": 0,
        "daily_stop_loss_threshold_usdc": 15.0,
        "blocked_reason": None
    }, f, indent=2)

# ── 5. open_positions_sample.jsonl ────────────────────────────────────────────
with open(f"{BASE}/open_positions_sample.jsonl","w") as f:
    f.write(json.dumps({"_note": "SYNTHETIC — example $5 YES position"}) + "\n")
    f.write(json.dumps({
        "position_id": "0xaaa111", "market_id": "0xaaa111",
        "question": "Bitcoin Up or Down - Mar 15 8:30PM-8:45PM ET",
        "outcome": "YES", "amount_usdc": 5.0, "entry_price": 0.62,
        "current_mark": 0.68, "current_value_usdc": 5.48, "unrealized_pnl_usdc": 0.48,
        "status": "MATCHED", "order_id": "0xaaa111", "age_seconds": 480, "risk_flags": []
    }) + "\n")

# ── 6. closed_trades_sample.jsonl ─────────────────────────────────────────────
with open(f"{BASE}/closed_trades_sample.jsonl","w") as f:
    f.write(json.dumps({"_note": "SYNTHETIC. opened_at/closed_at=null: not stored in positions.json."}) + "\n")
    f.write(json.dumps({
        "trade_id":"0xwin001","question":"BTC Up or Down Mar14","outcome":"YES",
        "amount_usdc":5.0,"entry_price":0.45,"close_price":1.0,
        "realized_pnl_usdc":6.11,"pnl_pct":122.2,"result":"WIN",
        "opened_at":None,"closed_at":None,"original_policy_profile":None
    }) + "\n")
    f.write(json.dumps({
        "trade_id":"0xloss001","question":"ETH Up or Down Mar14","outcome":"NO",
        "amount_usdc":5.0,"entry_price":0.52,"close_price":0.0,
        "realized_pnl_usdc":-5.0,"pnl_pct":-100.0,"result":"LOSS",
        "opened_at":None,"closed_at":None,"original_policy_profile":None
    }) + "\n")

# ── 7. orders_sample.jsonl ────────────────────────────────────────────────────
with open(f"{BASE}/orders_sample.jsonl","w") as f:
    f.write(json.dumps({"_note": "SYNTHETIC"}) + "\n")
    f.write(json.dumps({
        "order_id":"0xorder001","market":"Bitcoin Up or Down","outcome":"YES",
        "amount_usdc":5.0,"price":0.62,"edge":0.038,"status":"MATCHED",
        "time": NOW.isoformat()
    }) + "\n")

# ── 8. equity_curve_sample.jsonl ──────────────────────────────────────────────
with open(f"{BASE}/equity_curve_sample.jsonl","w") as f:
    f.write(json.dumps({"_note": "SYNTHETIC — 3-day hypothetical pilot trajectory"}) + "\n")
    for hours, eq, pos, rpnl in [
        (48, 100.00, 0, 0.0), (36, 100.48, 1, 0.0),
        (24, 101.11, 0, 0.63),(12, 100.80, 1, 0.63), (0, 101.74, 0, 1.74)
    ]:
        f.write(json.dumps({
            "ts": (NOW - timedelta(hours=hours)).isoformat(),
            "equity_usdc": eq, "open_positions": pos, "realized_pnl_usdc": rpnl
        }) + "\n")

# ── 9. readiness_report.json ──────────────────────────────────────────────────
readiness_report = {
    "_note": "SYNTHETIC — current state: insufficient evidence (shadow running ~1 day)",
    "generated_utc": NOW.isoformat(),
    "verdict": "INSUFFICIENT_EVIDENCE",
    "evidence_sufficient": False,
    "verdict_reason": "Insufficient shadow evidence: Need 100 evaluated decisions; have 60. Need 5.0 observation days; have 1.0.",
    "blockers":[],"fails":[],"warns":[],"checks":[],
    "evidence_result": {
        "live_like_evaluated": 60,"live_like_executes": 17,"live_like_rejects": 43,
        "observation_days": 1.0,
        "gaps":[
            {"dimension":"live_like_evaluated","required":100,"actual":60,
             "message":"Need 100 evaluated decisions; have 60."},
            {"dimension":"observation_days","required":5.0,"actual":1.0,
             "message":"Need 5.0 calendar days; have 1.0."}
        ]
    },
    "pilot_constraints": {
        "max_open_positions":1,"pilot_asset":"BTC","pilot_horizon_minutes":15,
        "max_nominal_usdc":10.0,"daily_max_loss_usdc":5.0,"single_loss_kill_usdc":3.0,
        "mandatory_review_hours":24,"pilot_evaluation_days":3
    }
}
with open(f"{BASE}/readiness_report.json","w") as f:
    json.dump(readiness_report, f, indent=2)

# ── 10. daily_report.json ─────────────────────────────────────────────────────
with open(f"{BASE}/daily_report.json","w") as f:
    json.dump({
        "_note":"SYNTHETIC","generated_utc":NOW.isoformat(),"date_label":"2026-03-15",
        "total_records":60,
        "live_metrics":{
            "policy_profile":"live","total_evaluated":60,"execute_count":17,
            "reject_count":43,"execution_rate":0.283,
            "mean_ev":0.038,"p25_ev":0.031,"p75_ev":0.047,
            "suspicious_underround_rate":0.0,"stale_pricing_rate":0.05,
            "partial_fill_rejection_rate":0.0,
            "rejection_rates":{
                "LOW_EDGE":0.558,"STALE_PRICING":0.116,
                "LOW_CALIBRATION_CONFIDENCE":0.140,"LOW_PROB_SUM":0.093,"LOW_LIQUIDITY":0.093
            }
        },
        "strict_metrics":{"policy_profile":"paper_strict","total_evaluated":60,
                           "execute_count":14,"reject_count":46,"execution_rate":0.233},
        "loose_metrics":{"policy_profile":"paper_loose","total_evaluated":60,
                          "execute_count":28,"reject_count":32,"execution_rate":0.467},
        "readiness":readiness_report,
        "would_trade":[{
            "asset":"BTC","market_id":"0xabc123","decision":"EXECUTE_YES",
            "executable_ev":0.038,"fill_fraction":1.0,"intended_size_usdc":20.0,
            "passed_paper_strict":True,"passed_paper_loose":True
        }],
        "loose_only":[],
        "journal_integrity_stats":{
            "total_lines":60,"parsed_ok":60,"bad_json_count":0,
            "bad_line_fraction":0.0,"files_checked":1
        },
        "regime_summary":"No regime instability detected."
    }, f, indent=2)

# ── 11. drift_report.json ─────────────────────────────────────────────────────
with open(f"{BASE}/drift_report.json","w") as f:
    json.dump({
        "_note":"SYNTHETIC","generated_utc":NOW.isoformat(),
        "baseline_window_size":30,"current_window_size":30,
        "profiles_checked":["live","paper_strict","paper_loose"],
        "rejection_rate_delta":{"live":0.017,"paper_strict":0.008,"paper_loose":-0.010},
        "ev_mean_delta":{"live":-0.002},
        "divergence":[{"profile_a":"live","profile_b":"paper_loose",
                        "total_shared":30,"agreement_count":22,"divergence_rate":0.267}],
        "alerts":[],"drift_detected":False
    }, f, indent=2)

# ── 12. rejection_analytics.json ─────────────────────────────────────────────
with open(f"{BASE}/rejection_analytics.json","w") as f:
    json.dump({
        "_note":"SYNTHETIC","generated_utc":NOW.isoformat(),
        "profile":"live","total_evaluated":60,"total_rejected":43,"rejection_rate":0.717,
        "reason_breakdown":{
            "LOW_EDGE":{"count":24,"pct":55.8},
            "LOW_CALIBRATION_CONFIDENCE":{"count":6,"pct":14.0},
            "STALE_PRICING":{"count":5,"pct":11.6},
            "LOW_PROB_SUM":{"count":4,"pct":9.3},
            "LOW_LIQUIDITY":{"count":4,"pct":9.3}
        },
        "interpretation":"LOW_EDGE dominates at 55.8% — market prices are close to model estimates. Normal for a tight live policy."
    }, f, indent=2)

# ── 13-17. Decision examples ─────────────────────────────────────────────────
def save_example(name, obj):
    with open(f"{BASE}/decision_examples/{name}.json","w") as f:
        json.dump(obj, f, indent=2)

r_clean = make_record("EXECUTE_YES","live","BTC",exec_ev=0.042)
save_example("clean_execute_yes", {"_type":"clean_execute_yes","_note":"SYNTHETIC","record":r_clean})

r_susp = make_record("EXECUTE_YES","paper_loose","BTC",exec_ev=0.024,
                      sanity="suspicious underround: ask_yes+ask_no=0.91 < 0.93 threshold")
save_example("suspicious_underround_paper_loose_only",
             {"_type":"suspicious_underround_loose_only","_note":"SYNTHETIC","record":r_susp})

r_partial = make_record("REJECT","live","ETH",rejection="PARTIAL_FILL_REJECT",
                          gross_ev=0.028,net_ev=0.018,exec_ev=0.012)
r_partial["decision_summary"]["fill_fraction"]=0.4
save_example("partial_fill_reject",{"_type":"partial_fill_reject","_note":"SYNTHETIC","record":r_partial})

r_live_fail = make_record("REJECT","live","BTC",rejection="LOW_EDGE",exec_ev=None)
r_loose_pass = make_record("EXECUTE_YES","paper_loose","BTC",exec_ev=0.022)
save_example("loose_only_divergence",
             {"_type":"loose_only","_note":"SYNTHETIC",
              "live_record":r_live_fail,"loose_record":r_loose_pass})

r_stale = make_record("REJECT","live","SOL",rejection="STALE_PRICING")
r_stale["pricing"]["snapshot_age_seconds"]=185.0
save_example("stale_pricing_reject",{"_type":"stale_pricing_reject","_note":"SYNTHETIC","record":r_stale})

# ── 18. Replay example ────────────────────────────────────────────────────────
with open(f"{BASE}/replay_examples/replay_clean_execute.json","w") as f:
    json.dump({
        "_note":"SYNTHETIC","record_id":r_clean["record_id"],
        "original_decision":"EXECUTE_YES","replayed_decision":"EXECUTE_YES",
        "original_rejection":None,"replayed_rejection":None,
        "match":True,"mismatch_reason":None
    }, f, indent=2)

# ── Summary ───────────────────────────────────────────────────────────────────
print("Artifacts generated:")
for root, dirs, files in os.walk(BASE):
    dirs[:] = [d for d in dirs if d not in ["__pycache__"]]
    for fn in sorted(files):
        if fn == "generate_artifacts.py":
            continue
        path = os.path.join(root, fn)
        rel = os.path.relpath(path, BASE)
        print(f"  artifacts/{rel}  ({os.path.getsize(path)} bytes)")
