# Bridge Katmanı Teknik Spesifikasyonu

## Genel Bakış

`signal_bridge/` modülü, `crypto_directional` tahmin çıktısını Polymarket işlem niyetine dönüştürür.

```
crypto_directional  →  signal_bridge  →  polymarket_bot
    UP/DOWN/NO_TRADE      YES/NO/REJECT      CLOB emri
```

## Mimari Akış

```
DirectionalSignal
    │
    ▼
market_matcher.py  ──────────────────────────────
    │  match_markets(signal, candidates) → list    │
    │  best_match(...) → Optional[MarketMatch]     │
    │                                              │
    │  Filtre sırası:                              │
    │  1. status == "active"?                      │
    │  2. Asset eşleşiyor mu?                      │
    │  3. time-to-resolution window içinde mi?     │
    │  4. Polarity tespit edilebiliyor mu?          │
    │  → match_score ile sıralama                  │
    │                                              │
    ▼                                              │
MarketMatchResult                                  │
    │                                              │
    ▼                                              │
signal_router.py  ──────────────────────────────  │
    │  route(signal, match) → TradeIntent          │
    │                                              │
    │  Yönlendirme tablosu:                        │
    │  UP   + NORMAL   → YES                       │
    │  UP   + INVERTED → NO                        │
    │  DOWN + NORMAL   → NO                        │
    │  DOWN + INVERTED → YES                       │
    │  NO_TRADE / AMBIGUOUS / REJECTED → REJECT    │
    │                                              │
    ▼                                              │
TradeIntent (pre-filter)                           │
    │                                              │
    ▼                                              │
trade_filter.py  ───────────────────────────────  │
    │  apply_filters_with_candidate(               │
    │      intent, candidate, config) → TradeIntent│
    │                                              │
    │  Filtre sırası:                              │
    │  1. confidence >= min_confidence             │
    │  2. liquidity  >= min_liquidity              │
    │  3. spread     <= max_spread                 │
    │  4. edge       >= min_edge_after_fee         │
    │                                              │
    ▼
TradeIntent (final)
    mapped_side: YES / NO / REJECT
    token_id: hangi token alınacak
    ask_price: ödenen fiyat
    expected_edge: beklenen avantaj
```

## Veri Modelleri

### DirectionalSignal (giriş)
| Alan | Tip | Açıklama |
|------|-----|----------|
| asset | str | "BTC", "ETH", "SOL", ... |
| horizon_minutes | int | 5 veya 15 |
| timestamp_utc | datetime | Sinyal üretim zamanı |
| direction | Direction | UP / DOWN / NO_TRADE |
| confidence | float | 0.0–1.0 model güveni |
| model_version | str | İzlenebilirlik |

### PolymarketCandidate (polymarket_bot'tan)
| Alan | Tip | Açıklama |
|------|-----|----------|
| market_id | str | Polymarket market ID |
| title | str | Market soru metni |
| end_time_utc | datetime | Resolution zamanı |
| yes_token_id | str | YES token CLOB ID |
| no_token_id | str | NO token CLOB ID |
| best_ask_yes | float | YES alım fiyatı |
| best_bid_yes | float | YES satış fiyatı |
| liquidity | float | Anlık likidite (USDC) |
| status | str | "active" / "closed" / "resolved" |

### TradeIntent (çıktı)
| Alan | Tip | Açıklama |
|------|-----|----------|
| mapped_side | TradeSide | YES / NO / REJECT |
| token_id | str | Alınacak token ID'si |
| ask_price | float | Ödenen fiyat |
| expected_edge | float | confidence − ask − fee |
| rejection_reason | RejectionReason | None ise işlem açılabilir |
| rationale | str | İnsan okunabilir açıklama |

## Polarity ve Yönlendirme

Polymarket'in "X Up or Down" formatı:
- YES token → fiyat yükseldi (UP)
- NO token → fiyat düştü (DOWN)
- Bu format **daima NORMAL polarity** olarak sınıflandırılır

Diğer market türleri ("above X", "fall below Y") keyword analizi ile sınıflandırılır.

```
direction=UP,   polarity=NORMAL   → YES token al
direction=UP,   polarity=INVERTED → NO  token al
direction=DOWN, polarity=NORMAL   → NO  token al
direction=DOWN, polarity=INVERTED → YES token al
```

## Edge Formülü

```
edge = signal.confidence − ask_price − assumed_taker_fee_pct
```

`ask_price`:
- YES tarafı → `candidate.best_ask_yes`
- NO tarafı → `candidate.best_ask_no`

Edge threshold geçilmezse → `NEGATIVE_EDGE` ile REJECT.

## Timing Windows

| Horizon | min_sec | max_sec | Açıklama |
|---------|---------|---------|----------|
| 5m sinyal | 60s | 600s | 1–10 dakika içinde resolve |
| 15m sinyal | 120s | 1800s | 2–30 dakika içinde resolve |

Pencere dışı → `TIMING_TOO_CLOSE` veya `TIMING_TOO_FAR` ile REJECT.
Timing skoru: window midpoint'e yakınlık → 0.0–1.0 (1.0 = ideal).

## Konfigürasyon (BridgeConfig)

```python
BridgeConfig(
    min_confidence=0.58,          # Model güveni eşiği
    min_liquidity=1_000.0,        # USDC likidite eşiği
    max_spread=0.05,              # Maksimum bid-ask spreadi
    min_edge_after_fee=0.02,      # Fee sonrası minimum edge
    assumed_taker_fee_pct=0.01,   # Taker fee varsayımı (%1)
    timing_5m_min_sec=60,
    timing_5m_max_sec=600,
    timing_15m_min_sec=120,
    timing_15m_max_sec=1_800,
)
```

Test ve paper-trading için farklı config instance'ı oluştur.

## Hata Yönetimi

Bridge katmanı exception fırlatmaz; her hata `TradeIntent(mapped_side=REJECT)` ile döner.

Olası `RejectionReason` değerleri:

| Neden | Katman | Açıklama |
|-------|--------|----------|
| NO_TRADE_SIGNAL | router | signal.direction == NO_TRADE |
| ASSET_MISMATCH | matcher | Market asset != sinyal asset |
| AMBIGUOUS_WORDING | matcher/router | Polarity tespit edilemedi |
| TIMING_TOO_CLOSE | matcher | Resolution çok yakın |
| TIMING_TOO_FAR | matcher | Resolution çok uzak |
| MARKET_INACTIVE | matcher | Market aktif değil |
| LOW_CONFIDENCE | filter | Model güveni yetersiz |
| LOW_LIQUIDITY | filter | Likidite yetersiz |
| HIGH_SPREAD | filter | Spread çok geniş |
| NEGATIVE_EDGE | filter | Fee sonrası edge negatif |
| NO_CANDIDATE_MARKETS | — | Uygun market bulunamadı |

## Test Çalıştırma

```bash
pytest signal_bridge/tests/ -v
```
