# Decision Flow

End-to-end path from market data to shadow journal record.

```
ORCHESTRATOR (60s cycle)
│
├─ 1. Fetch active markets (polymarket_client)
│     Filter: crypto up/down only (BTC/ETH/SOL/XRP/DOGE/BNB/HYPE)
│
├─ 2. ArbitrageEngine.analyze(candidates)
│     For each candidate:
│     ├─ BinanceFeed / Bitstamp → spot price, RSI, volume, order book
│     ├─ BayesianEstimator → P(UP) given technical indicators
│     ├─ EdgeModel → edge = P(UP) - market_ask
│     ├─ StoikovModel → fill realism (fill_fraction, executable_notional)
│     ├─ KellyCriterion → position size
│     └─ SmartTraderTracker → ±0.05 Bayesian boost if top traders agree
│
├─ 3. _record_shadow_decisions(candidates, signals)
│     For EACH candidate (whether signal produced or not):
│     ├─ Build ShadowDecisionRecord from TradeSignal / no-signal
│     ├─ decision = EXECUTE_YES/EXECUTE_NO or REJECT + reason
│     └─ JournalWriter.write(record) → data/shadow_journal_YYYY-MM-DD.jsonl
│
├─ 4. If live_trading=True AND signal produced:
│     polymarket_client.place_order(token_id, size, price)
│     position_manager.add_position(market_id, outcome, amount, entry)
│
└─ 5. StatusWriter.update() → data/status.json
```

## What ArbitrageEngine Does (NOT the calibration pipeline)

ArbitrageEngine is the LIVE decision engine. It does NOT use:
- signal_bridge/ (CalibratedSignal type mapping)
- calibration/ (probability_mapper, calibrator)
- decision_policy.decide()

It uses its own internal Bayesian + Edge stack.

The calibration pipeline (signal_bridge → calibration → execution_realism → decide())
is used by the ShadowRunner test harness — NOT by the live orchestrator.

This is the key architectural split:

| Component | Used By | Purpose |
|---|---|---|
| ArbitrageEngine | Orchestrator (live) | Real-time trading decisions |
| calibration pipeline + decide() | ShadowRunner | Research/policy testing |
| ShadowDecisionRecord | Both paths | Unified audit journal |

## Shadow Recording in the Orchestrator

The orchestrator creates ShadowDecisionRecord objects directly from
ArbitrageEngine output (not from ShadowRunner.evaluate()):

- EXECUTE signals → decision = "EXECUTE_YES" or "EXECUTE_NO"
- Candidates with no signal → decision = "REJECT", reason = "NO_SIGNAL_PRODUCED"
- All records written to daily-rotating JSONL journal
- evidence_source = "live_shadow" (valid for readiness gate)

## Capital Flow

```
Initial capital: positions.json["capital"]
  ↓
Trade opened:    capital unchanged; position added to positions["positions"]
  ↓
available_capital = capital - sum(open_position.amount)
  ↓
Trade closed:    capital += pnl  (NOT += amount + pnl — double-count fix)
                 position removed; added to positions["closed"]
  ↓
Daily PnL:       positions["daily"]["pnl"] accumulated
```

## Kill Switch Path

```
data/control.json: live_trading = False
  → orchestrator checks each cycle
  → if False: skips order placement (shadow recording continues)

Daily stop loss: if daily_pnl < -15% of initial_capital
  → orchestrator sets equity.blocked_reason = "DAILY_STOP_LOSS"
  → no new orders placed for rest of day
```
