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

## Mimari

```
ORCHESTRATOR (5dk döngü)
    ├── signal_agent.py    → Claude AI'a market sorusu gönder → olasılık tahmini al
    ├── whale_tracker.py   → Son 2 saatin büyük işlemlerini tara
    ├── kelly_criterion.py → Edge varsa bet büyüklüğünü hesapla
    └── polymarket_client  → Emri Polymarket CLOB API'ye gönder
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
