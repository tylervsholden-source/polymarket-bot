# Live vs Shadow Side Truth Specification

## Problem Statement

Before this fix, the shadow journal and live execution path had different views of NO-side pricing:

| Path | NO Price Source | Result |
|------|----------------|--------|
| **Live** (ArbitrageEngine) | Real orderbook `no_best_ask` | Often 0.99 → NO edge always negative → NO never traded |
| **Shadow** (Orchestrator journal) | Synthetic `1 - yes_price` | Healthy-looking NO edges → appeared tradable |

This split-brain meant shadow analysis could never predict live behavior. Shadow showed opportunities that could never execute, making shadow review unreliable for validating the NO side.

## Solution

### Shadow Journal Fix

The shadow journal (`_record_shadow_decisions` in orchestrator.py) now:

1. **Uses real NO prices when available**:
   ```
   if market has no_best_ask and it's > 0:
       use real no_best_ask → label as REAL_BOOK
   else:
       use 1 - yes_price → label as SYNTHETIC
   ```

2. **Labels the source explicitly** in every shadow record via `mapping_context`:
   ```
   no_src=REAL_BOOK  or  no_src=SYNTHETIC
   no_ask=0.99       (the actual value used)
   ```

3. **Includes side diagnostics** from the ArbitrageEngine when available:
   - `dir_reason` — the NoSideStatus that explains the direction decision
   - `bridge_intent_side` — YES/NO/NONE from diagnostics

4. **Includes entry window status**:
   - `ew=PASS` or `ew=FAIL:reason` or `ew=NO_QUESTION`

### Live Path (ArbitrageEngine)

The live path now has identical NO price source logic:
- Real book → `NoPriceSource.REAL_BOOK`
- No book data → `NoPriceSource.SYNTHETIC`
- Book fetched but empty → `NoPriceSource.MISSING`

### Consistency Guarantee

Both paths now:
1. Check `market.get("no_best_ask")` first
2. Fall through to synthetic only when real data is unavailable
3. Label the source explicitly
4. Use the same price value for edge computation

This means shadow records accurately reflect what the live path would see, making shadow review a valid predictor of live behavior.

## Shadow Record Format

Each shadow `SignalSnapshot` now includes in `mapping_context`:

```
no_src=REAL_BOOK|SYNTHETIC  no_ask=0.45  ew=PASS  dir_reason=YES_EDGE_DOMINATES
```

## How to Audit

1. **Check shadow journal for NO price source distribution**:
   - Grep for `no_src=REAL_BOOK` vs `no_src=SYNTHETIC`
   - If all entries are SYNTHETIC, the NO orderbook data pipeline may be broken

2. **Compare live rejections with shadow**:
   - Shadow `dir_reason=NO_SIDE_UNTRADABLE` should match live logs
   - No more phantom NO opportunities in shadow that can't execute live

3. **Entry window correlation**:
   - Shadow `ew=FAIL:TOO_LATE` should correlate with live gate rejections

## Files Changed

| File | Change |
|------|--------|
| `agents/orchestrator.py` | Shadow journal uses real NO prices, includes diagnostics and entry window |
| `strategies/arbitrage_engine.py` | Exposes `get_last_diagnostics()` for shadow to consume |

## Verification

- `tests/test_shadow_live_side_consistency.py` — both paths produce same source labels and prices
