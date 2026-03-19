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
