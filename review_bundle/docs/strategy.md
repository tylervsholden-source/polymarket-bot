# Trading Stratejisi

## Hedef
$1,000 → $3,000 USDC — 20 günde %200 getiri

## 1. Kelly Criterion

### Formül
```
f* = (b×p - q) / b

b = net kazanç oranı = (1/price) - 1
p = tahmini kazanma olasılığı (AI tahmini)
q = 1 - p
```

### Örnek
- Market fiyatı: 0.40 (YES %40 olasılıklı görünüyor)
- AI tahmini: 0.60 (gerçekte %60)
- Edge: 0.20
- b = (1/0.40) - 1 = 1.5
- Kelly f = (1.5 × 0.60 - 0.40) / 1.5 = 0.333
- Yarı-Kelly: 0.167 → $1000'dan $167 yatır

### Neden Yarı-Kelly?
Tam Kelly teorik olarak optimal ama pratikte aşırı volatil.
Yarı-Kelly aynı uzun vadeli büyümeyi daha az risk ile sağlar.

## 2. Risk Kuralları

| Kural | Değer | Neden |
|-------|-------|-------|
| Max tek pozisyon | %20 | Konsantrasyon riski |
| Günlük stop-loss | -%15 | Ruin önleme |
| Max açık pozisyon | 5 | Çeşitlendirme |
| Min market hacmi | $5,000 | Likidite |
| Min edge | 0.05 | Düşük kalite filtresi |

## 3. Sinyal Sistemi

### Edge Hesabı
```
edge = AI_probability - market_price
```

- edge < 0.05 → Geç (avantaj yok)
- edge 0.05-0.10 → Küçük pozisyon
- edge 0.10-0.20 → Orta pozisyon
- edge > 0.20 → Büyük pozisyon (max %20 cap)

### Whale Tracker Desteği
- Son 2 saatte büyük alımlar → bullish sinyal
- Son 2 saatte büyük satışlar → bearish uyarı
- Whale yönü ile AI sinyali aynıysa → Yüksek güven

## 4. Hedef Yolculuğu

| Gün | Hedef Sermaye | Gerekli Günlük Getiri |
|-----|--------------|----------------------|
| 5   | $1,350       | ~%6.3                |
| 10  | $1,800       | ~%5.9                |
| 15  | $2,400       | ~%5.7                |
| 20  | $3,000       | ~%5.6                |

Ortalama ~%5.7 günlük getiri gerekiyor.
Bu yüksek bir hedeftir — backtest sonuçları gerçekçi beklenti oluşturur.

## 5. Çıkış Stratejisi

- Market kapandığında otomatik kapanır
- Erken çıkış: Manuel `position_manager.close()` ile
- Stop: Günlük -%15 otomatik durdurma
