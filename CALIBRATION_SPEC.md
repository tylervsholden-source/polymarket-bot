# Kalibrasyon Katmanı Teknik Spesifikasyonu

## Genel Bakış

Bu katman, `crypto_directional` model çıktısını Polymarket fiyatıyla kıyaslanabilir
kalibre edilmiş olay olasılığına dönüştürür.

```
crypto_directional  →  calibration  →  signal_bridge  →  polymarket_bot
RawSignalOutput        CalibratedSignal + EdgeEstimate     CLOB emir
                       TradeDecision
```

**Bu katmanın varlık nedeni:**
Ham sınıflandırıcı güveni (`predict_proba` max değeri) ile piyasa fiyatı olasılık
biçiminde kıyaslanamaz. Bu iki büyüklük aynı para biriminde değildir.

## Ham Model Çıktısı (RawSignalOutput)

```
asset              : "BTC", "ETH", ...
horizon_minutes    : 5 veya 15
predicted_class    : UP / DOWN / NO_TRADE  (argmax sınıfı)
raw_confidence     : max(predict_proba)    (0.0–1.0)
class_probabilities: {"UP": 0.72, "DOWN": 0.18, "NO_TRADE": 0.10}  (opsiyonel)
model_version      : "v0"
```

`raw_confidence` Nedir ve Ne Değildir:
- **Nedir**: Modelin bu örnekte en olası sınıfa verdiği ham tahmin skoru.
- **Ne değildir**: "Bu sinyal gerçekleşirse %72 ihtimalle UP olur" garantisi.
- Sınıflandırıcılar varsayılan olarak kalibre değildir. Overconfident veya
  underconfident olabilir.

## Kalibre Edilmiş Olasılığa Dönüşüm

Hedef: `raw_confidence` → `calibrated_up_prob` / `calibrated_down_prob`

### Kalibrasyon Yöntemleri

| Yöntem | Açıklama | Veri İhtiyacı |
|--------|----------|----------------|
| `identity` | Ham güveni olduğu gibi kullan (başlangıç proxy'si) | Sıfır |
| `platt` | Sigmoid uyumu (A*x + B parametreleri) | ≥ 50 örnek |
| `isotonic` | Monotonic regresyon (sklearn) | ≥ 200 örnek |

**Varsayılan: `identity`** — canlı veri toplanana kadar.

Identity kalibrasyonu ham güveni kullanır; bu bilinçli bir varsayımdır, körlük değildir.
Kalibrasyon kalitesi `calibration_quality: "unknown"` olarak işaretlenir.

### Kalibrasyon Uyumu Kuralları (Leakage Önleme)

1. Kalibrasyon parametreleri YALNIZCA validation set üzerinde fit edilir.
2. Test seti veya canlı verisi kalibrasyon uyumunda kullanılamaz.
3. Walk-forward backtesting'de her fold kendi kalibrasyon modelini kullanır.
4. `identity` kalibrasyonda uyum yapılmaz; leakage riski sıfırdır.

## Polarity → Olay Olasılığı Eşleştirmesi

Bridge katmanı market'in wording polarity'sini belirler.
Bu bilgi kalibre edilmiş yön olasılığını "YES resolves" olasılığına çevirmek için kullanılır.

```
NORMAL polarity (örn. "Bitcoin Up or Down"):
    P(YES resolves) = calibrated_up_prob
    P(NO resolves)  = calibrated_down_prob

INVERTED polarity (örn. "Will BTC fall below X?"):
    P(YES resolves) = calibrated_down_prob  ← DOWN → YES
    P(NO resolves)  = calibrated_up_prob    ← UP → NO
```

`effective_yes_prob` ve `effective_no_prob` bu eşleştirme sonucunda belirlenir.

NOT: NO_TRADE sinyali için polarity eşleştirmesi yapılmaz → REJECT.

## Kabul Edilebilir Varsayımlar

1. `class_probabilities` mevcut değilse, simetrik varsayım:
   `calibrated_down_prob ≈ 1 - raw_confidence` (NO_TRADE görmezden gelindi)
2. NO_TRADE sinyali — hem YES hem NO için edge yoktur; REJECT.
3. Kalibrasyon kalitesi ölçülemiyor ise → `calibration_quality: "unknown"`, edge hesabı devam eder ancak bu zayıflık TradeDecision'a yansır.

## Açıkça Varsayılmayanlar

1. Ham güven kalibre edilmiş olasılıktır — **HAYIR**
2. Yüksek confidence yüksek edge garantiler — **HAYIR**
3. Düşük confidence kesinlikle ticaret edilemez — **HAYIR** (Polymarket fiyatı çok düşükse düşük confidence da karlı olabilir)
4. Kalibrasyon yöntemi doğru olasılıklar üretir — SADECE yeterli veri ile

## Red Koşulları

Kalibrasyon katmanı aşağıdaki durumlarda **açıkça REJECT** üretir:

| Durum | Sebep |
|-------|-------|
| predicted_class = NO_TRADE | Edge yok, yön belirsiz |
| class_probabilities None ve raw_confidence < 0.5 | Argmax sınıfı dominant değil |
| polarity = "AMBIGUOUS" | Eşleştirme mümkün değil |
| reject_on_weak_calibration=True ve quality="weak" | Kalibre güven yetersiz |
| effective_yes_prob + effective_no_prob > 1.0 + tol | İnkonsistent olasılık |

## Kalibrasyon Diagnostikleri

Fit edilmiş kalibratörler şu metrikleri raporlar:
- **Brier Score**: Ortalama kare hata (≤ 0.25 makul, ≤ 0.15 iyi)
- **Log Loss**: Negatif log likelihood (≤ 0.50 makul)
- **ECE** (Expected Calibration Error): Bucket-tabanlı kalibrasyon hatası (≤ 0.10 kabul edilebilir)

`identity` kalibratörde bu metrikler hesaplanamaz → None.

## Limitasyonlar

1. **Kısa horizon**: 5m / 15m marketlerde sinyal ve market arasında bilgi gecikmesi var.
2. **Tarihsel veri yetersizliği**: Kalibrasyon için yeterli resolved market geçmişi yok (Phase 5).
3. **Stale pricing**: Market fiyatları bayat olabilir; yaş kontrolü `max_snapshot_age_seconds` ile yapılır.
4. **Market microstructure**: Düşük likidite marketlerde fiyat sinyali zayıftır.
