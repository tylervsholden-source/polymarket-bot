# Günlük Strateji İncelemesi — 2026-09-18 (86. tur)

## Durum
Oturum başında `origin/main` = bu branch = `a2662e8` (#149, 85. inceleme
sonrası — `Orchestrator._analyze_new_closed_trades()`'ın sim/paper modunda
hiç çalışmaması düzeltilmiş). Açık PR yoktu (`gh`/GitHub API üzerinden
doğrulandı — 0 open PR). `git branch -r` 159 uzak dal listeledi; bunların
büyük çoğunluğu zaten merge edilmiş eski `claude/brave-faraday-*` review
dallarının kalıntısı, aktif iş içermiyor.

## Bu turda yapılanlar

### Bulunan ve düzeltilen hata: `requirements.txt`'te `scikit-learn` eksik — hem test suite collection'ı kırıyor hem de canlıda ML sinyalini sessizce devre dışı bırakıyor

Bu ortamda `python3 -m pytest tests/ -q` çalıştırıldığında yalnızca
**863 passed** görüldü (85. turun raporladığı 1695'in yarısından az).
Sebep bulundu: `pytest.ini`'nin `testpaths`'i `tests/` dışında
`calibration/tests`, `execution_realism/tests`, `crypto_directional/tests`'i
de kapsıyor; sade `pytest tests/` bu üçünü atlıyor. Bare `pytest -q`
(gerçek CI/test komutu) çalıştırıldığında ise şu hata çıktı:

```
ImportError while importing test module
'crypto_directional/tests/test_backtest.py'
crypto_directional/backtests/metrics.py:12: in <module>
    from sklearn.metrics import precision_recall_fscore_support
ModuleNotFoundError: No module named 'sklearn'
```

`requirements.txt` içinde `scikit-learn` hiç yok — bu container'da sadece
`pip install -r requirements.txt` çalıştırılmıştı. `pip install
scikit-learn` sonrası tam suite 85. turun raporuyla birebir eşleşti:
**1695 passed, 4 skipped**. Yani 85. turun sayısı doğruydu (o oturumda
sklearn zaten kuruluymuş/kurulmuş) — burada tespit edilen gerçek kusur,
`requirements.txt`'in eksik olması ve bunun **sessizce** iki farklı yere
zarar vermesi:

1. **Fresh clone/CI**: `pip install -r requirements.txt` sonrası
   `crypto_directional/tests/test_backtest.py` collection'da patlıyor —
   `pytest.ini`'nin `testpaths`'ine göre tam suite hiç yeşil olamıyor.
2. **Canlı risk sinyali sessizce devre dışı**: `strategies/ml_classifier.py`
   `sklearn`'ü `try/except ImportError` ile sarıyor
   (`_ML_AVAILABLE = False`), ve `TradeClassifier._load_model()` bu
   durumda **hiçbir log basmadan** `return` ediyordu. `ml_score`
   `strategies/arbitrage_engine.py:1870`'te üretiliyor ve doğrudan canlı
   Kelly boyutlandırmasını etkiliyor (`ML_CAUTION`: ml_score < -0.5 →
   pozisyon küçült; `ML_BOOST`: ml_score > 0.5 → yüksek güven log'u;
   ayrıca `ML_THIN_LIQ_CAP`/`ML_MACRO_CAP` gate'leri). `data/ml_model.pkl`
   (118KB, eğitilmiş model) repo'da mevcut — bu sinyalin canlıda aktif
   olması bekleniyor. `requirements.txt`'i birebir izleyen herhangi bir
   fresh deployment'ta bu sinyal, operatöre hiçbir uyarı vermeden sürekli
   `0.0` (nötr) dönecekti — CLAUDE.md'nin "Doğrulama Zorunlu" ve API/hata
   yollarının görünür olması ilkesine aykırı bir sessiz bozulma.

### Uygulanan düzeltme
- `requirements.txt`: `scikit-learn>=1.3.0` eklendi (numpy/pandas
  pinleriyle uyumlu, mevcut sürüm aralığı).
- `strategies/ml_classifier.py::_load_model()`: `_ML_AVAILABLE` False
  ise artık `logger.warning(...)` ile açıkça log basıyor — sklearn
  gelecekte tekrar eksik kalırsa (ör. farklı bir deploy ortamı) sessiz
  kalmıyor.

Kapsam dışı bırakılan, minimal değişiklik ilkesiyle dokunulmayan yerler:
`calibration/calibrator.py` ve `strategies/ml_classifier.py` dışındaki
`sklearn` kullanan diğer dosyalar (`review_bundle/`, `incident_bundle*/`
altındakiler — bunlar canlı kod yolunda değil, eski inceleme paketleri).

### Doğrulama
```
pip install scikit-learn
python3 -m pytest -q
# 1695 passed, 4 skipped, 1 warning in 17.08s  (85. turla birebir aynı — regresyon yok)
python3 -m pytest tests/ -k "ml_classifier or ml_class" -q
# 14 passed
```
CLAUDE.md'nin risk kuralları (max %20 pozisyon, günlük -%15 stop, max 5
açık pozisyon, min $5,000 hacim, min 0.05 edge) değiştirilmedi.

## Operasyonel bulgu (kod dışı, kullanıcıya ayrıca bildirildi)
Bu "günlük" inceleme görevi, son ~36 saatte 85 kez tetiklendi (`git log
--format='%ad' -- docs/reviews/` → 2026-09-17 02:06'dan 2026-09-18
17:11'e kadar art arda, bazen aynı dakika içinde birden fazla tur).
Bu, "her gün" beklentisiyle uyuşmuyor ve zamanlayıcı/tetikleyici
yapılandırmasının çok daha sık (muhtemelen ~15-30 dakikada bir) ateşlendiğini
gösteriyor. Bu oturumdan zamanlayıcı ayarına erişim yok (`CronList` bu
oturumda oluşturulan job'ları listeliyor, hesap düzeyindeki tetikleyiciyi
değil) — kullanıcının kendi tetikleyici/schedule yapılandırmasını
gözden geçirmesi gerekiyor.

## Sıradaki tur için notlar
- `agents/whale_tracker.py:48`'deki `market` query param sorusu
  (`data-api.polymarket.com/trades`'e istek atarken doğru filtre parametresi
  mi) bu turda da doğrulanamadı — egress bu ortamda engelli, hâlâ açık.
- `requirements.txt` artık `scikit-learn` içeriyor; bir sonraki tur bu
  ortamda `pip install -r requirements.txt` sonrası ekstra `pip install
  scikit-learn` adımına gerek kalmadan tam suite'in collection hatasız
  geçtiğini doğrulamalı.
