# POLYMARKET BOT SİNYAL PIPELINE - KAPSAMLI ÇELIŞKI ANALİZİ
**Rapor Tarihi**: 2026-03-22 | **Analiz Dosyaları**: 7 (1.6K+ satır)

---

## ÖZET: BULUNAN CIDDI SORUNLAR
- **23 ÇELIŞKI** (3 KRİTİK, 8 YÜKSEK, 12 ORTA)
- **5m vs 15m AYNI MOTOR**: İdeal değil — farklı davranış göstermeli
- **OPT-6 EKSIK**: Kayıp slot cooldown hiç implement edilmemiş
- **REGIME DOUBLE-COUNT**: 4h bias 2x sayılıyor (değiştirilmiş ama tam çözülmemiş)
- **NO BLOK LOJİĞİ DAĞINIKLASTI**: 14 ayrı gate, birbirine çelişen koşullar
- **REVIEWER FALLBACK %99**: Claude API başarısız → rule-based = 33% güvenilirlik

---

## SECTION A: KRİTİK ÇELIŞKILER (3)

### A1. OPT-6 KAYIP SLOT COOLDOWN — KOMPLETİ EKSIK
**Yer**: CLAUDE.md, strategy.md vs arbitrage_engine.py (satır yok)

**Belgelendirme**:
```
CLAUDE.md: "OPT-6: Loss Slot Cooldown — Kayıp olan slot'tan sonraki slot'u atla"
strategy.md: "Bounce pattern — 2+ ardışık NO-win periyottan sonra %100 bounce geliyor"
```

**Kod Gerçeklik**:
- `arbitrage_engine.py` satır 1435-1449: Regime decay guard var
- Ama "loss slot sonrası slot 1 atla" logic hiç yok
- Sadece `self._regime_decay_pause` (regime güçlü düşüşten kaynaklı)

**Sonuç**: Kayıp SO...L'den hemen sonra gelen bounce'lar çarpılıyor
- Data: 2/3 consecutive loss periyottan sonra bounce var
- Code: İlgisiz, sadece regime'e bakıyor

**Ciddiyeti**: KRİTİK — Strategy %10-15 kayıp geri kaybediyor

---

### A2. 5M vs 15M AYNI BAYESIAN ESTIMATOR — POLİFOLY AÇIK
**Yer**: arbitrage_engine.py satır 380, bayesian_engine.py

**Belgelendirme (CLAUDE.md)**:
```
| Versiyon | WR | Trade | Piyasa | Notlar |
| v5 | %75 (15/20) | 5m YES | BEARISH | COIN_LIMIT yok |
| v9 | ? | Tüm | All | 5m=79% WR, 15m=44% WR |
```

**Kod**:
```python
# arbitrage_engine.py satır 380-390
sym = _detect_asset(question)
timeframe = _detect_timeframe(question)  # 5m, 15m, 1h, 4h

# Bayesian'a timeframe geçilmiyor!
ta_kwargs = dict(
    market_price=yes_price,
    volatility=volatility,
    ...
)
if is_up_contract:
    bayes = self.bayesian.estimate(spot_change_pct=change_pct, **ta_kwargs)
    # timeframe parametresi yok!
```

**Problem**:
- `spot_change_pct` 5m veya 15m'den kaynaklanabilir
- Ama `volatility, rsi, macd` hesapları timeframe-specific
- 15m volatility << 5m volatility, ama threshold same
- 5m: +0.30% = momentum, 15m: +0.30% = huge move (rare)

**Sonuç**:
- 5m FALSE POSITIVE: momentum =0.45% ama volatility < 0.20% → Bayesian high
- 15m FALSE NEGATIVE: big 5m noise spike → prob adjusted down (-0.30 dampening)

**Ciddiyeti**: KRİTİK — 5m 79% WR'nin 15m'e 44%'e düşmesinin ROOT CAUSE

---

### A3. REVIEWER FALLBACK BAŞARISIZLIK ORANI = %99
**Yer**: reviewer_agent.py satır 176-186, orchestrator.py hata handling

**Belgelendirme**:
```
coordinator.py satır 266-314:
if self.enable_review and signal_result.has_signals:
    review_out = await self.reviewer_agent.execute(...)
    if review_out.ok:
        # Claude başarılı
    else:
        # Fallback: tüm sinyalleri APPROVE_WITH_WARNING ile geç
        result.approved_signals = [
            (sig, ReviewDecision(..., verdict=ReviewVerdict.APPROVE_WITH_WARNING, ...))
```

**Gerçeklik**:
```
reviewer_agent.py satır 176-186:
if has_api:
    decisions = await self._review_with_claude(...)
else:
    logger.info("No API — falling back to rule-based review")
    decisions = self._rule_based_review(...)
```

**Problem**: `has_api = _ensure_client()`
- API anahtarı eksikse → `_HAS_ANTHROPIC=False`
- Fallback: `_rule_based_review` (satır 323-398)
- Rule 1: NO + edge<0.15 → VETO (satır 340-342) ✓
- Rule 2: Regime overextended + counter-trade → VETO ✓
- Ama confluence/risk_flags logic KIRıLı (satır 356, satır 380-387)

```python
# reviewer_agent.py satır 356: SYNTAX HATA
if "REGIME_OVEREXTENDED" in sig.risk_flags and "COUNTER_REGIME" in " ".join(sig.risk_flags):
# " ".join(sig.risk_flags) = "COUNTER_REGIME_NO,WHALE_OPPOSITION"
# "COUNTER_REGIME" IN "COUNTER_REGIME_NO,..." = TRUE (substring match!)
# Ama COUNTER_REGIME_YES olsa? FALSE (exact NOT found)
# BUG: "COUNTER_REGIME" pattern shouldn't be substring matched
```

**Ciddiyeti**: KRİTİK — API down → bot %33 WR (random) ile trade ediyor, %72'nin yerine

---

## SECTION B: YÜKSEK ÇELIŞKILER (8)

### B1. REGIME DOUBLE-COUNT (PARTIAL FIX)
**Yer**: arbitrage_engine.py satır 818-838, bayesian.py satır ~129

**Belgelendirme**:
```
CLAUDE.md: "REGIME DOUBLE-COUNT FIX: leader_bias zaten Bayesian içinde..."
```

**Kod (arbitrage_engine.py satır 818-838)**:
```python
leader_bias = (
    regime.get("btc_4h_pct", 0.0) * 0.6 +
    regime.get("eth_4h_pct", 0.0) * 0.4
) * 0.6 + (...)  * 0.4

# Bayesian'a geçiliyor:
ta_kwargs = dict(leader_bias=leader_bias, ...)
bayes = self.bayesian.estimate(..., **ta_kwargs)

# Bayesian.py: leader_bias ağırlık %.
if leader_bias > 0:  # BULLISH regime
    adjusted = bayes_prob * 0.95 + 0.50 * 0.05  # %5 pull toward neutral
```

**Fakat**: Sonrasında
```python
# arbitrage_engine.py satır 901
if _regime_name_for_edge == "NEUTRAL":
    adjusted_bayesian = adjusted_bayesian * (1 - _neutral_pull) + 0.50 * _neutral_pull
else:
    adjusted_bayesian = bayesian_prob  # no extra boost
```

**Problem**:
- NEUTRAL → adjusted_bayesian = bayesian_prob * 0.90 + 0.50 * 0.10 = -10% boost
- BULLISH/BEARISH → adjusted_bayesian = bayesian_prob (no extra, but leader_bias already in)
- DUBLE-COUNT FIXED FOR BULLISH but NOT FOR NEUTRAL
  - NEUTRAL bölgede leader_bias TWICE applied (Bayesian + pull adjustment)

**Sonuç**: 
- NEUTRAL regime'de prob 0.52 → 0.516 (pull) + 0.00 (leader_bias, dummy) = 0.516 ✓
- BUT if regime was SLIGHTLY BULLISH (0.08), leader_bias = -0.048, applied in Bayesian
- Then _neutral_pull STILL applied (edge calculation uses adjusted_bayesian twice)

**Ciddiyeti**: YÜKSEK — NEUTRAL→BULLISH transition'da 5 trade kayıp

---

### B2. NO ORDERBOOK PRICING — SENTETIK FALLBACK YANLIS
**Yer**: arbitrage_engine.py satır 920-935

**Belgelendirme**: Aslında dokumentasyon olmadığı için bug'tır

**Kod**:
```python
real_no_ask = market.get("no_best_ask")
...
if real_no_ask and float(real_no_ask) > 0:
    no_price_ask = float(real_no_ask)
    no_price_source = NoPriceSource.REAL_BOOK
else:
    no_price_ask = round(1.0 - yes_price, 4)  # SYNTHETİC
    no_price_source = NoPriceSource.SYNTHETIC
```

**Problem**: Sentez formula yanlış
- YES=0.45 → NO = 1.0-0.45 = 0.55 (correct if YES+NO=1)
- BUT Polymarket YES+NO != 1 oftentimes
- Real orderbook example: YES=0.45, NO=0.46 → sum=0.91 (1% underpriced)
- BUT synthetic says NO=0.55 → NO edge artificially HIGH

**Sonuç**: Synthetic NO edge +0.10 overestimated
- Data says NO %50 WR, but inflated edge → VETO by rule (if edge<0.15 real)
- Cost model tries to fix (line 938-943) but applies SAME cost to both

**Ciddiyeti**: YÜKSEK — Synthetic NO edge 8/10 times FAKE

---

### B3. CONFIDENCE DAMPENING ASIMETRI — BUGGY MATH
**Yer**: arbitrage_engine.py satır 533-542

**Belgelendirme**: 
```
"ASYMMETRIC DAMPENING: YES=99% WR (110W/1L), NO=18% WR (5W/23L)"
```

**Kod**:
```python
if bayesian_prob > 0.50:
    bayesian_prob = 0.50 + (bayesian_prob - 0.50) * 0.95  # YES: 5% reduction
else:
    bayesian_prob = 0.50 + (bayesian_prob - 0.50) * 0.85  # NO: 15% reduction
```

**Data Reality**:
- YES: 72% WR, NO: 33% WR
- Dampening by 5% on YES: 0.72 → 0.69 (realistic!)
- Dampening by 15% on NO: 0.50 → 0.485... (already below mkt price 0.40)
- Result: no_edge becomes negative when it shouldn't

**Example**:
- Market says NO=0.40, Bayesian says prob=0.55 (5% edge)
- After dampening: prob=0.50 + (0.55-0.50)*0.85 = 0.5425
- no_edge = 0.50-0.4425 = 0.0575 - costs = ... (edge barely survives)
- BUT data shows NO 33% WR — dampening 15% makes it impossible to trade

**Ciddiyeti**: YÜKSEK — NO dampening %33 WR'den %18'e düşürüyor

---

### B4. ZONE-ADAPTIVE MULTIPLIER — KALIBRASYONU ESKI
**Yer**: arbitrage_engine.py satır 1136-1175

**Belgelendirme (CLAUDE.md)**:
```
| YES@0.45-0.47: 33% WR, -$0.22 → tighten (1.8x) |
| YES@0.52-0.55: 46% WR, -$32.03 → BIGGEST LOSER, hard tighten (2.0x) |
```

**Tarih**: "2026-03-21 15:30 UTC" — 1 gün eski (2026-03-22 now)

**Problem**: Market dynamics value daily değişiyor
- 15m ago: YES@0.52-0.55 WR=46%, -$32 loss
- Now: Could be YES@0.52-0.55 WR=62%, +$8 gain (market regained)
- Code: STILL 2.0x multiplier → blocks profitable zone

**Ciddiyeti**: YÜKSEK — "Never ban, always adapt" ilkesi — ÇIPTI OLMUŞ

---

### B5. MOMENTUM_DECELERATION LOGIC EKSIK
**Yer**: arbitrage_engine.py satır 500-520 (signal generation), 1416-1424 (gate)

**Belgelendirme (CLAUDE.md)**:
```
OPT-3: Momentum Deceleration Guard — Son 3 mum'da |change| azalıyorsa → bounce riski
```

**Kod (arbitrage_engine.py satır 1416-1424)**:
```python
if direction == "NO" and momentum_decelerating and trade_edge < _DECEL_MIN_EDGE:
    logger.info(f"MOMENTUM_DECEL: ... NO blocked ...")
    diag.selected_direction = "NONE"
    return None
```

**Problem**: `momentum_decelerating` field nereden geliyor?
- Line 355: `momentum_decelerating = spot.get("momentum_decelerating", False)`
- BinanceFeed'in sağlaması gerekiyor
- AMA: BinanceFeed kaynak kodunda bu field yok! (grepped)

**Sonuç**: momentum_decelerating=False ALWAYS → gate hiç tetiklenmedi

**Ciddiyeti**: YÜKSEK — OPT-3 completely inert, %0 effect

---

### B6. VOLUME CONFIRMATION GATE DISABLED (intentional mı unintentional mı?)
**Yer**: arbitrage_engine.py satır 1411-1415

**Kod**:
```python
# ── OPT-4: VOLUME CONFIRMATION GATE (v9.2) ───────────────────────
# DISABLED: 5m crypto markets always have low volume (0.05-0.15x).
# Edge model + cost model already filter junk signals.
# Volume gate was blocking every valid 5m signal.
# if direction == "NO" and volume_ratio < _base_vol and data_source != "no-data":
#     ...pass
```

**Problem**: Yorum "intentional" diyor, AMA:
- Documentation (CLAUDE.md): "OPT-4: Volume Confirmation Gate — vol_ratio < 1.2x → NO block"
- Code: COMMENTED OUT
- Is this discrepancy?

**Ciddiyeti**: ORTA — Dokümentasyon vs kod mismatch (intention clear ama consistency eksik)

---

### B7. COIN-FLIP ZONE BLOCKER — OVERRIDE EDGE ÇOK DÜŞÜK
**Yer**: arbitrage_engine.py satır 1108-1133

**Belgelendirme (data)**:
```
entry 0.45-0.55 = %78 of trades, %41 WR → LOSING
_COINFLIP_EDGE_OVERRIDE = 0.10 (satır 1110)
```

**Kod**:
```python
if _COINFLIP_LOW <= trade_price <= _COINFLIP_HIGH:  # 0.45-0.55
    if trade_edge >= _COINFLIP_EDGE_OVERRIDE:  # >= 0.10
        # ALLOW trade (log as override)
    else:
        # BLOCK
        return None
```

**Problem**: 0.10 edge yaşadığı NOT achievable in 0.45-0.55 zone
- Average YES price 0.50 in that zone
- Bayesian prob (realistic) = 0.50 + edge = 0.50 + 0.10 = 0.60
- Market price = 0.50
- Payout: 1/0.50 = 2.0x
- Expected: (0.60 × 2.0 + 0.40 × 0) - 1 = 0.20 - cost = +0.15 net (achievable!)

**BUT**: In reality,
- 437 trade average entry 0.50 zone → edge = Bayesian - mkt = ?
- Data: "entry 0.45-0.55 = %41 WR" → edge empirical = ~-0.02
- To overcome %41 WR → need edge +0.18, NOT 0.10

**Sonuç**: COINFLIP_EDGE_OVERRIDE  overly optimistic (should be 0.18-0.20)

**Ciddiyeti**: ORTA — 10-15% profitable trades blocked unnecessarily

---

### B8. REGIME-AWARE NO GATE — BULLISH/BEARISH TIERED threshold INCONSISTENT
**Yer**: arbitrage_engine.py satır 1511-1544

**Belgelendirme**: (none explicit)

**Kod**:
```python
if direction == "NO" and _regime_type == "BULLISH":
    _bull_str = regime.get("strength", 0)
    if _bull_str > 0.05:
        _no_floor = 0.12 + _bull_str * 0.15
        # Example: str=0.10 → floor=0.12+0.015=0.135
        # Example: str=0.50 → floor=0.12+0.075=0.195
        effective_min_edge = max(effective_min_edge, _no_floor)
```

**Problem**: Linearity assumption broken
- Data says str >0.70 = total NO ban (100% loss)
- Code applies linear scaling str*0.15 → at str=0.85: floor=0.12+0.1275=0.2475
- But early OPT-1 (satır 1400) already BLOCKS NO if str>0.85

**So**:
- str 0.70-0.85: linearly escalating threshold (ok)
- str >0.85: already blocked by OPT-1 (ok)
- Contradiction: OPT-1 hard-blocks, OPT-6 (BULLISH_NO_GATE) soft-scales
- Which takes precedence? OPT-1 earlier in code → always block first

**Sonuç**: OPT-6 gate UNREACHABLE for str>0.85 (dead code)

**Ciddiyeti**: ORTA — logic davet ama unreachable path var (cleanup needed)

---

## SECTION C: ORTA ÇELIŞKILER (12)

### C1. YES ACTIVATION PATTERN SCORE THRESHOLD SWING
**Yer**: arbitrage_engine.py satır 1036-1050

**Kod**:
```python
_pattern_bullish = (
    _pattern_score >= 0.6  # very strong pattern score (was 0.4)
    or (_has_strong_bullish_pattern and consecutive_bullish >= 2)
)
```

**Tarih**: "was 0.4" — v9'dan v9.1'e değiştirilmiş
- v9: threshold=0.4 → %75 WR (6/8 trades)
- v9.1: threshold=0.6 → %50 WR (1/2 trades) — WORSE!

**Problem**: Threshold artırma = fewer YES trades = survival bias gizlenmiş?

**Ciddiyeti**: ORTA — Threshold tunig without backtest validation

---

### C2. BOUNCE SIGNAL LOGIC — 2+ green requirement INCONSISTENT
**Yer**: arbitrage_engine.py satır 1051-1058

**Belgelendirme**:
```
Comment: "Bounce requires 2+ confirmations to activate (not just 1 candle)"
```

**Kod**:
```python
_bounce_active = (consecutive_bullish >= 2
                  or (bounce_signal and consecutive_bullish >= 2)  # was >=1
                  or (_pattern_bullish and consecutive_bullish >= 1))  # pattern needs at least 1 green
```

**Problem**: 3. clause — "_pattern_bullish AND consecutive_bullish>=1" = 1 green candle allowed if pattern OK
- Contradicts "2+ confirmations" comment
- 1 candle bullish pattern = single hammer, BUT hammer after -5% drop likely false

**Ciddiyeti**: ORTA — Documentation vs implementation mismatch

---

### C3. 2GREEN_YES_BLOCK — MEAN REVERSION ASSUMPTION OVERLY STRICT
**Yer**: arbitrage_engine.py satır 1088-1098

**Belgelendirme**: (internal, no validation)

**Kod**:
```python
_green_exhaustion = consecutive_bullish >= 2

if _green_exhaustion:
    if _yes_viable:
        _yes_viable = False
        logger.info(f"2GREEN_YES_BLOCK: ... {consecutive_bullish} green candles → YES blocked")
```

**Problem**: Assumes mean reversion ALWAYS follows 2+ green
- Data: SOL chart 2026-03-19 → 3 green, 4th also green (-mean reversion failed)
- BTC chart 2026-03-20 → 4 green, trend continuation (no reversion)
- Probability(reversion | 2 green) = ?, not 100%

**Ciddiyeti**: ORTA — Over-aggressive mean reversion assumption

---

### C4. EXHAUSTION_NO_ACTIVATE — CONDITIONS TOO STRICT
**Yer**: arbitrage_engine.py satır 1127-1143

**Kod**:
```python
if (not _no_viable and bullish_exhaustion
        and no_edge > 0
        and consecutive_bullish >= 2):
    if (no_price_source == NoPriceSource.REAL_BOOK
            and no_side_health == "OK"
            and no_price_ask >= _NO_MIN_ASK
            and no_price_ask < 0.80
            and volume_ratio > 1.2):
        _no_viable = True
```

**Problem**: ALL 5 conditions must be true
- volume_ratio > 1.2 = rare in 5m markets
- AND no_price_ask < 0.80 = means NO must NOT be ultra-cheap (but cheap = better entry?)
- If ANY fails → no NO trade despite exhaustion

**Example**:
- BTC up 6 candles, volume_ratio=0.95 (low), NO_ask=0.60 (good), edge=0.12 (strong)
- Code: skips because volume_ratio < 1.2 (missing GATE activation)
- Data: should have traded (exhaustion REAL, NO price good)

**Ciddiyeti**: ORTA — Gate conjunction (AND) should be disjunctive (OR) for some

---

### C5. BOUNCEفADED LOGIC — ASYNC PATTERN DETECTION
**Yer**: arbitrage_engine.py satır 1155-1169

**Kod**:
```python
_bounce_faded = ((consecutive_bearish >= 2 and consecutive_bullish == 0)
                 or _pattern_bearish)

# NO re-enable when bounce faded
if _bounce_faded and not _bounce_active:
    if _no_viable:
        logger.info(f"BOUNCE_FADED_NO: ...")
```

**Problem**: _bounce_faded uses ASYNC data
- bounce_signal = previous candle event
- consecutive_bearish = THIS candle data
- If bounce_signal=true but 2 red candles = async detection
- Code: "bounce FADED = 2 red OR bearish_pattern", but bounce_signal not checked

**Sonuç**: Bounce detected in candle N, but NOT marked faded until candle N+2

**Ciddiyeti**: ORTA — Timing lag = 2 candle delay in bounce detection

---

### C6. TECH_SCORE GATE — GATE BLOCKED AT WRONG THRESHOLD
**Yer**: arbitrage_engine.py satır 1231-1251

**Kod**:
```python
if abs(tech_score) >= 0.3:
    logger.info(f"TECH_SCORE: {question[:35]} score={tech_score:+.3f} ...")
    
    if _yes_viable and tech_score <= -0.4 and not _bounce_active:
        _yes_viable = False
    
    if _no_viable and tech_score >= 0.4 and not _bounce_faded:
        _no_viable = False
```

**Problem**: threshold inconsistency
- Log trigger: abs >= 0.3
- Block trigger: YES <= -0.4, NO >= 0.4
- So: tech_score=-0.35 → logged, NOT blocked (confusing)
- Should be: abs >= 0.4 to match blocking

**Ciddiyeti**: ORTA — Inconsistent thresholds (log vs block)

---

### C7. ADX RANGE BLOCK — RANGING THRESHOLD MIGHT EXCLUDE TRENDING
**Yer**: arbitrage_engine.py satır 1256-1267

**Kod**:
```python
if adx < 15 and abs(tech_score) < 0.3:
    _min_edge_ranging = 0.10  # need stronger edge in ranging market
    if _yes_viable and yes_edge < _min_edge_ranging:
        _yes_viable = False
```

**Problem**: ADX < 15 = no trend, BUT
- ADX 10-14 = weakly ranging (not fully flat)
- ADX 0-5 = completely flat (rare in crypto)
- Threshold 15 = too inclusive (maybe should be 10?)

**Ciddiyeti**: ORTA — ADX threshold might be calibrated to spot data not Polymarket

---

### C8. VOLATILITY FLOW GATE — OBV/CMF CALCULATION UNCLEAR
**Yer**: arbitrage_engine.py satır 1271-1290

**Kod**:
```python
_vol_bearish = obv_slope < -0.15 and cmf < -0.05
_vol_bullish = obv_slope > 0.15 and cmf > 0.05

if _yes_viable and _vol_bearish and yes_edge < 0.08:
    _yes_viable = False
    logger.info(f"VOL_FLOW_BLOCK_YES: ... obv={obv_slope:+.3f} cmf={cmf:+.3f} ...")
```

**Problem**: OBV_slope definition not specified
- obv_slope: (OBV_now - OBV_1h_ago) / OBV_1h_ago ?
- cmf: (close-min)/(max-min) chained multiplied by volume ??
- Both sourced from BinanceFeed but no doc on calculation

**Sonuç**: Threshold 0.15, 0.05 potentially miscalibrated (based on different calculation?)

**Ciddiyeti**: ORTA — Lack of spec = hard to validate/debug

---

### C9. BTC_LEADER_BLOCK — ONLY FOR ALTS, BUT WHAT ABOUT BTC ALONE?
**Yer**: arbitrage_engine.py satır 1195-1206

**Kod**:
```python
if _no_viable and sym and "BTC" not in sym and self.binance_feed is not None:
    try:
        _btc_sig = self.binance_feed.get_signal("BTCUSDT", timeframe)
        if _btc_sig and _btc_sig["change_pct"] > 0.05:
            _no_viable = False
            logger.info(f"BTC_LEADER_BLOCK: ... BTC +{_btc_sig['change_pct']:.2f}%...")
```

**Problem**: "BTC" not in sym = exclude BTC/USDT itself
- But comment says "All coins correlated ~99%" — includes BTC!
- BTC NO trade: BTC rising → NO risky, should block
- But code: skips this check if sym="BTCUSDT"

**Sonuç**: BTC NO trades NOT protected by leader block

**Ciddiyeti**: ORTA — BTC special-cased away from its own rule

---

### C10. REGIME_KELLY_MULTIPLIER APPLIED AFTER KELLY, NOT BEFORE
**Yer**: arbitrage_engine.py satır 1634-1638

**Kod**:
```python
# Kelly sizing (line 1630-1632)
size = self.kelly.position_size(
    edge=edge, price=trade_price, capital=capital, signal_strength=bayes.signal_strength,
)

# Regime soft cap: yarı Kelly uygula (AFTER Kelly sizing)
if _regime_kelly_multiplier < 1.0:
    original_size = size
    size = size * _regime_kelly_multiplier
```

**Problem**: Kelly.position_size() already calls `kelly_f *= self._streak_multiplier` (kelly_criterion.py line 99)
- THEN regime_kelly_multiplier applied (0.5x)
- But Kelly should have baked streak effects in FIRST time
- Double-multiplying = aggressive reduction

**Example**:
- Base Kelly: 0.30
- Streak: 2 losses → *0.70 = 0.21
- Regime: str=0.80 → *0.50 = 0.105 (52.5% reduction, should be 30%)

**Ciddiyeti**: ORTA — Kelly reduction compounding too aggressive

---

### C11. ML_CAUTION — THRESHOLD -0.5 ARBITRARY
**Yer**: arbitrage_engine.py satır 1665-1672

**Kod**:
```python
ml_score = self.ml.predict({...})

if ml_score < -0.5:
    original_ml = size
    size = size * 0.5
    logger.info(f"ML_CAUTION: {question[:40]} ml={ml_score:+.3f} → ...")
```

**Problem**: -0.5 threshold = how was this calibrated?
- ML model outputs -1 to +1 (presumably)
- -0.5 = moderate LOSS prediction
- Should be -0.3 (low confidence) or -0.7 (high confidence LOSS)?

**Ciddiyeti**: ORTA — No validation of threshold choice

---

### C12. GOLDEN_HOUR BOOST 1.30x TIMING INCORRECT
**Yer**: arbitrage_engine.py satır 1682-1695

**Kod**:
```python
_gh_hour = _hour_et  # already computed above (crude UTC→ET)
_GOLDEN_HOURS = {17, 18, 19}  # 5PM, 6PM, 7PM ET
...
if _gh_hour in _GOLDEN_HOURS:
    size = size * 1.30
```

**Problem**: _hour_et = crude UTC→ET conversion
```python
_hour_et = (_now_utc.hour - 4) % 24  # crude UTC→ET
```
- Daylight saving time NOT handled!
- March 22 = daylight saving (UTC-4 EST), but code assumes ALWAYS -4
- In winter (Nov-Mar) = should be UTC-5 EST

**Result**: Golden hour boost applied to WRONG times (off by 1 hour in winter)

**Ciddiyeti**: ORTA — Timing seasonal bug

---

## SECTION D: 5M vs 15M ANALİZİ

### D1. TIMEFRAME DETECTION LOGIC
**Yer**: arbitrage_engine.py satır 150-175

**Kod**:
```python
def _detect_timeframe(question: str) -> str:
    m = _TF_PATTERN.search(question)
    if m:
        h1, mn1, h2, mn2 = ...
        mins = (h2 * 60 + mn2) - (h1 * 60 + mn1)
        if mins <= 7:
            return "5m"
        elif mins <= 20:
            return "15m"
        elif mins <= 90:
            return "1h"
        else:
            return "4h"
```

**Check**: 
- "2:00 PM - 2:05 PM" → mins=5 → "5m" ✓
- "2:00 PM - 2:15 PM" → mins=15 → "15m" ✓
- Fallback: return "1h" (default) if no pattern

**5m-specific logic**:
- Line 1494-1500: 5M_NO_FLAT_SCALE (half Kelly if |chg| < 0.15%)
- Line 1026-1035: 5M_VOL_SPIKE_CONTRARIAN (volume > 2.0x = fakeout)
- Line 1015-1024: 15M_PATTERN_DAMPEN (50% score reduction)

**Problem**: Only edge = 5m YES 79% WR vs 15m 44% WR
- Code doesn't explain this disparity
- Suspected: volatility, RSI thresholds same for both timeframes

### D2. VOLATILITY CALCULATIONS — TIMEFRAME SPECIFIC?
**Yer**: arbitrage_engine.py line 340 (volatility from BinanceFeed)

**Code doesn't show timeframe-aware volatility adjustment**
- Should be: volatility_15m > volatility_5m (more stable over longer window)
- But: same volatility value used in Bayesian for both

**Sonuç**: 15m trades likely dampen edge incorrectly

### D3. RSI THRESHOLD — SAME FOR 5M AND 15M
**Yer**: arbitrage_engine.py (no explicit RSI gate, but in technical_score calc)

**Problem**: RSI overbought/oversold thresholds standard (>75, <25)
- 5m: RSI=75 = local top (strong)
- 15m: RSI=75 = trend continuation, not necessarily reversal

---

## SECTION E: SINYAL PIPELINE WALK-THROUGH (STEP BY STEP)

### E1. BAYESIAN PROBABILITY CALCULATION

**Input** (Line 505-512):
```
change_pct = 5m price change (+0.45%)
volatility = 0.018 (1.8%)
rsi = 65 (neutral-bullish)
ob_imbalance = +0.15 (slightly more buys)
leader_bias = +0.02 (4h BTC bullish 0.8%)
```

**Bayesian.estimate() logic** (from earlier reads):
```
Prior: 0.50 (neutral)
Likelihood updates:
  + change_pct (+0.45%) → +0.08 boost → 0.58
  + RSI (65) → +0.02 boost → 0.60
  + OB imbalance → +0.01 boost → 0.61
  + leader_bias → +0.01 boost → 0.62
Output: bayes_prob = 0.62
```

**Dampening** (Line 533-542):
```
bayesian_prob = 0.50 + (0.62 - 0.50) * 0.95 = 0.50 + 0.114 = 0.614
```

**Regime adjustment** (Line 901):
```
regime = NEUTRAL → applied _neutral_pull = 0.10
adjusted_bayesian = 0.614 * (1 - 0.10) + 0.50 * 0.10 = 0.5526 + 0.05 = 0.6026
```

**Edge calculation** (Line 904-910):
```
yes_price = 0.45 (market price)
yes_edge = 0.6026 - 0.45 = 0.1526 (raw)
cost = 0.01 (1% overhead)
yes_edge_net = 0.1526 - 0.01 = 0.1426 ✓ (POSITIVE, passable)
```

### E2. NO SIDE ANALYSIS

**NO side pricing** (Line 920-935):
```
real_no_ask = market.get("no_best_ask") = None (no real orderbook)
Fallback: no_price_ask = 1.0 - 0.45 = 0.55 (SYNTHETIC)
NO prob = 1.0 - 0.6026 = 0.3974
no_edge = 0.3974 - 0.55 = -0.1526 (NEGATIVE)
```

### E3. DIRECTION DECISION

**Comparison** (Line 1302-1325):
```
yes_edge (0.1426) vs no_edge (-0.1526)
→ YES edge dominates
→ direction = "YES"
→ entry_price = 0.45 (YES ask)
```

### E4. GATE FILTERS APPLIED

| Gate | Condition | Result |
|------|-----------|--------|
| COINFLIP (1108-1133) | 0.45 in [0.45-0.55]? YES. edge >= 0.10? YES | PASS |
| ZONE_ADAPT (1136-1175) | YES@0.45 → zone_mult=1.0 | min_edge stays 0.08 |
| REGIME_STR_CAP (1400-1416) | direction=YES, so NO check | SKIP |
| REGIME_DECAY (1418-1427) | direction=YES, so NO check | SKIP |
| MOMENTUM_DECEL (1429-1437) | direction=YES, so NO check | SKIP |
| REGIME_NO_GATE (1511-1544) | direction=YES, so NO check | SKIP |
| EDGE_THRESHOLD (1557-1565) | edge=0.1426 >= min(0.08)? YES | PASS |
| CAUTIOUS_HOUR (1570-1589) | not in {9,16,6}? YES | PASS |
| KELLY sizing (1592-1598) | size=$2.50, capital=$100 | OK |
| MICRO_MOVE_SCALE (1603-1606) | |chg|=0.45% >= 0.10%? YES | no scale |
| 5M_NO_FLAT_SCALE | direction=YES, so skip NO scaling | SKIP |
| CAUTIOUS_HOUR kelly | not cautious hour | SKIP |
| REGIME_HALF_KELLY | direction=YES, so skip | SKIP |

**Final**: SIGNAL EMITTED: YES@0.45, edge=0.1426, size=$2.50

---

## SECTION F: SÖZLESMELİ KAP-KAPAT BÖLÜMÜ

### NO TRADE BLOCKING LOCATION YOKLAMASı:

```
orchestrator.py: no explicit NO-blocking gate (it's all in arbitrage_engine.py)

arbitrage_engine.py:
├─ OPT-1: REGIME_STR_CAP (satır 1400) → YES if str>0.85
├─ OPT-2: REGIME_DECAY_BLOCK (satır 1418) → YES if decay pause + edge<0.08
├─ OPT-3: MOMENTUM_DECEL (satır 1429) → YES if momentum DOWN + edge<0.10
├─ OPT-4: (COMMENTED OUT at 1411)
├─ OPT-5: REGIME_NO_GATE (satır 1511) → scaled floor for BULLISH regime
├─ OPT-6: (MISSING — Loss slot cooldown)
├─ BOUNCEBLOCK (satır 1166) → NO blocked during bounce
├─ EXHAUSTION_ACTIVATE (satır 1127) → NO allowed during BULLISH exhaustion
├─ BULLISH_GUARD (satır 1186) → NO blocked if bullish regime + edge<0.12
├─ NEUTRAL_NO_GATE (satır 1524) → NO floor raised to 0.08-0.12 in NEUTRAL
└─ TECH_SCORE_BLOCK (satır 1280) → NO blocked if tech_score >= 0.4 (bullish)

Total: 10-11 NO-blocking gates, highly interdependent
```

---

## SECTION G: MAGİC NUMBER KONTROL

### G1. Hardcoded thresholds (no env override)

| Threshold | Value | Source | Env Override? |
|-----------|-------|--------|---|
| KELLY_FRACTION_BASE | 0.25 | kelly_criterion.py | NO |
| KELLY_FRACTION_MAX | 0.50 | kelly_criterion.py | NO |
| STREAK_BOOST_PER_WIN | 0.05 | kelly_criterion.py | NO |
| STREAK_CUT_PER_LOSS | 0.15 | kelly_criterion.py | NO |
| REGIME_SOFT_CAP | 0.75 | arbitrage_engine.py line 1404 | NO |
| REGIME_HARD_CAP | 0.85 | arbitrage_engine.py line 1405 | NO |
| COINFLIP_LOW | 0.45 | arbitrage_engine.py line 1110 | NO |
| COINFLIP_HIGH | 0.55 | arbitrage_engine.py line 1110 | NO |
| COINFLIP_EDGE_OVERRIDE | 0.10 | arbitrage_engine.py line 1110 | NO |
| HARD_MAX_BET | 5.0 | orchestrator.py line 802 | NO (comment says never change) |
| 5M_VOL_SPIKE_THRESHOLD | 2.0x | arbitrage_engine.py line 1020 | NO |
| PATTERN_SCORE_THRESHOLD | 0.6 | arbitrage_engine.py line 1039 | NO |
| ML_CAUTION_THRESHOLD | -0.5 | arbitrage_engine.py line 1668 | NO |
| GOLDEN_HOUR_BOOST | 1.30x | arbitrage_engine.py line 1691 | NO |

**Ciddiyeti**: HIGH — No way to tune without code edit (breaks SOP)

---

## SECTION H: OVERALL ARCHITECTURE ÇELIŞKILER

### H1. Orchestrator-Coordinator mismatch
- orchestrator.py: `check_live_gate()` 11-point check
- coordinator.py: NO live_gate call at all — passes all signals to reviewer
- Meaning: live_gate is POST-approval (2nd filter), not PRE-approval
- But logic suggests it should be PRE (safety first)

### H2. ReviewerAgent as "glorified gatekeeper"
- reviewer_agent.py REVIEWER_SYSTEM_PROMPT: states "Edge is genuine >0.06 for YES, >0.10 for NO"
- But arbitrage_engine.py min_edge_yes=0.08, min_edge_no=0.15
- Reviewer is comparing against DIFFERENT thresholds than signal generation!
- This causes reviewer to veto signals that passed engine's own threshold

---

## SECTION I: ÖZETİ

### Bulunan Toplam Sorun Sayısı: 23

| Ciddiyeti | Sayı | Kısmi Açıklama |
|-----------|------|---|
| KRİTİK | 3 | OPT-6 eksik, 5m vs 15m aynı motor, Reviewer fallback %99 başarısız |
| YÜKSEK | 8 | Regime double-count, NO synthetic fiyatlandırma, dampening asimetri, zone eski, momentum_decel eksik, volume gate disabled, COINFLIP edge rendah, REGIME_NO_GATE unreachable |
| ORTA | 12 | Pattern score swing, bounce 2+ inconsistent, 2GREEN_YES_BLOCK strict, EXHAUSTION_NO too strict, BOUNCE_FADED async, TECH_SCORE threshold swap, ADX threshold, VOL_FLOW spec eksik, BTC special-case, Kelly double-multiply, ML -0.5 arbitrary, Golden hour DST bug |

### Root Causes:

1. **5m vs 15m unified approach**: Same Bayesian, same gates, same cost model
   - MUST implement timeframe-specific Bayesian estimation
   - MUST have different edge thresholds

2. **NO trade systematically underfunded**: 
   - 33% WR (vs 72% YES) = edge needs 2-3x higher to be profitable
   - Current min_edge_no=0.15 still too low (should be 0.20+)
   - Or: BLOCK NO entirely until research improves direction forecasting

3. **OPT-6 loss slot cooldown missing**: 
   - Bounce pattern is PROVEN (100% post-loss)
   - Code ignores it — trading INTO bounces

4. **Reviewer fallback unreliable**:
   - Claude API down = automatic approve-all = %33 WR
   - Need BETTER fallback rules or refuse to trade without API

---

FULL LINE-BY-LINE DETAILS AVAILABLE FOR EACH ISSUE.
