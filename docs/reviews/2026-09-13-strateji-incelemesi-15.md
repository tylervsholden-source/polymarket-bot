# Günlük Strateji İncelemesi — 2026-09-13 (15. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum özeti
- Bu çalışma başladığında **iki açık PR** vardı (aynı `main` taban commit'inden
  (`762f1a9`) türeyen, paralel çalışan iki farklı "14. çalışma" oturumu):
  - #33 — `PositionManager.update_positions()`'ın NO pozisyon değerlemesinde
    `client.get_market()`'in hiç doldurmadığı `no_token_id`'yi kullanmaya
    çalışması, gerçek CLOB orderbook'unun hiç sorgulanmamasına yol açıyordu.
  - #32 — Monte Carlo çağrısının `MAX_POSITION_PCT` fallback'i (`0.10`),
    Kelly/PositionManager'ın kullandığı `0.20`'den farklıydı.
  - Her iki PR da izole `git worktree`'lerde checkout edilip bağımlılıklar
    kuruldu, `pytest tests/` çalıştırıldı: #33 için **606 passed, 2 skipped**
    (PR iddiasıyla birebir), #32 için **605 passed, 2 skipped** (PR iddiasıyla
    birebir). Dosya çakışması yok (`core/position_manager.py` vs
    `strategies/arbitrage_engine.py` + ayrı test dosyaları). Her ikisi de
    squash-merge edildi (main sırasıyla `92a4589` → `398b2d2`).
- `data/control.json`/`positions.json`/`.env` bu ortamda yok → gerçek API
  kimlik bilgisi veya canlı pozisyon yok, bugün kapatılacak/açılacak gerçek
  bir pozisyon yoktu.

## Bugünkü derin inceleme: hiç dokunulmamış canlı-yol dosyaları

Önceki 14 rapor `orchestrator.py`, `autonomous_engine.py`, `coordinator.py`,
`kelly_criterion.py`, `position_manager.py`, `trade_analyzer.py`,
`reviewer_agent.py`, `signal_agent_v2.py`, `research_agent.py`,
`polymarket_client.py`, `resilience.py` ve MC tarafını zaten inceledi.
Repoda ayrıca `main.py`'nin import zincirine hiç girmeyen çok sayıda
(418 dosya) `incident_bundle/`, `incident_bundle_v2/`, `review_bundle/`,
`calibration/`, `crypto_directional/`, `operator_layer/` klasörü var — bunlar
grep ile doğrulandı: `agents/orchestrator.py` veya `main.py` bu klasörlerden
hiçbir şey import etmiyor, canlı yolu etkilemiyorlar, dokunulmadı.

Bugün canlı yola gerçekten bağlı ama hiç incelenmemiş iki dosyaya odaklanıldı:
`agents/subagents/orderflow_agent.py` (566 satır, `coordinator.py` üzerinden
paralel çalışıyor) ve `agents/latency_arb.py` (541 satır,
`orchestrator.py`'de başlatılıyor, `arbitrage_engine.py`'ye bağlı).

1. **`orderflow_agent.py` + `research_agent.py` + `signal_agent_v2.py` order
   flow zinciri** — `OrderFlowAgent.execute()` → `CoordinatorResult`'ta
   `research_result.orderflow_data`'ya merge → `get_market_context()` →
   `EnrichedSignal.orderflow_*` alanları → `_compute_confluence()` (ağırlık
   2.5, en yüksek ağırlıklardan biri) ve `_detect_risk_flags()`
   (`ORDERFLOW_OPPOSITION`) satır satır izlendi. Tamamı doğru bağlı; veri
   akışında kopukluk veya sessizce atlanan alan yok.
2. **`agents/latency_arb.py`** — `LatencyArbEngine._find_market_for_spike()`
   ve `_can_trade()` metodları tanımlı ama grep ile doğrulandı: repo genelinde
   **hiçbir yerden çağrılmıyorlar** — dosyanın kendi docstring'i
   ("5. Spike yönüne göre YES/NO token al") bu yolu tarif etse de, canlı kodda
   sadece `get_spike_boost()` kullanılıyor (`arbitrage_engine.py:749`), yani
   spike'lar asla doğrudan emir tetiklemiyor, sadece Bayesian olasılığa yumuşak
   bir boost olarak giriyor. `_orders_placed`/`_orders_skipped` sayaçları bu
   yüzden hep 0 kalıyor (`get_stats()` yanıltıcı ama zararsız — sadece log/
   dashboard görünürlüğü, davranışı etkilemiyor). Bugün dokunulmadı (CLAUDE.md
   "Sadelik": ölü kodu "bağlamak" davranış değişikliği ve ayrı bir tasarım
   kararı gerektirir, tek satırlık bir düzeltme değil — 14. çalışmanın MC
   `viable` gate kararıyla aynı emsal).

## Bugün bulunan ve düzeltilen gerçek hata: "TÜM EXTERNAL BOOST'LAR DEVRE DIŞI" resetinin SPIKE/MTF/LEAD_LAG'ı gerçekte hiç kapatmaması

### Kod incelemesi
`strategies/arbitrage_engine.py`'de (`9b5fd52` commit'inden beri, hiçbir
önceki inceleme bu bloğa dokunmamış) `_evaluate_market()` içinde şu blok var:

```python
# TÜM EXTERNAL BOOST'LAR DEVRE DIŞI — 5dk window için macro sinyaller zararlı
# YES %70 WR vs NO %35 WR → boost'lar sürekli bearish push yapıyordu
# Sadece Bayesian core (spot price action) kalıyor
# Kapatılan: SPIKE, MTF, LEAD_LAG, FUNDING, LS_RATIO, LIQUIDATION,
#            SPX_CORR, FNG, ENHANCED (multi-exchange, options, whale, social)
bayesian_prob = _pre_boost_prob  # Tüm boost'ları sıfırla, sadece core Bayesian
```

Bu yorumun listelediği 9 sinyalden 6'sı (FUNDING, LS_RATIO, LIQUIDATION,
SPX_CORR, FNG, ENHANCED) gerçekten devre dışı: `bayesian_prob = ... + boost`
satırları yorum satırına çevrilmiş (`# FIX: X boost DISABLED — ...`). Ama
**SPIKE, MTF ve LEAD_LAG için bu hiç yapılmamış** — reset satırından hemen
sonra gelen üç blok, boost'u hesaplayıp doğrudan `bayesian_prob`'a
uyguluyordu:

```python
bayesian_prob = _pre_boost_prob
# ── LATENCY ARB SPIKE BOOST ──
if abs(_spike_boost) > 0.005:
    bayesian_prob = max(0.05, min(0.95, bayesian_prob + _spike_boost))   # <- hiç kapatılmamış
# ── MULTI-TIMEFRAME CONSENSUS ──
if abs(_mtf_boost) > 0.003:
    bayesian_prob = max(0.05, min(0.95, bayesian_prob + _mtf_boost))      # <- hiç kapatılmamış
elif ...:  # MIXED dampen
    bayesian_prob = bayesian_prob * (1 - _dampen) + 0.50 * _dampen        # <- hiç kapatılmamış
# ── CROSS-EXCHANGE LEAD-LAG ──
if abs(_xex.get("boost", 0)) > 0.003:
    bayesian_prob = max(0.05, min(0.95, bayesian_prob + _xex["boost"]))   # <- hiç kapatılmamış
```

Sondaki `_TOTAL_BOOST_CAP = 0.04` agregat sınırı bu üçünün toplam etkisini
±0.04 ile sınırlıyor ama **sıfırlamıyor** — tam olarak reset'in önlemeye
çalıştığı şey.

### Etki
Somut senaryo (bugün eklenen regresyon testinde reprodüklendi): güçlü bullish
BTC 15dk marketi, spot momentum tek başına `edge=0.242` (`P=0.65`)
üretiyorken, SPIKE (+0.02) + MTF (+0.03) + LEAD_LAG (+0.03) aynı yönde
tetiklendiğinde toplam +0.08 boost `_TOTAL_BOOST_CAP` ile +0.04'e kırpılıyor
ve **`edge=0.282`'ye, `P=0.69`'a çıkıyordu** — reset'in "sadece core Bayesian
kalsın" niyetinin doğrudan ihlali. Bu tam olarak CLAUDE.md'nin belgelediği
kayıp örüntüsüyle (harici makro boost'ların kalıcı bearish/aşırı-güven push'u
yaratması, NO WR'ını %35'e düşürmesi) aynı sınıf risk — sadece YES tarafında,
edge'i olduğundan yüksek göstererek, aslında sınırda olan trade'leri
(`min_edge_yes=0.12`) yapay olarak eşiğin üstüne itiyor veya Kelly boyutunu
büyütüyor.

### Düzeltme
SPIKE, MTF (hem aligned-boost hem mixed-dampen dalı) ve LEAD_LAG bloklarındaki
`bayesian_prob = ...` satırları, diğer 6 sinyalle aynı stilde (`# FIX: X
boost DISABLED — ...` yorumu + satırı yorum haline getirme) devre dışı
bırakıldı. Loglama korundu (artık `(DISABLED)` etiketiyle) — teşhis için spike/
mtf/lead-lag'ın ne zaman tetiklendiğini görmeye devam ediyoruz, sadece
`bayesian_prob`'u artık değiştirmiyorlar.

`tests/test_disabled_boosts_stay_disabled.py` eklendi: aynı market/BinanceFeed
senaryosunu (test_execution_path.py'deki gibi) önce nötr spike/mtf/lead-lag
kaynaklarıyla, sonra üçünü de aynı yönde sert tetikleyen mock'larla çalıştırıp
`bayesian_prob`'un **aynı** kalması gerektiğini doğruluyor. Düzeltme öncesi
kodda test **başarısız oluyor** (0.65 → 0.69, log'da `SPIKE_BOOST`/
`MTF_CONSENSUS`/`LEAD_LAG`/`BOOST_CAP` satırlarıyla doğrulandı), düzeltme
sonrası **geçiyor** — regresyonu gerçekten yakaladığı doğrulandı.

## Doğrulama
- `python3 -c "import agents.orchestrator; import strategies.arbitrage_engine"` → hatasız.
- Düzeltme öncesi (`git stash` ile geçici geri alma): yeni test **FAIL**
  (`bayesian_prob` 0.65 → 0.69).
- Düzeltme sonrası: `pytest tests/` → **608 passed, 2 skipped** (606 → 608:
  1 yeni test dosyası, mevcut testlerden hiçbiri bozulmadı).
- `git status` → yalnızca amaçlanan iki değişiklik
  (`strategies/arbitrage_engine.py` + yeni test dosyası);
  `data/autonomous_state.json` gibi test çalıştırma yan etkileri
  commit'lenmeden geri alındı.

## Sonuç
15. çalışma önce paralel çalışan iki "14. çalışma" oturumunun PR'larını (#33
NO orderbook fix, #32 MC position-pct fix) izole worktree'lerde doğrulayıp
merge etti, sonra canlı yola bağlı ama hiç incelenmemiş iki dosyaya
(`orderflow_agent.py` zinciri, `latency_arb.py`) odaklandı. OrderFlow zincirinde
hata bulunamadı; `latency_arb.py`'de dead-code bulundu ama davranışı
etkilemediği için (sadece log/dashboard) bugün bağlanmadı. Asıl bulgu
`arbitrage_engine.py`'de: "tüm external boost'ları kapat" resetinin kendi
yorumunda listelediği 9 sinyalden 3'ünü (SPIKE, MTF, LEAD_LAG) hiç
kapatmadığı — CLAUDE.md'nin belgelediği "harici makro boost'lar kalıcı
bearish/aşırı-güven push'u yaratıyor" kayıp örüntüsüyle aynı sınıf, ama
sessizce YES edge'ini şişiren bir hata. Düzeltme minimal (3 blok, diğer 6
sinyalle birebir aynı stil) ve regresyon testiyle kilitlendi (düzeltme öncesi
kodda test'in gerçekten fail ettiği doğrulandı).
