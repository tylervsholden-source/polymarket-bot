---
name: trade-memory
description: |
  Polymarket trading bot'unun canlı hafıza ve öğrenme sistemi. Her trade sonucu, sinyal kalitesi ve pattern'leri sürekli analiz eder. Kullanıcı bot performansını sorduğunda, "nasıl gidiyor", "ne öğrendin", "hangi coin iyi", "trade analiz" gibi sorularda veya herhangi bir trade kararı verilmeden önce bu skill MUTLAKA tetiklenmeli. Bot kodunda değişiklik yapmadan önce de bu hafızaya danışılmalı.
---

# Trade Memory — Canlı Öğrenme Sistemi

Bu skill, Polymarket trading bot'unun tüm trade geçmişini analiz ederek sürekli güncellenen bir "hafıza" oluşturur. Her çalıştırıldığında `data/positions.json` ve shadow journal'ları okur, pattern'leri çıkarır ve `data/trade_memory.json` dosyasına yazar.

## Ne Zaman Kullanılır

- Kullanıcı "nasıl gidiyor", "performans", "analiz" dediğinde
- Trade stratejisi değişikliği düşünüldüğünde
- Herhangi bir bot parametresi ayarlanmadan önce (min_edge, coin blacklist, vb.)
- "Ne öğrendin", "pattern ne", "hangi coin" sorularında
- Yeni bir döngü öncesi karar desteği gerektiğinde

## Nasıl Çalışır

### 1. Analiz Script'ini Çalıştır

```bash
cd /sessions/inspiring-youthful-maxwell/mnt/Polymarket
python3 .skills/trade-memory/scripts/analyze_trades.py
```

Bu script:
- `data/positions.json` → tüm kapalı trade'leri okur
- Coin, timeframe, direction, entry_price, sonuç bazlı pattern analizi yapar
- Loss streak pattern'leri tespit eder
- Son 24 saatteki trend'i önceki döneme karşılaştırır
- Sonuçları `data/trade_memory.json`'a yazar

### 2. Hafızayı Oku

```bash
cat data/trade_memory.json
```

Bu dosya şu bölümleri içerir:

- **summary**: Genel performans (WR, PnL, sermaye)
- **rules**: Veriden çıkarılmış kesin kurallar (örn: "15m NO trade yapma")
- **coin_report**: Her coin için detaylı WR/PnL raporu
- **timeframe_report**: Her zaman dilimi için YES/NO ayrımıyla performans
- **entry_price_zones**: Hangi fiyat aralığında WR nasıl
- **loss_patterns**: Loss streak analizi ve tetikleyicileri
- **trend**: Son 24h vs önceki dönem karşılaştırması
- **warnings**: Aktif uyarılar (düşen WR, yüksek loss streak, vb.)
- **updated**: Son güncelleme zamanı

### 3. Kuralları Uygula

Hafızadan çıkan her kural, bot kodundaki karar mekanizmasına uygulanabilir.
Kurallar kanıta dayalıdır — her birinde sample size ve confidence interval bilgisi vardır.

Kural örneği:
```json
{
  "rule": "NO_15M_BLOCK",
  "action": "15m timeframe'de NO trade yapma",
  "evidence": "15m NO: 12W/25L = 32% WR, -$29.47 PnL",
  "confidence": "HIGH",
  "sample_size": 37
}
```

Confidence seviyeleri:
- **HIGH**: 50+ trade, pattern net (±5% margin)
- **MEDIUM**: 20-50 trade, pattern var ama varyans yüksek
- **LOW**: <20 trade, erken gösterge — izlemeye devam

### 4. Kullanıcıya Rapor Verirken

Hafızadan çıkan bilgiyi sade Türkçe ile aktar. Teknik jargonu minimize et.
Şöyle bir format iyi çalışır:

```
📊 Son Durum:
- 412 trade, %60 WR, toplam PnL: +$325
- Sermaye: $21.89 (başlangıç: $500)

✅ İyi Çalışan:
- HYPE: %88 WR (en iyi coin)
- 5m YES: %79 WR (altın standart)
- Entry price 0.50-0.65: %69 WR

⚠️ Dikkat:
- NO trade'ler: %29 WR (para kaybettiriyor)
- 15m timeframe: %44 WR (5m'in çok altında)
- Entry <0.35: %20 WR (ucuz = tehlikeli)

🚫 Yapma:
- 4h trade: 0/3 = %0 WR
- 15m + NO: 12/37 = %32 WR
```

## Dosya Yapısı

```
.skills/trade-memory/
├── SKILL.md              ← Bu dosya
└── scripts/
    └── analyze_trades.py ← Otomatik analiz script'i
```

Çıktı:
```
data/trade_memory.json    ← Canlı hafıza dosyası (script tarafından yazılır)
```
