# Edge Tahmin Mantığı

## Fiyat Sözleşmesi

Polymarket fiyatları **[0, 1] aralığında olasılık birimi** olarak normalize edilmiştir.
Karıştırılmaması için:
- `0.45` = $0.45 / share = piyasa YES'e %45 olasılık biçiyor
- Share başı ödeme = `ask_price` (giriş için)
- Share başı gelir = $1.00 (eğer seçilen taraf resolve olursa)

Tüm hesaplamalar 0-1 ölçeğindedir. Cent veya dolar karışımı yoktur.

## Temel Beklenen Değer Formülü

### YES Tarafı Girişi

```
p     = calibrated_event_probability  (P(YES resolves))
q     = ask_yes                       (giriş fiyatı)
c     = assumed_taker_fee_pct         (tek yön maliyet, örn. 0.01)

gross_EV  = p * (1 - q) + (1 - p) * (-q)
          = p - q

net_EV    = p - q - c
edge      = net_EV = p - q - c
```

Türetme:
- Kazanırsak: share başı kazanç = `1 - q`
- Kaybedersek: share başı kayıp = `q` (yatırılan miktar)
- EV = p × kazanç + (1-p) × (-kayıp) = p(1-q) - (1-p)q = p - q

### NO Tarafı Girişi

```
p_no  = calibrated_event_probability  (P(NO resolves))
q_no  = ask_no                        (giriş fiyatı — NO tarafı)
c     = assumed_taker_fee_pct

gross_EV_no = p_no - q_no
net_EV_no   = p_no - q_no - c
edge_no     = net_EV_no
```

## Side-Aware Fiyat Kullanımı

| İşlem Tarafı | Giriş Fiyatı | Spread | Kalibre Olasılık |
|-------------|-------------|--------|-----------------|
| YES | `ask_yes` | `ask_yes - bid_yes` | `effective_yes_prob` |
| NO | `ask_no` | `ask_no - bid_no` | `effective_no_prob` |

YES spread'ini NO işlemi için kullanmak **hatalıdır**.
Asimetrik orderbook'larda bu, yanlış maliyet tahminine yol açar.

## Maliyet Varsayımları

```
assumed_taker_fee_pct = 0.01   (Polymarket taker fee ~%1)
```

Tek yönlü maliyet (`c = 0.01`) giriş maliyeti olarak sayılır.
Round-trip (giriş + çıkış) maliyeti bu fazda modellenmez;
pozisyon resolve olana kadar tutulduğu varsayılır.

İdeal round-trip modeli (Phase 5):
```
round_trip_cost = c_entry + c_exit
edge_round_trip = p - q - round_trip_cost
```

## Edge Eşiği

```
min_edge_after_fee = 0.02   (varsayılan, config ile override edilebilir)
```

Ticaret kararı:
```
if edge >= min_edge_after_fee:
    → EXECUTE
else:
    → REJECT (NEGATIVE_EDGE)
```

## Örnek Senaryolar

### Senaryo 1: Net Pozitif Edge

```
Sinyal: BTC UP, confidence=0.72
Kalibrasyon: identity → calibrated_up_prob=0.72, polarity=NORMAL → eff_yes_prob=0.72
Market: ask_yes=0.45, fee=0.01

gross_EV = 0.72 - 0.45 = 0.27
net_EV   = 0.27 - 0.01 = 0.26

edge = 0.26 ≥ 0.02 → EXECUTE YES
```

### Senaryo 2: Edge Yok — Piyasa Zaten Fiyatlamış

```
Sinyal: BTC UP, confidence=0.72
Market: ask_yes=0.71, fee=0.01  (piyasa aynı görüşte)

gross_EV = 0.72 - 0.71 = 0.01
net_EV   = 0.01 - 0.01 = 0.00

edge = 0.00 < 0.02 → REJECT (NEGATIVE_EDGE)
Yorum: Sinyal doğru olabilir ama piyasa bunu zaten fiyatladı.
```

### Senaryo 3: Maliyet Sonrası Edge Yok

```
Sinyal: BTC UP, confidence=0.65
Market: ask_yes=0.63, fee=0.01

gross_EV = 0.65 - 0.63 = 0.02
net_EV   = 0.02 - 0.01 = 0.01

edge = 0.01 < 0.02 → REJECT
Yorum: Gross pozitif ama fee sonrası eşiğin altında.
```

### Senaryo 4: Yüksek Güven, Zayıf Net Edge

```
Sinyal: ETH UP, confidence=0.85
Market: ask_yes=0.84, fee=0.01

gross_EV = 0.85 - 0.84 = 0.01
net_EV   = 0.01 - 0.01 = 0.00

edge = 0.00 → REJECT
Yorum: Yüksek confidence ≠ yüksek edge. Piyasa seni takip etmiş.
```

### Senaryo 5: Kalibrasyon Zayıf

```
Sinyal: BTC UP, raw_confidence=0.65
Kalibrasyon: identity, quality="unknown"
identity → calibrated_up_prob=0.65

not: ham güven kalibre edilmemiş; bu olasılık gerçek değil, proxy.
reject_on_weak_calibration=False → devam et (uyarı ile)
reject_on_weak_calibration=True  → REJECT (WEAK_CALIBRATION)
```

### Senaryo 6: Ambiguous Market Mapping

```
Polarity: AMBIGUOUS
→ effective_yes_prob ve effective_no_prob belirlenemiyor
→ REJECT (AMBIGUOUS_MAPPING)
Yorum: Edge hesabı yapılamaz çünkü hangi tarafın ne anlama geldiği bilinmiyor.
```

## Ham Güven Neden Yetersiz?

Ham güven ile Polymarket ask fiyatını direkt kıyaslamak şu sebepler için hatalıdır:

1. **Kalibrasyon eksikliği**: Sınıflandırıcı %72 der ama gerçekte %60 anlamına gelebilir.
2. **Sınıf dengesizliği**: Eğitim veri setinde UP/DOWN/NO_TRADE oranları eşit değilse,
   ham softmax çıktıları sapmalı olur.
3. **Threshold arbitraryliği**: `raw_confidence > 0.58` eşiği (bridge config'deki)
   kalibre edilmiş bir ticaret sinyali değil, ön filtreleme için kullanılır.

Doğru akış:
```
raw_confidence
    ↓ calibrator.transform()
calibrated_probability
    ↓ compare with ask_price
edge = calibrated_probability - ask_price - fee
    ↓ edge >= threshold?
EXECUTE or REJECT
```

## Ertelenmiş Konular (Phase 5)

- Round-trip maliyet modeli
- Liquidity impact (büyük pozisyonlarda slippage)
- Kelly sizing entegrasyonu ile calibrated probability
- Zaman serisi kalibrasyon güncellemesi (online learning)
- Bid-side çıkış optimizasyonu
