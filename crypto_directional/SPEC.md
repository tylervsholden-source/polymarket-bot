# SPEC.md — Kripto 5m/15m Yön Tahmin Sistemi

**Versiyon:** 0.1.1
**Tarih:** 2026-03-15
**Faz:** 1 — Tasarım
**Modül:** `crypto_directional/`

> **Mevcut polymarket botunu koru; yeni hedefi eski strateji gövdesine zorla yamama.**

---

## 1. Proje Amacı

Binance Futures Perpetual piyasasında **BTCUSDT** ve **ETHUSDT** için kısa vadeli fiyat yönünü tahmin eden, leakage-free ve execution-aware bir araştırma/trading sistemi kurmak.

Sistem şu soruya cevap vermelidir:

> Önümüzdeki **5 dakika** / **15 dakika** içinde fiyat:
> (a) anlamlı biçimde yukarı mı gidecek → `UP`
> (b) anlamlı biçimde aşağı mı gidecek → `DOWN`
> (c) işlem maliyetini karşılayacak kadar güçlü bir yön yok mu → `NO_TRADE`

Sistemin amacı "AI yorum üretmek" değil; **istatistiksel olarak sınanabilir, leakage-siz, execution-aware bir trading research stack** oluşturmaktır.

---

## 2. Polymarket Botundan Ayrışma

Bu modül Polymarket botunun **hiçbir parçasını yeniden kullanmaz**:

| Polymarket Bileşeni | Bu Modüldeki Durum | Neden Ayrı |
|--------------------|--------------------|------------|
| `ArbitrageEngine` | Kullanılmıyor | Binary prediction market logic, Bayesian prior YES/NO fiyatına karşı — burada geçersiz |
| `BayesianEstimator` | Kullanılmıyor | Polymarket fiyatını prior olarak kullanan tasarım; futures spot fiyatı için uygulanamaz |
| `KellyCriterion` (mevcut) | Kullanılmıyor | Polymarket binary outcome math; futures P&L farklı hesaplanır |
| `PolymarketClient` / CLOB | Kullanılmıyor | YES/NO token, settlement, allowance — tamamen farklı domain |
| `BinanceFeed` (mevcut) | Kullanılmıyor | Bitstamp-backed, tek-timeframe, Polymarket sinyali için tasarlandı |
| `SmartTraderTracker` | Kullanılmıyor | Polymarket whale tracker |

Paylaşılan tek altyapı: `loguru` (log), PyYAML (config) — her ikisi de zaten bağımlılıkta.

---

## 3. Kapsam (İlk Sürüm)

| Parametre | Değer |
|-----------|-------|
| Borsa | Binance Futures (USDⓈ-M Perpetual) |
| Semboller | BTCUSDT, ETHUSDT |
| Hedef horizon | 5m, 15m |
| Sınıf sayısı | 3 — UP / DOWN / NO_TRADE |
| Veri | Min 6 ay OHLCV + türev veri |
| Execution | Faz 6'ya kadar yok |

**İlk sürümde kapsam dışı:**
- Diğer semboller (Top 50, altcoin)
- Haber / sosyal medya / sentiment
- LLM ana tahmin motoru
- Multi-agent sistem
- Dashboard / UI

---

## 4. Tahmin Görevi

### 4.1 Sınıf Tanımları

| Sınıf | Koşul |
|-------|-------|
| `UP` | `future_return > +threshold` |
| `DOWN` | `future_return < -threshold` |
| `NO_TRADE` | `\|future_return\| <= threshold` veya sinyal güvenilir değil |

Sistem her zaman işlem üretmek zorunda **değildir**. Zorla pozisyon açan sistem istenmemektedir.

### 4.2 Threshold Hesabı

```
threshold = taker_fee_pct + expected_slippage_pct + min_edge_pct
```

| Bileşen | Varsayılan | Config Anahtarı |
|---------|-----------|-----------------|
| taker_fee | 0.0400% | `thresholds.taker_fee_pct` |
| slippage | 0.0150% | `thresholds.slippage_pct` |
| min_edge | 0.0200% | `thresholds.min_edge_pct` |
| **Toplam (round-trip)** | **~0.075%** | — |

**Her horizon için ayrı threshold konfigürasyonu vardır:**

| Horizon | Config Anahtarı | Varsayılan |
|---------|----------------|-----------|
| 5m | `thresholds.label_threshold_5m` | base (taker+slip+edge) |
| 15m | `thresholds.label_threshold_15m` | ayrı değer — veriden kalibre edilir |

> **Neden ayrı?**
> 15m horizon daha büyük fiyat hareketi oluşturabilir, ancak "1.5× base" gibi keyfi bir çarpan kullanmak gizli curve-fitting'tir.
> Doğru yöntem: her horizon için geçmiş veriden maliyet sonrası pozitif expectancy veren minimum hareketi ölçmek ve eşiği o değere göre ayarlamak.
> Bu kalibrasyon **Faz 3 backtest aşamasında** yapılır; başlangıçta her iki horizon da aynı base threshold'u kullanır.

Tüm eşikler `config/defaults.yaml`'dan okunur. Hardcoded değer kabul edilmez.

---

## 5. Label Tanımı

### 5.1 Formül

```python
mid_price[t]    = (best_bid[t] + best_ask[t]) / 2
future_mid[t+H] = mid_price[t+H]          # H = 5m veya 15m

future_return[t, H] = (future_mid[t+H] - mid_price[t]) / mid_price[t]
```

### 5.2 Sınıflandırma Mantığı

```python
def label(future_return: float, threshold: float) -> str:
    if future_return > +threshold:
        return "UP"
    elif future_return < -threshold:
        return "DOWN"
    else:
        return "NO_TRADE"
```

### 5.3 Execution Timing — Kesin Tanım

Bu tanım backtest'in gerçekçi kalması için zorunludur. Buradaki her kelime kurallaştırılmıştır.

```
Sinyal zamanı:       bar_close_t  (t anındaki bar kapanışında)
Karar zamanı:        t_close      (bar kapandıktan hemen sonra)
Varsayımsal giriş:   t_close anındaki ilk geçerli quote — aşağıya bak
```

**Tek giriş fiyatı kuralı:**

> Giriş fill'i `t_close` anındaki anlık quote'tan yapılmış kabul edilir.
> "t+1 bar açılışına kadar bekle" varsayımı **yoktur** — order, bar kapanır kapanmaz verilmiş sayılır.
> Backtest, paper trading ve label hesabı bu tek kuralı paylaşır.

**Giriş fiyatı (simülasyon / paper trading):**

| Order tipi | Giriş fiyatı |
|------------|-------------|
| Taker (market order) | `best_ask[t_close]` (LONG) / `best_bid[t_close]` (SHORT) |
| Maker (limit order) | Fill modeli ayrıca tanımlanır (Faz 6) — şimdilik taker varsayılır |

**Çıkış fiyatı (horizon sonunda):**

| Çıkış tipi | Fiyat |
|------------|-------|
| Fixed horizon | `mid_price[t+H]` (t+H barının kapanışındaki mid price) |
| Stop loss | `best_bid[stop_trigger_time]` (LONG pozisyon, taker fill) |
| Take profit | `best_ask[tp_trigger_time]` (SHORT pozisyon, taker fill) |

**Kritik kural:**

> `t` barının close bilgisi en erken `t` kapandıktan **sonra** kullanılabilir.
> Bu nedenle `t` barı üzerindeki feature'larla yapılan ilk aksiyon, en erken `t_close` anında başlatılır.
> **Aynı bar içinde hem sinyal üretip hem fill varsaymak yasaktır** — aksi açıkça ve gerekçeli biçimde belirtilmedikçe.

**Label ile execution tutarlılığı:**

Label formülü `mid_price[t+H] - mid_price[t]` kullanır.
Execution, taker fee'yi ve bid-ask spread'i giriş/çıkış fiyatına yansıtır.
Bu nedenle **net PnL ≠ label olarak görülen fiyat farkı** — fee ve spread her zaman düşülür.

### 5.4 Kritik Leakage Kuralları

1. Label **yalnızca** `[t, t+H]` aralığıyla hesaplanır.
2. `future_return`, `future_mid` değerleri hiçbir feature'a **sızdırılmaz**.
3. Her horizon için **ayrı label** üretilir.
4. Label hesabında OHLCV kapanışı değil **mid_price** kullanılır.
5. Bar kapanmadan label hesaplanamaz — streaming'de dikkat.

### 5.5 Leakage Kontrol Listesi

- [ ] Feature index `t`, label index `t+H` — farklı satırlar
- [ ] Rolling window'lar `[t-N, t]` aralığına bakıyor, `t+1` dahil değil
- [ ] Normalization (scaling) sadece train seti istatistiklerine fit
- [ ] Train/val/test bölünmesi kronolojik sıraya göre
- [ ] `future_return` feature vektöründe yer almıyor
- [ ] Execution fiyatı `t_close` anındaki quote kullanıyor, önceki bar kapatma değil

---

## 6. Validation Metodolojisi

### 6.1 Temel Kural

```
random_state ile veri karıştırmak (shuffle) YASAKTIR.
Tüm bölünmeler kronolojik sıraya göre yapılır.
```

### 6.2 Walk-Forward Evaluation

```
Expanding window (tercih edilen):
|--- Train1 ---|-- Val1 --|-- Test1 --|
|------ Train2 ------|-- Val2 --|-- Test2 --|
|---------- Train3 ----------|-- Val3 --|-- Test3 --|

Min fold sayısı: 5
```

### 6.3 Overlapping Label Riski ve Embargo

**Problem:** Her bar için ayrı label üretildiğinde, ardışık barların label'ları aynı geleceği paylaşır.

```
Örnek — 15m horizon, 5m barlar:
  bar t:   future_return hesabı → t..t+3 barlarını kapsar
  bar t+1: future_return hesabı → t+1..t+4 barlarını kapsar
  bar t+2: future_return hesabı → t+2..t+5 barlarını kapsar
```

Bu overlap fold sınırlarına yanlış biçimde sızarsa validation sonuçları optimistik görünür.

**Çözüm — Purge + Embargo:**

```
fold_train_end = t_i
embargo_bars   = ceil(H / bar_interval) + 1  # 15m/5m = 3 + 1 = 4 bar
fold_val_start = t_i + embargo_bars
```

| Kural | Açıklama |
|-------|----------|
| **Purge** | Fold sınırındaki `embargo_bars` kadar bar train'den çıkarılır |
| **Embargo** | Aynı `embargo_bars` val/test başlangıcına eklenir — train sonu ile val başı arasında boşluk |
| **Non-overlapping eval** | Opsiyonel: label aralıkları birbiriyle çakışmayacak şekilde sadece H-aralıklı barları değerlendir |

Bu kurallar walk-forward backtest implementasyonunda (`backtests/walk_forward_backtest.py`) uygulanır.

### 6.4 Normalization Kuralı

```python
scaler.fit(X_train)                      # sadece train'e
X_val_scaled  = scaler.transform(X_val)
X_test_scaled = scaler.transform(X_test)
```

### 6.5 Zorunlu Metrikler

**Sınıflandırma:**

| Metrik | Not |
|--------|-----|
| Precision (sınıf bazlı) | UP/DOWN sinyalleri ne kadar güvenilir |
| Recall (sınıf bazlı) | Gerçek fırsatları yakalama oranı |
| F1 (sınıf bazlı) | Denge |
| MCC | Class imbalance'a dayanıklı |
| Balanced Accuracy | NO_TRADE baskınlığını dengeler |
| Confusion Matrix | Hata dağılımı |

**Trading:**

| Metrik | Not |
|--------|-----|
| Net PnL (fee sonrası) | Para kazanıyor mu |
| PnL per trade | Ortalama kalite |
| Expectancy | E[win×rate] - E[loss×rate] |
| Hit Rate (UP/DOWN ayrı) | Yön doğruluk oranı |
| Max Drawdown | Risk profili |
| Turnover | Maliyet baskısı |
| Trade Count | Örneklem yeterliliği |
| UP/DOWN/NO_TRADE dağılımı | Model sağlık kontrolü |

> Accuracy tek başına başarı metriği değildir.

---

## 7. Trade Karar Mimarisi

```
Raw Market Data (Binance Futures)
        ↓
Feature Engineering (leakage-free)
        ↓
Model Prediction → {class, probabilities}
        ↓
Trade Filter (confidence + spread + vol + data freshness)
        ↓
Risk Manager (sizing + limits + kill switches)
        ↓
Paper Trader ──(Faz 6)──→ Execution Adapter
```

### 7.1 Trade Filtresi Koşulları

| Koşul | Parametre |
|-------|-----------|
| Model confidence | `P(class) >= min_confidence` |
| Spread | `spread_pct <= max_spread_pct` |
| Volatility | `realized_vol < vol_kill_threshold` |
| Data freshness | `bar_age <= max_data_age_sec` |
| Funding | `\|funding_rate\| <= max_funding_pct` |
| Açık pozisyon | `positions < max_concurrent` |
| Günlük PnL | `daily_pnl > -daily_loss_limit` |

### 7.2 Çıkış Mantığı

Her işlem için baştan tanımlı kurallar:

1. **Fixed horizon:** 5m/15m sonunda otomatik kapat (primary)
2. **Stop loss:** `config.risk.stop_loss_pct`
3. **Take profit:** `config.risk.take_profit_pct`
4. **Time stop:** `max_hold_bars` geçmişse kapat
5. **Opposite signal:** Ters sinyal gelince kapat + aç
6. **Stale data:** Feed kesilmişse kapat

---

## 8. Risk Yönetimi

Risk yönetimi başından sistemin parçasıdır. Sonradan eklenmez.

### 8.1 Parametreler

| Parametre | Varsayılan | Config Anahtarı |
|-----------|-----------|-----------------|
| Max günlük zarar | -%2.0 | `risk.daily_loss_limit_pct` |
| Max ardışık zarar | 5 | `risk.max_consecutive_losses` |
| Max eş zamanlı pozisyon | 2 | `risk.max_concurrent_positions` |
| İşlem başına risk | %0.5 | `risk.max_risk_per_trade_pct` |
| Max notional | %5.0 | `risk.max_notional_pct` |

### 8.2 Kill Switch'ler

| Switch | Tetikleyici | Aksiyon |
|--------|------------|---------|
| `daily_loss` | PnL < -limit | Yeni işlem durdur |
| `consecutive_loss` | N ardışık zarar | Durdur + uyarı |
| `stale_data` | Veri yaşı > threshold | Pozisyon kapat |
| `api_failure` | Bağlantı kopuk | Yeni işlem durdur |
| `volatility_spike` | Vol > 3× normal | Yeni işlem durdur |
| `abnormal_spread` | Spread > 5× normal | Yeni işlem durdur |
| `manual` | Elle tetikleme | Tüm pozisyon kapat |

---

## 9. Geliştirme Fazları

| Faz | İçerik | Giriş Kriteri |
|-----|--------|---------------|
| 1 | Tasarım belgeleri + iskelet | — |
| 2 | Veri + labeling + features | Faz 1 tamamlandı |
| 3 | Baseline modeller + backtest | Faz 2 tamamlandı |
| 4 | Paper trading | Faz 3: fee sonrası pozitif expectancy |
| 5 | Risk katmanı | Faz 4: yeterli sample + kabul edilebilir DD |
| 6 | Live execution | Faz 5: kill switch testleri geçti |

---

## 10. LLM / Agent Kullanım Politikası

**İzin verilen (herhangi bir fazda):**
- Deney sonuçlarını özetlemek
- Feature fikirleri önermek
- Log/anomali analizi
- Risk raporu yorumu

**Başlangıçta yasak:**
- 5m/15m yön tahmin ana motoru olarak kullanmak
- Multi-agent consensus ile istatistiksel modelin yerini almak
- Haber/sentiment üzerinden birincil trade kararı vermek

> Önce veri. Önce istatistik. Önce leakage-free backtest. Önce maliyet sonrası edge kanıtı. Ancak o zaman LLM katmanı değerlendirilebilir.

---

## 11. Mühendislik İlkeleri

| İlke | Uygulama |
|------|----------|
| Modüler | Her modül bağımsız test edilebilir |
| Deterministic | Seed sabit, zaman mock'lanabilir |
| Config-driven | Hiçbir eşik hardcoded değil |
| Reproducible | Seed + veri versiyonu = aynı çıktı |
| Single source of truth | Her entity için tek kaynak dosya; orders/fills/positions/equity ayrı ama tutarlı ledger — aralarında reconciliation |
| Kapsamlı log | Her karar loglanır |

**Yasaklar:**
- Fake backtest
- Current context ile geçmiş market değerlendirme
- Future information leakage
- Canlı ve paper state karıştırma
- Docs ile kodun farklı şey söylemesi
