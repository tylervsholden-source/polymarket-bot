# crypto_directional — Kripto Yön Tahmin Modülü

> **Mevcut polymarket botunu koru; yeni hedefi eski strateji gövdesine zorla yamama.**

Bu modül Polymarket botundan **tamamen bağımsızdır**. Aynı repoda yaşar ama ayrı bir pipeline, ayrı veri kaynağı ve ayrı tahmin mantığı kullanır.

---

## Ne Yapar

Binance Futures Perpetual piyasasında **BTCUSDT** ve **ETHUSDT** için:

- **5 dakikalık** ve **15 dakikalık** fiyat yönü tahmini
- Her bar kapanışında `UP / DOWN / NO_TRADE` kararı
- Fee + slippage sonrası ölçülebilir edge arayışı

---

## Ne Yapmaz

| Özellik | Durum |
|---------|-------|
| Polymarket YES/NO token | Yok — bu modülde binary settlement yok |
| CLOB order / token_id routing | Yok — CLOB Polymarket'e özgü |
| Prediction market discovery | Yok — Gamma API bu modülde kullanılmıyor |
| event/conditional settlement | Yok |
| SignalAgent / WhaleTracker | Yok — Polymarket'e özgü |
| ArbitrageEngine / BayesianEstimator | Yok — Polymarket için tasarlandı |
| LLM ana tahmin motoru (başlangıçta) | Yok — önce istatistik |

---

## Geliştirme Fazları

```
Faz 1  ← Şu an
  Tasarım belgeleri: SPEC.md, DATA_SCHEMA.md, FEATURE_CATALOG.md
  Klasör iskeleti, config yapısı

Faz 2  (sonraki)
  Veri çekimi: OHLCV, OB, funding, OI, liquidations
  Labeling pipeline
  Feature engineering

Faz 3
  Baseline modeller: LR, RF, XGBoost
  Walk-forward backtest
  Metrik raporlama

Faz 4
  Paper trading (canlı veri, sanal fill)
  Paper raporu

Faz 5
  Risk katmanı: kill switches, sizing, daily limit

Faz 6
  Live execution (ancak tüm önceki fazlar geçildikten sonra)
```

---

## Hızlı Başlangıç (Faz 2'den itibaren)

```bash
# Bağımlılıklar (mevcut requirements.txt yeterli olmayabilir)
pip install ccxt pandas numpy scikit-learn lightgbm pyarrow pyyaml

# Veri çek (Faz 2)
python crypto_directional/scripts/fetch_data.py --symbol BTCUSDT --interval 5m

# Model eğit (Faz 3)
python crypto_directional/scripts/train_model.py --horizon 5m

# Paper trading başlat (Faz 4)
python crypto_directional/scripts/run_paper.py
```

---

## Bağımsızlık Notu

Bu modül Polymarket botunun hiçbir dosyasını `import` etmez.

Paylaşılan tek katman `shared/` altındaki genel amaçlı yardımcılar:
- `shared/logging_utils/` — loguru wrapper
- `shared/config/` — YAML config loader
- `shared/utils/` — genel yardımcılar

Polymarket botuna özgü hiçbir sınıf veya fonksiyon bu modüle girmez.

---

## Dosya Yapısı

```
crypto_directional/
├── README.md               ← Bu dosya
├── SPEC.md                 ← Sistem spesifikasyonu
├── DATA_SCHEMA.md          ← Veri şemaları
├── FEATURE_CATALOG.md      ← Feature kataloğu
├── config/
│   ├── settings.py         ← Config loader
│   └── defaults.yaml       ← Varsayılan parametreler
├── data/
│   ├── collectors/         ← Binance veri çekme
│   ├── loaders/            ← Parquet/CSV okuma
│   ├── preprocessing/      ← Temizleme, hizalama
│   └── storage/            ← Dosya yönetimi
├── labeling/
│   └── label_generator.py  ← UP/DOWN/NO_TRADE üretimi
├── features/
│   ├── momentum.py
│   ├── mean_reversion.py
│   ├── volatility.py
│   ├── microstructure.py
│   ├── derivatives.py
│   └── feature_pipeline.py
├── models/
│   ├── baselines.py
│   ├── train.py
│   └── predict.py
├── backtests/
│   ├── walk_forward_backtest.py
│   └── metrics.py
├── paper/
│   ├── paper_trader.py
│   └── paper_report.py
├── risk/
│   ├── risk_manager.py
│   ├── sizing.py
│   └── kill_switches.py
├── execution/              ← En son dolacak (Faz 6)
│   ├── exchange_adapter.py
│   ├── order_manager.py
│   └── reconciliation.py
├── tests/
└── scripts/
```
