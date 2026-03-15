# DATA_SCHEMA.md — Veri Şeması

**Versiyon:** 0.1.0
**Tarih:** 2026-03-15
**Modül:** `crypto_directional/`

---

## 1. Veri Kaynakları

| Kaynak | Veriler | Endpoint |
|--------|---------|----------|
| Binance Futures REST | OHLCV (klines) | `GET /fapi/v1/klines` |
| Binance Futures REST | Mark price / OB | `GET /fapi/v1/depth` |
| Binance Futures REST | Funding rate | `GET /fapi/v1/fundingRate` |
| Binance Futures REST | Open interest | `GET /futures/data/openInterestHist` |
| Binance Futures REST | Liquidations | `GET /futures/data/forceOrders` |
| Binance Futures WebSocket | Real-time ticker | `bookTicker` stream |

**Not:** Tüm zaman damgaları **UTC, Unix millisecond** formatında saklanır.
**Not:** Mevcut `BinanceFeed` Bitstamp kullanır; bu modül doğrudan Binance Futures REST kullanır.

### Veri Kaynağı ve Hizalama Kuralı

**Tek kaynak ilkesi (single source of truth):**

Ham veri en küçük granülaritede (`1m`) toplanır.
5m ve 15m barlar bu 1m veriden deterministik olarak resample edilir.
Native 5m/15m endpoint'leri de çekilir ama **karşılaştırma ve doğrulama** amacıyla kullanılır — birincil kaynak 1m resampling'dir.

```
1m OHLCV (native Binance)
    ├── resample → 5m OHLCV  (deterministik)
    └── resample → 15m OHLCV (deterministik)
```

**Türev veri hizalama kuralları:**

| Veri | Hizalama Yöntemi |
|------|-----------------|
| Funding Rate (8h) | `merge_asof` (backward) → bar zaman damgasına en yakın önceki kayıt |
| Open Interest | Native 5m/15m endpoint; bar `open_time` ile exact join |
| Liquidations | Bar aralığına aggregate: `[bar_open_time, bar_open_time + interval)` |
| OB Snapshot | Bar `close_time`'ında alınır; 2×interval yaşından eski snapshot düşürülür |

**Timestamp alignment pseudocode:**

```python
# Tüm veri bar open_time üzerinden hizalanır
aligned = (
    ohlcv_5m                                        # temel index
    .merge(ob_snapshots, on="open_time", how="left")
    .merge(taker_flow,   on="open_time", how="left")
    .merge_asof(funding, left_on="open_time",        # backward fill
                right_on="funding_time", direction="backward")
    .merge(oi_5m, on="open_time", how="left")
    .merge(liquidations_5m, on="open_time", how="left")
)
# Eksik değer strateji: DATA_SCHEMA.md § 7.1
```

---

## 2. Ham Veri Şemaları

### 2.1 OHLCV (Kline)

**Dosya:** `crypto_directional/data/raw/ohlcv_{symbol}_{interval}.parquet`

| Kolon | Tip | Açıklama |
|-------|-----|----------|
| `open_time` | int64 (ms UTC) | Bar açılış zamanı — PRIMARY KEY |
| `open` | float64 | Açılış fiyatı |
| `high` | float64 | En yüksek |
| `low` | float64 | En düşük |
| `close` | float64 | Kapanış |
| `volume` | float64 | Hacim (base asset) |
| `close_time` | int64 (ms UTC) | Bar kapanış zamanı |
| `quote_volume` | float64 | Hacim (USDT) |
| `trade_count` | int64 | İşlem sayısı |
| `taker_buy_volume` | float64 | Taker alım hacmi |
| `taker_buy_quote_volume` | float64 | Taker alım (USDT) |

**Zorunluluk:** `open_time` benzersiz ve artan sırada.
**Interval değerleri:** `1m`, `5m`, `15m`, `1h`, `4h`

---

### 2.2 Order Book Snapshot

**Dosya:** `crypto_directional/data/raw/ob_{symbol}_{interval}.parquet`

Her bar kapanışında alınan anlık görüntü (top 20 level).

| Kolon | Tip | Açıklama |
|-------|-----|----------|
| `timestamp` | int64 (ms UTC) | Snapshot zamanı — PRIMARY KEY |
| `best_bid` | float64 | En iyi alış |
| `best_ask` | float64 | En iyi satış |
| `mid_price` | float64 | `(bid + ask) / 2` |
| `spread_abs` | float64 | `ask - bid` |
| `spread_pct` | float64 | `spread_abs / mid_price` |
| `bid_depth_5` | float64 | Top 5 bid toplam hacim (USDT) |
| `ask_depth_5` | float64 | Top 5 ask toplam hacim (USDT) |
| `bid_depth_10` | float64 | Top 10 bid toplam hacim (USDT) |
| `ask_depth_10` | float64 | Top 10 ask toplam hacim (USDT) |
| `ob_imbalance_5` | float64 | `(bid5 - ask5) / (bid5 + ask5)` |
| `ob_imbalance_10` | float64 | Top 10 imbalance |

---

### 2.3 Taker Flow (Bar Aggregated)

**Dosya:** `crypto_directional/data/raw/taker_{symbol}_{interval}.parquet`

Her bar içindeki taker alım/satım dengesi (OHLCV'den türetilir).

| Kolon | Tip | Açıklama |
|-------|-----|----------|
| `bar_open_time` | int64 (ms UTC) | Ait olduğu bar — FOREIGN KEY |
| `total_volume` | float64 | Toplam hacim |
| `taker_buy_volume` | float64 | Taker alım |
| `taker_sell_volume` | float64 | Taker satım (`total - buy`) |
| `taker_buy_ratio` | float64 | `taker_buy / total` |
| `trade_count` | int64 | İşlem sayısı |
| `avg_trade_size` | float64 | Ortalama işlem büyüklüğü |

---

### 2.4 Funding Rate

**Dosya:** `crypto_directional/data/raw/funding_{symbol}.parquet`

Binance Futures: 8 saatte bir güncellenir (00:00, 08:00, 16:00 UTC).

| Kolon | Tip | Açıklama |
|-------|-----|----------|
| `funding_time` | int64 (ms UTC) | Uygulama zamanı — PRIMARY KEY |
| `funding_rate` | float64 | Oran (örn: 0.0001 = %0.01) |
| `mark_price` | float64 | Mark fiyat |

**Feature'a dönüşüm:** Bar bazına forward-fill ile yayılır.
Son uygulanan funding, bir sonraki uygulamaya kadar geçerlidir.

---

### 2.5 Open Interest

**Dosya:** `crypto_directional/data/raw/oi_{symbol}_{interval}.parquet`

| Kolon | Tip | Açıklama |
|-------|-----|----------|
| `timestamp` | int64 (ms UTC) | PRIMARY KEY |
| `open_interest_usdt` | float64 | Toplam açık pozisyon (USDT notional) |
| `open_interest_contracts` | float64 | Sözleşme sayısı |

**Interval:** `5m`, `15m`

---

### 2.6 Liquidations (Bar Aggregated)

**Dosya:** `crypto_directional/data/raw/liquidations_{symbol}_{interval}.parquet`

| Kolon | Tip | Açıklama |
|-------|-----|----------|
| `bar_open_time` | int64 (ms UTC) | Ait olduğu bar |
| `long_liq_volume` | float64 | Long tasfiye hacmi (USDT) |
| `short_liq_volume` | float64 | Short tasfiye hacmi (USDT) |
| `long_liq_count` | int64 | Long tasfiye sayısı |
| `short_liq_count` | int64 | Short tasfiye sayısı |
| `liq_imbalance` | float64 | `(long - short) / (long + short + ε)` |

---

## 3. İşlenmiş Veri Şemaları

### 3.1 Feature Table

**Dosya:** `crypto_directional/data/processed/features_{symbol}_{horizon}.parquet`

Her satır bir bar kapanışı.
**Tüm feature'lar `t` zamanında veya öncesinde hesaplanmış.**
`future_return` ve `label` bu tabloda **YOK** — ayrı label tablosunda.

| Kolon | Kaynak |
|-------|--------|
| `open_time` | OHLCV — index |
| `mid_price` | OB snapshot |
| `[feature_*]` | FEATURE_CATALOG.md'e göre |
| `data_quality_flag` | Kalite kontrolü |

---

### 3.2 Label Table

**Dosya:** `crypto_directional/data/processed/labels_{symbol}.parquet`

| Kolon | Tip | Açıklama |
|-------|-----|----------|
| `open_time` | int64 | PRIMARY KEY |
| `mid_price_t` | float64 | `t` anındaki mid_price |
| `mid_price_t5m` | float64 | `t+5m` anındaki mid_price |
| `mid_price_t15m` | float64 | `t+15m` anındaki mid_price |
| `future_return_5m` | float64 | `(mid_t5m - mid_t) / mid_t` |
| `future_return_15m` | float64 | `(mid_t15m - mid_t) / mid_t` |
| `label_5m` | str | UP / DOWN / NO_TRADE |
| `label_15m` | str | UP / DOWN / NO_TRADE |
| `threshold_5m` | float64 | Kullanılan eşik (config'den gelir) |
| `threshold_15m` | float64 | Kullanılan eşik |

**KURAL:** `future_return_*` ve `mid_price_t*m` kolonları hiçbir zaman
feature vektörüne dahil edilmez. Sadece analiz ve label üretimi içindir.

---

### 3.3 Model Input Contract

```python
# X: sadece feature kolonları (label ve future_return YOK)
feature_cols = [c for c in df.columns
                if c.startswith("feat_") or c in FEATURE_CATALOG_NAMES]
X = df[feature_cols]

# y: hedef label
y = labels_df.loc[df.index, "label_5m"]   # veya label_15m
```

Feature isimlerinin tam listesi `FEATURE_CATALOG.md`'de tanımlıdır.

---

## 4. Paper Trading Log Şeması

**Dosya:** `crypto_directional/data/logs/paper_{date}.jsonl`

```json
{
  "timestamp_utc": "2026-03-15T10:30:00.000Z",
  "symbol": "BTCUSDT",
  "horizon": "5m",
  "bar_open_time": 1742039400000,
  "probabilities": {"UP": 0.61, "DOWN": 0.22, "NO_TRADE": 0.17},
  "predicted_class": "UP",
  "confidence": 0.61,
  "spread_pct": 0.00012,
  "realized_vol_5m": 0.0018,
  "vol_regime": "low",
  "funding_rate": 0.00008,
  "decision": "OPEN_LONG",
  "size_usdt": 50.0,
  "entry_mid": 83420.5,
  "fee_pct_expected": 0.04,
  "slippage_pct_expected": 0.015,
  "exit_reason": "fixed_horizon",
  "exit_timestamp_utc": "2026-03-15T10:35:00.000Z",
  "exit_mid": 83560.2,
  "return_pct": 0.00167,
  "pnl_gross_usdt": 0.083,
  "pnl_net_usdt": 0.041,
  "cumulative_pnl_usdt": 1.247
}
```

---

## 5. Paper Trading Operasyonel Şemalar

Aşağıdaki şemalar paper trading aşamasında (Faz 4) üretilir.
Şema tanımları Faz 1'de yapılır — kod Faz 4'te yazılır.

### 5.1 Emir Logu

**Dosya:** `crypto_directional/data/logs/paper_orders_{date}.jsonl`

```json
{
  "order_id": "ord_BTCUSDT_5m_1742039400000",
  "timestamp_utc": "2026-03-15T10:30:00.050Z",
  "symbol": "BTCUSDT",
  "horizon": "5m",
  "bar_open_time": 1742039400000,
  "side": "BUY",
  "order_type": "TAKER",
  "requested_size_usdt": 50.0,
  "limit_price": null,
  "status": "FILLED",
  "fill_price": 83425.0,
  "fill_size_usdt": 50.0,
  "fee_usdt": 0.02,
  "slippage_pct": 0.000054,
  "latency_ms": 12
}
```

---

### 5.2 Fill Logu

**Dosya:** `crypto_directional/data/logs/paper_fills_{date}.jsonl`

Her emir kaydından türetilir; reconciliation için ayrı tutulur.

```json
{
  "fill_id": "fill_ord_BTCUSDT_5m_1742039400000_0",
  "order_id": "ord_BTCUSDT_5m_1742039400000",
  "fill_time_utc": "2026-03-15T10:30:00.060Z",
  "symbol": "BTCUSDT",
  "side": "BUY",
  "fill_price": 83425.0,
  "fill_qty_base": 0.000599,
  "fill_qty_usdt": 50.0,
  "fee_usdt": 0.02,
  "cumulative_fill_usdt": 50.0
}
```

---

### 5.3 Pozisyon Logu

**Dosya:** `crypto_directional/data/logs/paper_positions_{date}.jsonl`

```json
{
  "position_id": "pos_BTCUSDT_5m_1742039400000",
  "symbol": "BTCUSDT",
  "horizon": "5m",
  "side": "LONG",
  "open_time_utc": "2026-03-15T10:30:00.060Z",
  "close_time_utc": "2026-03-15T10:35:00.020Z",
  "entry_price": 83425.0,
  "exit_price": 83560.2,
  "size_usdt": 50.0,
  "entry_fee_usdt": 0.02,
  "exit_fee_usdt": 0.02,
  "gross_pnl_usdt": 0.081,
  "net_pnl_usdt": 0.041,
  "return_pct": 0.00162,
  "exit_reason": "fixed_horizon",
  "bar_label": "UP",
  "model_confidence": 0.61
}
```

---

### 5.4 Equity Curve

**Dosya:** `crypto_directional/data/processed/equity_curve.parquet`

Her bar kapanışında güncellenir; hem açık hem kapalı pozisyon etkisi yansıtılır.

| Kolon | Tip | Açıklama |
|-------|-----|----------|
| `timestamp_utc` | datetime64 | Bar kapanış zamanı |
| `open_time` | int64 | Bar open_time (foreign key) |
| `capital_total` | float64 | Toplam portföy değeri (realized + unrealized) |
| `capital_available` | float64 | Serbest sermaye |
| `capital_in_positions` | float64 | Açık pozisyonlarda kilitli |
| `realized_pnl_cumulative` | float64 | Kümülatif realize pnl |
| `unrealized_pnl` | float64 | Anlık açık pozisyon pnl |
| `daily_pnl` | float64 | Günlük gerçekleşen pnl |
| `open_position_count` | int16 | Açık pozisyon sayısı |
| `drawdown_pct` | float64 | Zirve'den güncel çekilme |

---

### 5.5 Reconciliation Logu

**Dosya:** `crypto_directional/data/logs/paper_reconcile_{date}.jsonl`

Her karar döngüsünde: beklenen durum ile kayıt edilen durumun karşılaştırması.

```json
{
  "reconcile_time_utc": "2026-03-15T10:35:01.000Z",
  "expected_capital": 1000.041,
  "recorded_capital": 1000.041,
  "diff_usdt": 0.000,
  "open_positions_expected": 0,
  "open_positions_recorded": 0,
  "status": "OK",
  "warnings": []
}
```

---

## 6. Paper Trading Runtime State

**Dosya:** `crypto_directional/data/state/paper_state.json`

```json
{
  "capital": 1000.0,
  "available_capital": 950.0,
  "open_positions": {
    "BTCUSDT_5m_1742039400000": {
      "symbol": "BTCUSDT",
      "horizon": "5m",
      "side": "LONG",
      "size_usdt": 50.0,
      "entry_mid": 83420.5,
      "entry_utc": "2026-03-15T10:30:00Z",
      "stop_loss": 83003.0,
      "take_profit": 84255.0,
      "deadline_utc": "2026-03-15T10:35:00Z"
    }
  },
  "closed_count": 47,
  "daily_pnl_usdt": -2.15,
  "daily_loss_limit_usdt": -20.0,
  "consecutive_losses": 1,
  "active_kill_switches": [],
  "last_updated_utc": "2026-03-15T10:30:01Z"
}
```

---

## 7. Dizin Yapısı

```
crypto_directional/
└── data/
    ├── raw/
    │   ├── ohlcv_BTCUSDT_1m.parquet      ← kaynak; 5m/15m buradan türetilir
    │   ├── ohlcv_BTCUSDT_5m.parquet
    │   ├── ohlcv_BTCUSDT_15m.parquet
    │   ├── ohlcv_ETHUSDT_1m.parquet
    │   ├── ob_BTCUSDT_5m.parquet
    │   ├── funding_BTCUSDT.parquet
    │   ├── oi_BTCUSDT_5m.parquet
    │   └── liquidations_BTCUSDT_5m.parquet
    ├── processed/
    │   ├── features_BTCUSDT_5m.parquet
    │   ├── features_BTCUSDT_15m.parquet
    │   ├── labels_BTCUSDT.parquet
    │   └── equity_curve.parquet
    ├── state/
    │   └── paper_state.json
    └── logs/
        ├── paper_orders_20260315.jsonl
        ├── paper_fills_20260315.jsonl
        ├── paper_positions_20260315.jsonl
        ├── paper_reconcile_20260315.jsonl
        └── paper_20260315.jsonl          ← trade kararı logu (eski format, compat)
```

---

## 8. Veri Kalite Kuralları

### 8.1 Eksik Veri Stratejisi

| Kolon tipi | Strateji |
|------------|---------|
| OHLCV gap (< 3 bar) | Forward fill, `data_quality_flag = "ffill"` |
| OHLCV gap (>= 3 bar) | Bar'ı düşür — leakage riski |
| Funding (8s cycle) | Forward fill bir sonraki uygulamaya kadar |
| OB snapshot eksik | Rolling ortalama, `data_quality_flag = "ob_missing"` |
| OI eksik | Forward fill |

### 8.2 Anomali Flagleri

| Flag | Koşul |
|------|-------|
| `high_spread` | `spread_pct > 5 × rolling_mean_spread_20` |
| `zero_volume` | `volume == 0` |
| `price_spike` | `\|return_1b\| > 5 × realized_vol_20` |
| `stale_ob` | OB snapshot yaşı > 2 × interval |
| `extreme_funding` | `\|funding_rate\| > 0.005` |

### 8.3 Veri Metadata Şeması

Her raw dosyanın yanında `{filename}_meta.json` bulunur:

```json
{
  "symbol": "BTCUSDT",
  "interval": "5m",
  "source": "binance_futures_rest",
  "fetched_utc": "2026-03-15T00:00:00Z",
  "start_utc": "2025-09-15T00:00:00Z",
  "end_utc": "2026-03-15T00:00:00Z",
  "total_rows": 52560,
  "missing_bars": 3,
  "schema_version": "0.1.0"
}
```
