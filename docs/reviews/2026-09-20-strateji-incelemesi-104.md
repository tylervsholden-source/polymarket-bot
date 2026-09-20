# Günlük Strateji İncelemesi — 2026-09-20 (104. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = `f47c5cc` (#186 merge edilmiş; 103. turun
iki paralel oturumu — cycle risk-budget'ın sim modda un-floored real balance
kullanması ve `/api/status`'ta `position_meta.json` merge'inin
`positions.json` override'ı tarafından ezilmesi — main'e girmiş). Bu branch
(`claude/brave-faraday-o1xotg`) `origin/main` ile eşit başladı.

Konteynerde çalışan bir bot instance'ı yok (`data/status.json`,
`data/control.json`, `data/positions.json` mevcut değil; `.env` yok; canlı
Polymarket/Anthropic API erişimi yok) — %10 hedefine karşı gerçek zamanlı
sermaye ilerlemesi bu oturumdan doğrulanamıyor. Katkı, önceki turlarda
olduğu gibi kod/strateji doğruluğu seviyesinde devam etti: `agents/
orchestrator.py`, `agents/autonomous_engine.py`, `core/position_manager.py`,
`strategies/kelly_criterion.py`, `strategies/arbitrage_engine.py`,
`strategies/edge_model.py`, `strategies/bayesian.py`, `control_plane/*.py`,
`execution_realism/*.py`, `agents/subagents/*.py` canlı ticaret yoluna
odaklanarak satır satır tarandı.

## Baseline doğrulama
- `pip install -r requirements.txt` çalıştırıldı.
- `python3 -m pytest tests/ calibration/tests execution_realism/tests signal_bridge/tests crypto_directional/tests -q`
  → **1773 passed, 4 skipped** (bu turun düzeltmesi öncesi başlangıç durumu).
- `data/autonomous_state.json` yan etkisi (bilinen davranış, testler
  çalıştıkça state dosyasına yazıyor) her çalıştırma sonrası
  `git checkout -- data/autonomous_state.json` ile geri alındı.

## Bulunan ve düzeltilen hata: Regime Decay Guard (v8) hesaplanıyor ama hiçbir yerde okunmuyordu

CLAUDE.md'nin sim tarihçesi tablosu v8'i şöyle tanımlıyor: "COIN_LIMIT=2 +
decay guard eklendi". `strategies/arbitrage_engine.py::analyze()` bu guard'ı
tam olarak uyguluyor görünüyordu:

```python
# ── Regime decay detection (v8) ──────────────────────────────────────
current_str = self._current_regime.get("strength", 0.0)
if current_str > self._regime_strength_peak:
    self._regime_strength_peak = current_str
    self._regime_decay_pause = False
decay = self._regime_strength_peak - current_str
...
elif decay >= 0.20 and self._regime_strength_peak >= 0.40:
    if not self._regime_decay_pause:
        logger.warning(
            f"REGIME_DECAY: peak={self._regime_strength_peak:.2f} → "
            f"now={current_str:.2f} (decay={decay:.2f}) → NO trade'ler 1 cycle DURDU"
        )
    self._regime_decay_pause = True
```

Peak regime strength'ten hızlı bir düşüş (ör. 0.85 → 0.50, decay=0.35) tespit
edilince `self._regime_decay_pause = True` set ediliyor ve log "NO trade'ler
1 cycle DURDU" diyor — tam olarak CLAUDE.md'nin "Kritik Keşifler" bölümünün
belgelediği dead-cat-bounce penceresi ("2+ ardışık NO-win periyottan sonra
%100 bounce geliyor").

Ancak `self._regime_decay_pause` repo genelinde hiçbir `if` koşulunda
okunmuyordu (`grep -rn "regime_decay_pause"` sadece atama satırlarını
buluyor). `_evaluate_market()`'in NO yönü seçim mantığı — hem başlangıç
`_no_viable` hesabı hem de sonraki tüm reaktivasyon dalları
(`3GREEN_NO_ACTIVATE`, `EXHAUSTION_NO_ACTIVATE`, `CANDLE_NO_ACTIVATE`,
`TF_CONFLICT_FLIP_NO`) — bu bayrağa hiç bakmıyordu. Sonuç: guard her cycle
çalışıyor, log'da "durdu" diyor, ama gerçekte hiçbir NO trade'i engellemiyordu.
Bu, aynı dosyada daha önce düzeltilmiş `momentum_decelerating` (OPT-3) ve
`_spot_bearish_for_no` hatalarıyla birebir aynı sınıf: "hesaplanan ama hiç
okunmayan güvenlik bayrağı".

`agents/orchestrator.py`'de de aynı özelliğin ölü bir kopyası var
(satır 324-325: `self._regime_strength_history` / `self._regime_decay_pause`
`__init__`'te set edilip bir daha hiç dokunulmuyor) — bu, özelliğin
kablolamasının hiçbir zaman tamamlanmadığını doğruluyor. Bu ölü alanlara
dokunulmadı (temizlik, davranışı etkilemiyor).

**Somut senaryo**: BTC 4h regime strength cycle N'de 0.85'e (güçlü BEARISH)
ulaşıyor. Cycle N+2'de 0.50'ye düşüyor (klasik dead-cat-bounce) →
`decay=0.35≥0.20`, `peak=0.85≥0.40` → guard tetikleniyor, `_regime_decay_pause
= True`. Aynı cycle'da `no_edge=0.20>0.15`, `no_price_ask` eşik üstü, gerçek
orderbook, sağlıklı NO tarafı, spot yükselmiyor → tüm `_no_viable` koşulları
sağlanıyor → NO sinyali üretiliyor, Kelly ile boyutlandırılıyor, coordinator/
reviewer/autonomous engine üzerinden canlıya gidiyor — guard'ın kendi log
mesajının "durduruldu" dediği tam o trade.

### Düzeltme
`strategies/arbitrage_engine.py`, "Pick the better viable direction"
bloğundan hemen önce (satır ~1535), tüm reaktivasyon dallarından *sonra*
uygulanan tek bir enforcement noktası eklendi — böylece hiçbir dal guard'ı
bypass edemiyor:

```python
# ── REGIME DECAY GUARD (v8) ──────────────────────────────────
if self._regime_decay_pause and _no_viable:
    _no_viable = False
    logger.info(
        f"REGIME_DECAY_BLOCK_NO: {question[:35]} | "
        f"peak={self._regime_strength_peak:.2f} decay-pause active → NO blocked"
    )
```

Başlangıç `_no_viable` tanımına (satır 1194-1198) sadece `and not
self._regime_decay_pause` eklemek yetersiz kalırdı — sonraki dört dal
(3GREEN/EXHAUSTION/CANDLE/TF_CONFLICT) `_no_viable`'ı yeniden `True`
yapabiliyor; bu yüzden guard, direction seçiminden hemen önce, en son
uygulanan kontrol olarak eklendi (MACRO_TREND_BLOCK_NO ile aynı desen).

### Test
`tests/test_regime_decay_guard_blocks_no.py` (2 yeni test, `tests/
test_no_viable_missing_spot_guard.py`'nin desenini izliyor):
- `test_no_direction_blocked_while_regime_decay_pause_active` — düşen spot
  (`change_pct=-0.50%`) normalde NO seçtirir; `_regime_decay_pause=True`
  iken sinyal `None` kalmalı. Düzeltme öncesi `git stash` ile doğrulandı:
  test FAIL ediyor (NO $4.00 boyutla üretiliyor) — hatayı birebir yakalıyor.
  Düzeltme sonrası PASS.
- `test_no_direction_is_selected_once_decay_pause_is_not_active` —
  regresyon-karşıtı sanity check: `_regime_decay_pause=False` iken aynı
  senaryo normal şekilde NO üretmeli; düzeltme guard'ı koşulsuz
  engellemiyor.

### Tam suite
- Düzeltme öncesi (baseline): **1773 passed, 4 skipped**.
- Düzeltme sonrası: **1775 passed, 4 skipped** (+2 yeni test, 0 regresyon).

## Önce kontrol edilen, hatasız çıkan alanlar
- `agents/orchestrator.py` — `compute_bet_size`, `compute_cycle_risk_budget`,
  `apply_risk_size_multiplier`/`apply_adaptive_bet_multiplier`, EXECUTE
  APPROVED SIGNALS döngüsü, `_execute_approved_orders`, `_sync_real_balance`,
  MAX_DIRECTIONAL/cycle_budget kablolaması, maker/bond faz geçişi.
- `agents/autonomous_engine.py` — risk skorlama, boyut çarpanları, SKIP
  koruma mantığı, performans/drawdown takibi.
- `core/position_manager.py` (tüm dosya) — `available_capital`,
  `close_position`/`close_position_neutral`, günlük loss roll, order-fill
  reconciliation, YES/NO değerleme.
- `strategies/kelly_criterion.py`, `strategies/edge_model.py`,
  `strategies/bayesian.py`.
- `agents/subagents/coordinator.py` (REDUCE/VETO kablolaması,
  re-enrichment/re-sort), `agents/subagents/reviewer_agent.py`
  (`trade_number` eşleşmesi, `suggested_size_pct` clamp), `agents/
  subagents/signal_agent_v2.py`, `agents/subagents/research_agent.py`.
- `agents/smart_trader_tracker.py`.
- `strategies/maker_engine.py` — `DEFAULT_POOLS` doğrulandı, maker pool
  varsayılan %0, yani şu an canlı ticareti etkilemiyor.
- `control_plane/live_gate.py`, `control_plane/expiry_guard.py`,
  `control_plane/reentry_guard.py`, `control_plane/entry_window_guard.py`.

## Sermaye/performans notu
Bu oturumda çalışan bir bot instance'ı yok — %10 hedefine karşı gerçek
ilerleme doğrulanamıyor. Önceki turlarda düzeltilen kritik canlı-karar
hataları (edge wiring, OPT-7 EXPIRED streak, NO-direction Kelly penaltısı,
WS coin evreni, cycle risk-budget floor, position_meta merge sırası) hâlâ
yerinde ve testlerle korunuyor. Bu turda aynı sınıftan ("hesaplanan ama hiç
okunmayan güvenlik bayrağı") üçüncü bir örnek daha bulundu ve düzeltildi —
Regime Decay Guard (v8), tam da CLAUDE.md'nin belgelediği bounce riski
penceresinde canlı sermayeyi koruma amacındaydı ama hiç etkili değildi.

## Sonuç
`strategies/arbitrage_engine.py`'de Regime Decay Guard'ın hiç uygulanmadığı
hatası düzeltildi ve regresyon testiyle doğrulandı. `agents/
orchestrator.py`'deki ölü kopya alanlar (`_regime_strength_history`,
`_regime_decay_pause`) not edildi ama davranışı etkilemediği için
dokunulmadı (sadelik ilkesi — minimal değişiklik).

## Sıradaki tur için notlar
- `agents/orchestrator.py:324-325`'teki ölü `_regime_strength_history`/
  `_regime_decay_pause` alanları temizlik adayı (davranış etkisi yok,
  düşük öncelik).
- Bu turda kapsamlı taranan alanlar (yukarıdaki liste) kısa vadede tekrar
  bakmak için düşük getiri.
- Sonraki adaylar (103b'nin notlarından hâlâ geçerli, henüz commit
  geçmişinde düzeltme görünmüyor): `operator_layer/` (chamber API —
  `pnl.py` hariç satır satır okunmadı), `agents/enhanced_signals.py`,
  `agents/hit_rate_tracker.py`, `agents/copytrade.py`. Ayrıca onay kuyruğu
  / doğrudan emir yolu çelişkisi (`docs/APPROVAL_WORKFLOW_SPEC.md` vs.
  `agents/orchestrator.py` doğrudan emir yolu) hâlâ kullanıcı kararı
  bekliyor — büyük mimari değişiklik, otonom olarak değiştirilmedi.
