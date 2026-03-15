# Calibration Layer — Specification

Sürüm: Phase 7 (Mart 2026)

---

## 1. Amaç

Crypto directional model çıktısını (UP/DOWN/NO_TRADE) Polymarket olay olasılığına
dönüştürmek ve edge-positive yalnızca kararları üretmek.

Bu katman **kural tabanlı ve deterministik**tir. LLM/AI karar mantığı yoktur.

---

## 2. Desteklenen Horizon'lar

```python
SUPPORTED_HORIZONS = {5, 15}  # dakika
```

Bu set dışındaki `horizon_minutes` değerleri `UNSUPPORTED_HORIZON` ile reddedilir.
Yeni horizon eklemek için hem bu seti hem kalibrasyon artifact'lerini güncellemek gerekir.

---

## 3. Polarity Eşleştirme Tablosu

| Polarity | predicted_class | bridge_intent_side | effective_yes_prob   | effective_no_prob    |
|----------|-----------------|--------------------|----------------------|----------------------|
| NORMAL   | UP              | YES                | calibrated_up_prob   | calibrated_down_prob |
| NORMAL   | DOWN            | NO                 | calibrated_up_prob   | calibrated_down_prob |
| INVERTED | UP              | NO                 | calibrated_down_prob | calibrated_up_prob   |
| INVERTED | DOWN            | YES                | calibrated_down_prob | calibrated_up_prob   |

**Önemli:** `effective_yes_prob` ve `effective_no_prob` yalnızca polaritiye bağlıdır;
`predicted_class` bu değerleri etkilemez. `predicted_class` yalnızca
`bridge_intent_side`'ı belirler.

---

## 4. Olasılık Toplamı Politikası

```
PROB_MIN_SUM  = 0.50  # toplam < bu → INCONSISTENT_PROBS
PROB_MAX_OVER = 0.01  # toplam > 1 + bu → INCONSISTENT_PROBS
```

Geçerli aralık: `0.50 ≤ (up + down + no_trade) ≤ 1.01`

Alt sınır gerekçesi: Toplam < 0.50 olan modeller eksik tanımlı bir olasılık uzayına
işaret eder; anlamlı edge hesabı yapılamaz.

---

## 5. Karar Sırası (11 Adım)

| Adım | Kontrol                          | Ret Sebebi               |
|------|----------------------------------|--------------------------|
| 1    | Kalibrasyon kalitesi WEAK        | WEAK_CALIBRATION         |
| 2    | Kalibrasyon kalitesi UNKNOWN     | UNKNOWN_CALIBRATION      |
| 3    | class_probabilities zorunluluğu  | UNKNOWN_CALIBRATION      |
| 4    | Horizon kontrolü                 | UNSUPPORTED_HORIZON      |
| 5    | Pricing yapısal doğrulaması      | INVALID_PRICING          |
| 6    | Pricing yaşı (horizon-aware)     | STALE_PRICING            |
| 7    | Likidite                         | LOW_LIQUIDITY            |
| 8    | bridge_intent_side geçerliliği   | AMBIGUOUS_MAPPING        |
| 9    | Spread (side-aware)              | HIGH_SPREAD              |
| 10   | Minimum kalibre olasılık         | LOW_CONFIDENCE           |
| 11   | Edge eşiği                       | NEGATIVE_EDGE            |

---

## 6. Edge Formülü

```
gross_EV             = calibrated_event_probability - ask_price
net_EV               = gross_EV - assumed_taker_fee_pct
execution_adjusted   = net_EV - slippage_pct          ← karar metriği
passes_edge_gate     = execution_adjusted >= min_execution_adjusted_edge
```

`slippage_pct`: YES tarafı için `assumed_slippage_yes_pct` (None ise `assumed_slippage_pct`),
NO tarafı için `assumed_slippage_no_pct` (None ise `assumed_slippage_pct`).

---

## 7. Kalibrasyon Politikaları

| Politika          | reject_unknown | reject_weak | require_class_probs | min_exec_adj_edge |
|-------------------|----------------|-------------|---------------------|-------------------|
| DEFAULT_CAL_CONFIG| False          | False       | False               | 0.02              |
| PAPER_CAL_CONFIG  | False          | False       | False               | 0.02              |
| LIVE_CAL_CONFIG   | **True**       | **True**    | **True**            | **0.03**          |

Live modda `class_probabilities=None` olan sinyaller (ham confidence proxy)
otomatik reddedilir.

---

## 8. Tasarım Sözleşmeleri

- **Bridge-intent-only:** Karar katmanı yalnızca `bridge_intent_side` tarafını değerlendirir.
  Karşı taraf daha ucuz olsa bile sisteme geçirilmez.
- **Sessiz fallback yok:** Her ret açık bir `CalibrationRejectionReason` taşır.
- **Deterministik:** Aynı girdi → her zaman aynı çıktı.
- **Horizon sözleşmesi:** `horizon_minutes` `SUPPORTED_HORIZONS` dışında ise
  kalibrasyon artifact'leri geçerli değildir; reject edilmesi zorunludur.

---

## 9. Bilinen Sınırlar (Faz 7 sonrası)

- Slippage sabit — size/liquidity bağımsız (Faz 8'de giderilecek)
- Probability sum policy canlıda 0.50–0.99 aralığını geçirir; daha sıkı sınır düşünülmeli
- Calibration lifecycle (drift, re-fit) henüz yok (Faz 10)
- Market template taxonomy henüz yok (Faz 11)
