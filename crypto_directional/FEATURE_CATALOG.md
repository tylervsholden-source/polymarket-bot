# FEATURE_CATALOG.md — Feature Kataloğu

**Versiyon:** 0.1.0
**Tarih:** 2026-03-15
**Modül:** `crypto_directional/`

---

## Genel Kurallar

1. Tüm feature'lar `t` anında veya öncesinde hesaplanır. `t+1` veya sonrası bilgi kullanılmaz.
2. Rolling window'lar `[t-N, t]` aralığına bakar. `t+1` kapanışı hiçbir zaman dahil edilmez.
3. Feature isimleri `feat_` prefix'i taşır (feature vs label ayrımı için).
4. Her feature için leakage riski açıkça belirtilir.
5. Feature aktifliği `config/defaults.yaml`'dan kontrol edilir.

### Leakage Risk Seviyeleri

| Seviye | Anlam |
|--------|-------|
| `NONE` | Sadece `[t-N, t]` — güvenli |
| `LOW` | Dikkat gerekli; doğru implementasyonda güvenli |
| `MEDIUM` | Spesifik kontrol gerekiyor |
| `HIGH` | Kullanılmaz — devre dışı |

### Öncelik Seviyeleri

| Öncelik | Anlam |
|---------|-------|
| P1 | İlk modele dahil — zorunlu |
| P2 | Ablation testine tabi — ilk modelde var |
| P3 | İkinci aşama — ablation sonrası karar |

---

## Kategori 1 — Momentum

### feat_return_1b

| Alan | Değer |
|------|-------|
| **ID** | M001 |
| **Formül** | `(close[t] - open[t]) / open[t]` |
| **Lookback** | 1 bar |
| **Kaynak** | OHLCV |
| **Leakage** | NONE |
| **Öncelik** | P1 |
| **Neden Faydalı** | Mevcut bar momentumu; trend devam eğilimi |

---

### feat_return_Nb (N = 3, 5, 10, 20)

| Alan | Değer |
|------|-------|
| **ID** | M002 |
| **Formül** | `(close[t] - close[t-N]) / close[t-N]` |
| **Lookback** | N bar |
| **Kaynak** | OHLCV |
| **Leakage** | NONE |
| **Öncelik** | P1 |
| **Türev Feature'lar** | `feat_return_3b`, `feat_return_5b`, `feat_return_10b`, `feat_return_20b` |
| **Neden Faydalı** | Farklı uzunluklarda momentum ölçümü |

---

### feat_ema_cross_fast_slow

| Alan | Değer |
|------|-------|
| **ID** | M003 |
| **Formül** | `(EMA(close, fast)[t] - EMA(close, slow)[t]) / close[t]` |
| **Lookback** | max(fast, slow) bar |
| **Kaynak** | OHLCV |
| **Leakage** | NONE |
| **Öncelik** | P1 |
| **Türev Feature'lar** | `feat_ema_cross_5_20`, `feat_ema_cross_9_21` |
| **Dikkat** | EMA hesabı `t` kapanışını içerir; `t+1` dahil değil |

---

### feat_rsi_14

| Alan | Değer |
|------|-------|
| **ID** | M004 |
| **Formül** | Wilder RSI, period=14 |
| **Lookback** | 15 bar |
| **Kaynak** | OHLCV |
| **Leakage** | NONE |
| **Öncelik** | P1 |
| **Neden Faydalı** | Aşırı alım (>70) / aşırı satım (<30) sinyali |

---

### feat_macd_hist_norm

| Alan | Değer |
|------|-------|
| **ID** | M005 |
| **Formül** | `MACD_histogram(12,26,9)[t] / close[t]` |
| **Lookback** | 34 bar |
| **Kaynak** | OHLCV |
| **Leakage** | NONE |
| **Öncelik** | P2 |
| **Türev Feature'lar** | `feat_macd_line_norm`, `feat_macd_signal_norm`, `feat_macd_hist_norm` |

---

### feat_ema_slope_9

| Alan | Değer |
|------|-------|
| **ID** | M006 |
| **Formül** | `(EMA9[t] - EMA9[t-3]) / (EMA9[t-3] × 3)` |
| **Lookback** | 12 bar |
| **Kaynak** | OHLCV |
| **Leakage** | NONE |
| **Öncelik** | P2 |
| **Neden Faydalı** | Trend ivmesi — hızlanan/yavaşlayan trend ayrımı |

---

## Kategori 2 — Mean Reversion

### feat_zscore_close_20

| Alan | Değer |
|------|-------|
| **ID** | MR001 |
| **Formül** | `(close[t] - mean(close, 20)) / std(close, 20)` |
| **Lookback** | 20 bar |
| **Kaynak** | OHLCV |
| **Leakage** | NONE — window `[t-19, t]` |
| **Öncelik** | P1 |
| **Neden Faydalı** | Ortalamadan sapma; geri dönüş beklentisi |

---

### feat_bb_position_20

| Alan | Değer |
|------|-------|
| **ID** | MR002 |
| **Formül** | `(close[t] - BB_lower) / (BB_upper - BB_lower)` |
| **Lookback** | 20 bar |
| **Kaynak** | OHLCV |
| **Leakage** | NONE |
| **Öncelik** | P1 |
| **Neden Faydalı** | 0 = alt band, 1 = üst band; uçlarda ortalamaya dönüş |

---

### feat_price_dev_ema_21

| Alan | Değer |
|------|-------|
| **ID** | MR003 |
| **Formül** | `(close[t] - EMA21[t]) / EMA21[t]` |
| **Lookback** | 21 bar |
| **Kaynak** | OHLCV |
| **Leakage** | NONE |
| **Öncelik** | P2 |

---

### feat_price_vs_vwap

| Alan | Değer |
|------|-------|
| **ID** | MR004 |
| **Formül** | `(close[t] - rolling_VWAP(20)[t]) / rolling_VWAP(20)[t]` |
| **Lookback** | 20 bar |
| **Kaynak** | OHLCV (volume × close) |
| **Leakage** | LOW — rolling VWAP `[t-19, t]` kullanılır |
| **Öncelik** | P2 |
| **Dikkat** | Oturum başlangıcı kullanılmaz; rolling window kullanılır |

---

## Kategori 3 — Volatility

### feat_realized_vol_20

| Alan | Değer |
|------|-------|
| **ID** | V001 |
| **Formül** | `std(log(close[t]/close[t-1]), 20)` |
| **Lookback** | 21 bar |
| **Kaynak** | OHLCV |
| **Leakage** | NONE |
| **Öncelik** | P1 |
| **Neden Faydalı** | Mevcut piyasa riski; kill switch için de kullanılır |
| **Türev Feature'lar** | `feat_realized_vol_10`, `feat_realized_vol_20`, `feat_realized_vol_50` |

---

### feat_vol_ratio_5_20

| Alan | Değer |
|------|-------|
| **ID** | V002 |
| **Formül** | `realized_vol(5)[t] / realized_vol(20)[t]` |
| **Lookback** | 21 bar |
| **Kaynak** | OHLCV |
| **Leakage** | NONE |
| **Öncelik** | P1 |
| **Neden Faydalı** | Volatilite rejim değişimi; >1 = artan vol |

---

### feat_atr_14_norm

| Alan | Değer |
|------|-------|
| **ID** | V003 |
| **Formül** | `ATR(14)[t] / close[t]` |
| **Lookback** | 15 bar |
| **Kaynak** | OHLCV |
| **Leakage** | NONE |
| **Öncelik** | P2 |

---

### feat_intrabar_range

| Alan | Değer |
|------|-------|
| **ID** | V004 |
| **Formül** | `(high[t] - low[t]) / close[t]` |
| **Lookback** | 1 bar |
| **Kaynak** | OHLCV |
| **Leakage** | LOW — kapanmış bar; bar kapanışında hesaplanır |
| **Öncelik** | P2 |

---

## Kategori 4 — Microstructure

### feat_ob_imbalance_5

| Alan | Değer |
|------|-------|
| **ID** | MS001 |
| **Formül** | `(bid_depth_5 - ask_depth_5) / (bid_depth_5 + ask_depth_5)` |
| **Lookback** | Anlık (bar kapanış snapshot) |
| **Kaynak** | Order Book Snapshot |
| **Leakage** | NONE |
| **Öncelik** | P1 |
| **Neden Faydalı** | Kısa vadeli baskı yönü; pozitif = alım baskısı |
| **Türev Feature'lar** | `feat_ob_imbalance_5`, `feat_ob_imbalance_10` |

---

### feat_spread_pct

| Alan | Değer |
|------|-------|
| **ID** | MS002 |
| **Formül** | `(best_ask - best_bid) / mid_price` |
| **Lookback** | Anlık |
| **Kaynak** | Order Book Snapshot |
| **Leakage** | NONE |
| **Öncelik** | P1 |
| **Neden Faydalı** | Likidite kalitesi; trade filter için de kullanılır |

---

### feat_spread_zscore_20

| Alan | Değer |
|------|-------|
| **ID** | MS003 |
| **Formül** | `(spread_pct[t] - mean(spread_pct, 20)) / std(spread_pct, 20)` |
| **Lookback** | 20 bar |
| **Kaynak** | Order Book Snapshot |
| **Leakage** | NONE |
| **Öncelik** | P2 |

---

### feat_taker_buy_ratio

| Alan | Değer |
|------|-------|
| **ID** | MS004 |
| **Formül** | `taker_buy_volume[t] / total_volume[t]` |
| **Lookback** | 1 bar |
| **Kaynak** | Taker Flow Table |
| **Leakage** | NONE — bar kapandıktan sonra hesaplanır |
| **Öncelik** | P1 |
| **Neden Faydalı** | Agresif alıcı/satıcı baskısı |

---

### feat_volume_ratio_20

| Alan | Değer |
|------|-------|
| **ID** | MS005 |
| **Formül** | `volume[t] / mean(volume[t-20 : t-1])` |
| **Lookback** | 20 bar |
| **Kaynak** | OHLCV |
| **Leakage** | NONE — ortalama `t-1`'e kadar alınır, `t` dahil değil |
| **Öncelik** | P1 |
| **Dikkat** | `mean(volume[t-N : t-1])` — son nokta `t-1`, `t` değil |

---

## Kategori 5 — Derivatives

### feat_funding_rate

| Alan | Değer |
|------|-------|
| **ID** | D001 |
| **Formül** | Forward-fill ile her bara yayılmış güncel funding oranı |
| **Lookback** | Son uygulama zamanı |
| **Kaynak** | Funding Rate Table |
| **Leakage** | NONE |
| **Öncelik** | P1 |
| **Neden Faydalı** | Pozitif = long taraf ödüyor; aşırı yüksek = crowded trade |

---

### feat_funding_8h_sum

| Alan | Değer |
|------|-------|
| **ID** | D002 |
| **Formül** | Son 24h içindeki funding ödemelerinin toplamı |
| **Lookback** | 3 funding period (24h) |
| **Kaynak** | Funding Rate Table |
| **Leakage** | NONE |
| **Öncelik** | P2 |

---

### feat_oi_change_3b

| Alan | Değer |
|------|-------|
| **ID** | D003 |
| **Formül** | `(OI[t] - OI[t-3]) / OI[t-3]` |
| **Lookback** | 3 bar |
| **Kaynak** | Open Interest Table |
| **Leakage** | NONE |
| **Öncelik** | P1 |
| **Neden Faydalı** | OI artışı + fiyat artışı = güçlü trend; OI artışı + düşüş = kısa baskı |

---

### feat_oi_zscore_20

| Alan | Değer |
|------|-------|
| **ID** | D004 |
| **Formül** | `(OI[t] - mean(OI, 20)) / std(OI, 20)` |
| **Lookback** | 20 bar |
| **Kaynak** | Open Interest Table |
| **Leakage** | NONE |
| **Öncelik** | P2 |

---

### feat_liq_imbalance

| Alan | Değer |
|------|-------|
| **ID** | D005 |
| **Formül** | `(long_liq - short_liq) / (long_liq + short_liq + ε)` |
| **Lookback** | 1 bar |
| **Kaynak** | Liquidations Table |
| **Leakage** | NONE — bar kapandıktan sonra hesaplanır |
| **Öncelik** | P2 |
| **Neden Faydalı** | Yüksek long tasfiye = panik satış; fiyatı aşağı iter |

---

### feat_perp_premium

| Alan | Değer |
|------|-------|
| **ID** | D006 |
| **Formül** | `(futures_close[t] - spot_close[t]) / spot_close[t]` |
| **Lookback** | 1 bar |
| **Kaynak** | Binance Futures + Spot OHLCV |
| **Leakage** | NONE |
| **Öncelik** | P2 |
| **Neden Faydalı** | Pozitif premium → long talep fazlası |

---

## Kategori 6 — Regime

### feat_vol_regime

| Alan | Değer |
|------|-------|
| **ID** | R001 |
| **Formül** | `percentile_rank(realized_vol_20[t], window=100)` |
| **Lookback** | 100 bar |
| **Kaynak** | V001'den türetilir |
| **Leakage** | NONE |
| **Öncelik** | P1 |
| **Değerler** | `0` (low <33p), `1` (mid 33-66p), `2` (high >66p) |
| **Neden Faydalı** | Kill switch için; momentum low/mid'de daha iyi çalışır |

---

### feat_adx_14

| Alan | Değer |
|------|-------|
| **ID** | R002 |
| **Formül** | Standard ADX(14) |
| **Lookback** | 28 bar |
| **Kaynak** | OHLCV |
| **Leakage** | NONE |
| **Öncelik** | P2 |
| **Neden Faydalı** | ADX>25 = trend, ADX<20 = chop; momentum stratejisi trend'de daha iyi |
| **Türev Feature'lar** | `feat_adx_14`, `feat_di_plus_14`, `feat_di_minus_14` |

---

### feat_autocorr_1_20

| Alan | Değer |
|------|-------|
| **ID** | R003 |
| **Formül** | `autocorr(return_1b, lag=1, window=20)[t]` |
| **Lookback** | 21 bar |
| **Kaynak** | OHLCV |
| **Leakage** | NONE |
| **Öncelik** | P3 |
| **Neden Faydalı** | Pozitif autocorr = trend; negatif = mean-reversion rejim |

---

### feat_btc_return_1b (sadece ETH modeli için)

| Alan | Değer |
|------|-------|
| **ID** | R004 |
| **Formül** | `(btc_close[t] - btc_close[t-1]) / btc_close[t-1]` |
| **Lookback** | 1 bar |
| **Kaynak** | BTCUSDT OHLCV |
| **Leakage** | LOW — aynı bar kapanışındaki BTC fiyatı; dikkat |
| **Öncelik** | P2 |
| **Neden Faydalı** | BTC hareketi ETH yönünü etkiler |
| **Dikkat** | Yalnızca kapanmış barın BTC fiyatı kullanılır; sonraki bar dahil değil |

---

## Özet Tablosu

| ID | Feature | Kategori | Leakage | Öncelik |
|----|---------|----------|---------|---------|
| M001 | `feat_return_1b` | Momentum | NONE | P1 |
| M002 | `feat_return_Nb` | Momentum | NONE | P1 |
| M003 | `feat_ema_cross` | Momentum | NONE | P1 |
| M004 | `feat_rsi_14` | Momentum | NONE | P1 |
| M005 | `feat_macd_hist_norm` | Momentum | NONE | P2 |
| M006 | `feat_ema_slope_9` | Momentum | NONE | P2 |
| MR001 | `feat_zscore_close_20` | Mean Rev | NONE | P1 |
| MR002 | `feat_bb_position_20` | Mean Rev | NONE | P1 |
| MR003 | `feat_price_dev_ema_21` | Mean Rev | NONE | P2 |
| MR004 | `feat_price_vs_vwap` | Mean Rev | LOW | P2 |
| V001 | `feat_realized_vol_20` | Volatility | NONE | P1 |
| V002 | `feat_vol_ratio_5_20` | Volatility | NONE | P1 |
| V003 | `feat_atr_14_norm` | Volatility | NONE | P2 |
| V004 | `feat_intrabar_range` | Volatility | LOW | P2 |
| MS001 | `feat_ob_imbalance_5` | Micro | NONE | P1 |
| MS002 | `feat_spread_pct` | Micro | NONE | P1 |
| MS003 | `feat_spread_zscore_20` | Micro | NONE | P2 |
| MS004 | `feat_taker_buy_ratio` | Micro | NONE | P1 |
| MS005 | `feat_volume_ratio_20` | Micro | NONE | P1 |
| D001 | `feat_funding_rate` | Derivatives | NONE | P1 |
| D002 | `feat_funding_8h_sum` | Derivatives | NONE | P2 |
| D003 | `feat_oi_change_3b` | Derivatives | NONE | P1 |
| D004 | `feat_oi_zscore_20` | Derivatives | NONE | P2 |
| D005 | `feat_liq_imbalance` | Derivatives | NONE | P2 |
| D006 | `feat_perp_premium` | Derivatives | NONE | P2 |
| R001 | `feat_vol_regime` | Regime | NONE | P1 |
| R002 | `feat_adx_14` | Regime | NONE | P2 |
| R003 | `feat_autocorr_1_20` | Regime | NONE | P3 |
| R004 | `feat_btc_return_1b` | Regime | LOW | P2 |

**P1 toplam:** 14 feature (ilk modele zorunlu dahil)
**P2 toplam:** 13 feature (ablation testine tabi)
**P3 toplam:** 2 feature (ikinci aşama)

---

## Leakage Test Şablonu

Her feature için zorunlu birim test:

```python
def test_no_leakage_feat_return_1b():
    """feat_return_1b sadece t ve öncesini kullanmalı."""
    df = build_features(ohlcv_df)
    value_at_t = df.iloc[100]["feat_return_1b"]

    # t+1 verisini değiştir
    df_mod = ohlcv_df.copy()
    df_mod.iloc[101, df_mod.columns.get_loc("close")] *= 1.10

    df_mod_feat = build_features(df_mod)
    assert df_mod_feat.iloc[100]["feat_return_1b"] == value_at_t, \
        "feat_return_1b leakage: t+1 değişimi t'yi etkilemeli değil"
```

Bu test şablonu `tests/test_features.py`'da her P1 feature için uygulanır.
