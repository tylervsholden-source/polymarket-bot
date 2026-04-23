# Polymarket AI Trading Bot

## Proje Amacı
$1000 USDC → $3000 USDC hedefiyle 20 günlük otomatik Polymarket trading botu.
Claude AI (Signal Agent) + Whale Tracker + Kelly Criterion risk motoru.

## Hızlı Başlangıç
```bash
pip install -r requirements.txt   # Bağımlılıkları kur
cp .env.example .env              # API key'leri doldur
python main.py --backtest         # Önce test et
python main.py                    # Canlıya al
python main.py --status           # Durum görüntüle
```

## Proje Yapısı
```
polymarket-bot/
├── CLAUDE.md                   ← Bu dosya (Claude Code okur)
├── main.py                     ← Giriş noktası
├── .env.example                ← API key şablonu
├── requirements.txt
├── agents/
│   ├── orchestrator.py         ← Ana koordinatör (5dk döngü)
│   ├── signal_agent.py         ← Claude AI ile market analizi
│   └── whale_tracker.py        ← Büyük pozisyon takibi
├── core/
│   ├── polymarket_client.py    ← CLOB API wrapper
│   └── position_manager.py     ← Pozisyon & P&L takibi
├── strategies/
│   └── kelly_criterion.py      ← Risk & pozisyon boyutlandırma
├── backtesting/
│   └── engine.py               ← Geçmiş veri ile test
└── docs/
    ├── architecture.md         ← Sistem mimarisi detayı
    ├── api_guide.md            ← API bağlantı kılavuzu
    └── strategy.md             ← Trading stratejisi açıklaması
```

## Mimari — Autonomous Multi-Agent Orchestration (v3, Mart 2026)

```
ORCHESTRATOR (adaptif döngü: 60-120sn)
    │
    ├── HealthMonitor          → Sistem sağlık kontrolü, hata oranı, backoff
    │
    ├── AgentCoordinator.run_cycle()
    │   │
    │   ├── PHASE 1: PARALLEL ──────────────────────────
    │   │   ├── ResearchAgent  → whale + smart_trader + regime + on-chain + sentiment
    │   │   └── SignalAgent    → 6-model ArbitrageEngine (Bayesian+Edge+Spread+Stoikov+Kelly+MC)
    │   │
    │   ├── PHASE 2: MERGE ─────────────────────────────
    │   │   → Sinyalleri research context ile zenginleştir
    │   │   → Confluence score hesapla (0-1)
    │   │   → Risk flag'leri tespit et
    │   │
    │   └── PHASE 3: SEQUENTIAL ────────────────────────
    │       └── ReviewerAgent  → Claude API ile APPROVE / VETO / REDUCE
    │           (API yoksa → rule-based fallback)
    │
    ├── AutonomousDecisionEngine.evaluate()  ← YENİ: Otonom karar motoru
    │   ├── Risk seviyesi sınıflandırma (LOW/MED/HIGH/CRITICAL)
    │   ├── Performans bazlı adaptif boyutlandırma
    │   ├── Loss streak / drawdown koruması
    │   ├── Volatilite rejimi adaptasyonu
    │   └── Karar: EXECUTE / EXECUTE_REDUCED / SKIP / DEFER
    │
    ├── Execute approved signals → Polymarket CLOB API
    │
    └── TradeAnalyzer.analyze_trade()  ← YENİ: Post-trade analiz
        ├── Root cause analysis (neden kazandı/kaybetti)
        ├── Sinyal doğruluk kontrolü (whale, regime, smart money)
        ├── Pattern eşleştirme (10+ bilinen pattern)
        └── Adaptif parametre önerileri
```

### Otonom Karar Akışı (v3 — Claude Code auto mode ilhamı)
```
Sinyal → AutonomousEngine.evaluate()
  ├── Performans snapshot güncelle (WR, streak, drawdown)
  ├── Risk skor hesapla (0-10, çoklu faktör)
  │     ├── Edge seviyesi
  │     ├── Yön (YES/NO risk farkı)
  │     ├── Risk flag sayısı
  │     ├── Confluence score
  │     ├── Pozisyon yoğunluğu
  │     └── Sermaye durumu
  ├── Adaptif boyut çarpanı belirle
  │     ├── Loss streak → küçült (×0.25-0.60)
  │     ├── Win streak → dikkat (×0.85)
  │     ├── Drawdown → savunma (×0.30-0.40)
  │     ├── Düşük sermaye → survival (×0.30)
  │     ├── Yüksek edge bonus (×1.2, max 1.0)
  │     └── Gece saatleri → düşük likidite (×0.70)
  └── Final: EXECUTE/REDUCED/SKIP + size_multiplier
```

### Resilience Katmanı (Bot ASLA Durmaz)
```
agents/resilience.py
├── @resilient              → Async fonksiyonları hata-güvenli yapar
├── with_retry()            → Exponential backoff ile retry
├── cycle_guard()           → Döngü timeout koruması
├── HealthMonitor           → Hata oranı, bellek, backoff hesaplama
└── infinite_loop()         → Durdurulamaz ana döngü wrapper
```

### Subagent Dosyaları
```
agents/subagents/
├── __init__.py          ← Package exports
├── base_agent.py        ← Abstract base (timeout, error handling)
├── research_agent.py    ← Piyasa araştırma (whale+smart+regime+enhanced)
├── signal_agent_v2.py   ← Sinyal üretimi (ArbitrageEngine wrapper)
├── reviewer_agent.py    ← Claude API trade reviewer (APPROVE/VETO/REDUCE)
└── coordinator.py       ← Hybrid orchestration hub

agents/
├── autonomous_engine.py ← Otonom karar motoru (risk sınıflandırma + adaptif boyut)
├── trade_analyzer.py    ← Post-trade analiz + pattern tanıma + öğrenme
└── resilience.py        ← Bot dayanıklılık katmanı (retry, guard, health)
```

### .env Ayarları
```
ENABLE_RESEARCH_AGENT=true     # Research agent'ı aç/kapa
ENABLE_REVIEWER_AGENT=true     # Reviewer agent'ı aç/kapa
REVIEWER_MODEL=claude-sonnet-4-20250514  # Reviewer için model
```

## Temel Kurallar (Değiştirme)
- Max tek pozisyon: portföyün %20'si (Kelly override yapmaz)
- Günlük stop-loss: -%15 → bot o gün durur
- Aynı anda max 5 açık pozisyon
- Min market hacmi: $5,000 USDC
- Min edge eşiği: 0.05 (AI tahmini - piyasa fiyatı)

## Geliştirme Talimatları
- Tüm loglar `loguru` ile yapılır
- API çağrıları try/except ile sarılı olmalı
- Yeni strateji eklerken `strategies/base_strategy.py` inherit et
- Test: `pytest tests/` ile çalıştır
- Async fonksiyonlar için `asyncio.run()` yerine `run_sync()` wrapper kullan

## Detaylı Dokümantasyon
@docs/architecture.md     ← Mimari ve veri akışı
@docs/api_guide.md        ← Polymarket API kurulum rehberi
@docs/strategy.md         ← Kelly Criterion ve trading mantığı

## Sim Sonuçları ve Optimizasyon Tarihi (2026-03-18)

### Sim Versiyonları
| Versiyon | WR | Trade | Piyasa | Notlar |
|----------|----|-------|--------|--------|
| v6 | %75 (15/20) | 20 NO | Güçlü BEARISH tek yön | COIN_LIMIT yok, 5 coin/slot |
| v7 | %65 (13/20) | 20 NO | Zayıflayan BEARISH | Bounce kayıpları dominant |
| v8 | %50 (4/4+) | 8+ NO | Choppy/dalgalı | COIN_LIMIT=2 + decay guard eklendi |
| v9 | TBD | - | - | Tüm 6 OPT aktif |

### v9 Optimizasyonları (Tümü Aktif)
1. **OPT-1: Regime Strength Cap** — `str > 0.75` → NO trade block. Aşırı bearish = aşırı satım = bounce. v6'da 5/5 LOSS, v8'de 3/4 LOSS bu bölgede.
2. **OPT-2: Max 1 Coin/Period** — COIN_LIMIT 2→1. Korelasyon %99, 2 coin = 2x risk 1x bilgi.
3. **OPT-3: Momentum Deceleration Guard** — Son 3 mum'da |change| azalıyorsa → bounce riski → NO block.
4. **OPT-4: Volume Confirmation Gate** — vol_ratio < 1.2x → düşük hacim = noise → NO block.
5. **OPT-5: Adaptive Edge Threshold** — min_edge = base + (regime_str × 0.08). Güçlü rejimde daha yüksek edge iste.
6. **OPT-6: Loss Slot Cooldown** — Kayıp olan slot'tan sonraki slot'u atla (dead cat bounce 1 periyot sürüyor).

### Kritik Keşifler (Derin Analiz)
- **Kayıplar edge eksikliğinden DEĞİL, yön tahmininden** — Edge 0.13-0.39 arası kayıp trade'lerde bile yüksek. Problem Bayesian direction forecasting.
- **Regime str > 0.70 = LOSS bölgesi** — Tüm sim'lerde güçlü regime ile kayıp korelasyonu.
- **Bounce pattern** — 2+ ardışık NO-win periyottan sonra %100 bounce geliyor (1 periyot sürüyor).
- **Bayesian overconfidence** — prob 0.80+ → gerçek WR %20. Dampening (YES 0.70x, NO 0.85x) uygulandı.
- **Sim-Live gap** — Sim'de NO %60-75 WR, canlıda %0 WR. Execution farkı araştırılmalı.

### Sinyal Pipeline Özeti
```
Bitstamp OHLCV → 7 weighted signal (momentum-first, contrarian kaldırıldı)
  → tanh() normalize → volume×lag×volatility multiplier
  → Bayesian log-odds update (2x strength) → confidence dampening
  → Edge = prob - price - costs → Direction = spot∧bayes alignment
  → 6 gate filter (regime cap, decay, decel, vol, adaptive edge, loss cooldown)
  → Kelly sizing → Trade
```

## Claude Çalışma Kuralları

### Doğrulama Zorunlu
- Bir görevi tamamlandı saymadan önce çalıştığını kanıtla (log, çıktı, test)
- "Çalışıyor olmalı" yeterli değil — gerçekten çalıştığını göster
- Değişiklik öncesi/sonrası davranış farkını netleştir

### Hata Düzeltme
- Hata raporu geldiğinde sormadan düzelt: log ve stack trace'e bak, root cause bul
- Geçici fix yapma — kalıcı çözüm uygula, senior developer standardı

### Karmaşık Görevler (3+ adım)
- Başlamadan önce planı yaz ve onayla
- Bir şeyler ters giderse dur ve yeniden planla, ilerlemeye devam etme

### Sadelik
- Minimal kod değişikliği — sadece gerekeni değiştir
- Tek seferlik işlemler için helper/abstraction ekleme
- "Daha zarif bir yol var mı?" sorusunu non-trivial değişikliklerde sor, basit fixlerde atla
