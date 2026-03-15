# Market Eşleştirme Kuralları

## Polymarket Crypto Market Formatı

Aktif crypto marketleri "X Up or Down" formatında gelir:

```
Bitcoin Up or Down - March 14, 12:30PM-12:45PM ET
Ethereum Up or Down - March 14, 12:30PM-12:45PM ET
Solana Up or Down - March 14, 12:30PM-12:45PM ET
```

Bu format:
- 15 dakikalık pencereler
- YES token = fiyat yükseldi (UP)
- NO token = fiyat düştü (DOWN)
- Her zaman **NORMAL polarity**

## Asset Anahtar Kelimeleri

| Asset | Market'te Aranacak Kelimeler |
|-------|------------------------------|
| BTC | bitcoin, btc |
| ETH | ethereum, eth, ether |
| SOL | solana, sol |
| XRP | ripple, xrp |
| DOGE | dogecoin, doge |
| BNB | bnb, binancecoin |
| HYPE | hyperliquid, hype |

Eşleştirme kuralı:
1. Sinyal asset'inin kelimelerinden en az biri market başlığında/açıklamasında olmalı
2. Başka bir asset'in kelimeleri de varsa → **ambiguous**, REJECT

## Polarity Tespiti

### Öncelik 1: "Up or Down" (kesin NORMAL)

```regex
\bup\s+or\s+down\b   (case insensitive)
```

Bu regex eşleşirse → NORMAL polarity (diğer keywordler görmezden gelinir).

### Öncelik 2: Keyword analizi

**NORMAL polarity** (UP → YES):
- above, higher, increases, rises, goes up, gains, exceeds

**INVERTED polarity** (UP → NO):
- below, lower, decreases, falls, falling, drops, declines, goes down

Kurallar:
- Yalnızca NORMAL keywordler → NORMAL
- Yalnızca INVERTED keywordler → INVERTED
- Her ikisi birden → AMBIGUOUS → REJECT
- Hiçbiri → AMBIGUOUS → REJECT

## Timing Eşleştirme

Sinyal horizon'u ile market resolution zamanı örtüşmelidir:

| Sinyal Horizon | Min Süre | Max Süre | İdeal |
|----------------|----------|----------|-------|
| 5m | 60s (1dk) | 600s (10dk) | 330s (5.5dk) |
| 15m | 120s (2dk) | 1800s (30dk) | 960s (16dk) |

- `time_to_resolution = market.end_time_utc - now_utc`
- Window dışı → reddedilir
- Window içi → midpoint'e yakınlık → `match_score` (0.0–1.0)

Birden fazla geçerli candidate varsa `match_score` yüksek olan seçilir.

## Yönlendirme Tablosu

| Sinyal Yönü | Market Polarity | Alınacak Token |
|-------------|-----------------|----------------|
| UP | NORMAL | YES |
| UP | INVERTED | NO |
| DOWN | NORMAL | NO |
| DOWN | INVERTED | YES |
| NO_TRADE | — | REJECT |

## Örnekler

### Örnek 1: BTC UP + "Up or Down"
```
Sinyal: asset=BTC, direction=UP, confidence=0.72, horizon=15m
Market: "Bitcoin Up or Down - March 15, 12:10PM-12:25PM ET"
        end_time = now + 12min = 720s

Eşleştirme:
  asset: "bitcoin" ✓
  timing: 720s ∈ [120, 1800] ✓
  polarity: "up or down" → NORMAL ✓
  match_score: |720 - 960| / 840 ≈ 0.71

Yönlendirme: UP + NORMAL → YES
Filtreler: conf=0.72 ≥ 0.58 ✓, liq=5000 ≥ 1000 ✓
  edge = 0.72 - 0.44 - 0.01 = 0.27 ≥ 0.02 ✓

Sonuç: YES token al, ask=0.44
```

### Örnek 2: ETH DOWN + "above"
```
Sinyal: asset=ETH, direction=DOWN, confidence=0.65, horizon=15m
Market: "Will ETH be above $3000 in 10 minutes?"
        end_time = now + 10min = 600s

Eşleştirme:
  asset: "eth" ✓
  timing: 600s ∈ [120, 1800] ✓
  polarity: "above" → NORMAL ✓

Yönlendirme: DOWN + NORMAL → NO
Filtreler: OK

Sonuç: NO token al
```

### Örnek 3: BTC ama ETH market
```
Sinyal: asset=BTC, direction=UP
Market: "Ethereum Up or Down - March 15"

Eşleştirme:
  asset: "bitcoin"/"btc" aranır → bulunamadı
  → ASSET_MISMATCH → REJECT
```

### Örnek 4: Düşük güven
```
Sinyal: asset=BTC, direction=UP, confidence=0.52
Eşleştirme: OK, yönlendirme: YES
Filtre: 0.52 < 0.58 min_confidence → LOW_CONFIDENCE → REJECT
```

## Desteklenmeyen Market Türleri

Bridge KAPSAM DIŞI market türleri:
- Oscar/ödül törenleri
- Spor sonuçları
- Siyasi olaylar
- Oyun/tahmin marketleri
- Genel fiyat marketleri ("Will BTC end 2026 above $100k?")

Bu marketler asset keyword eşleşmesi olsa bile timing window dışında kaldıklarından
otomatik olarak TIMING_TOO_FAR ile reddedilir.
