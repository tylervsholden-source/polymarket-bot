# Günlük Strateji İncelemesi — 2026-09-15 (39. çalışma)

## Kapsam
Bir önceki turun (38., PR #66 — henüz merge edilmemiş, `main` hâlâ `a0e3d10`)
bıraktığı taranmamış alanlarla başlandı: `control_plane/*` (approval_queue,
entry_window_guard, expiry_guard, live_gate, reentry_guard, process_lock),
`strategies/monte_carlo.py`, `strategies/ml_classifier.py`. Bu modüllerin
hepsi satır satır okundu; `control_plane/*` INC-2026-03-15-001 sonrası
yazılmış, disiplinli ve test edilebilir görünüyor (expiry/entry-window/
approval state machine'leri tutarlı). Genuine bir hata bulunamadı orada.

Paralel olarak canlı-yol pozisyon boyutlandırma zincirine
(`strategies/arbitrage_engine.py::_evaluate_market()`) odaklanıldı — bu
fonksiyon her sinyalde Kelly boyutunu hesaplayıp bir dizi çarpan/cap
uyguluyor (confidence mult → Kelly floor → **MAX BET CAP** → ML quality
score → **GOLDEN HOUR boost**). Sıra kontrol edilirken gerçek bir bulgu
çıktı.

## Bulgu (39.) — `GOLDEN_HOUR`/`GOOD_HOUR` boost, `MAX_BET_CAP`'ten SONRA çalışıyor; kendi yorumunun vaat ettiği "capped at max_bet" garantisini bozuyor

### Hata
`strategies/arbitrage_engine.py::_evaluate_market()` içinde sıra şöyle:

```python
# ── MAX BET CAP ──────────────────────────────────────────────────
_MAX_BET = 4.0
if size > _MAX_BET:
    size = _MAX_BET
...
# ── GOLDEN HOUR BOOST ────────────────────────────────────────────
# Data: 5-8PM ET (1-4AM TR) = 73-100% WR, +$138 profit.
# These hours have highest edge — boost Kelly by 1.3x (capped at max_bet).
if _gh_hour in _GOLDEN_HOURS:        # {17, 18, 19} ET
    size = size * 1.30
elif _gh_hour in _GOOD_HOURS:        # {11, 15, 3, 4} ET
    size = size * 1.15
```

`_MAX_BET` cap'i boost'tan **önce** uygulanıyor ve boost'tan sonra bir daha
uygulanmıyor. Yorum satırı açıkça "capped at max_bet" diyor — yani kodun
niyeti boost sonrası da $4.00'ı aşmamak — ama kod bunu hiç garanti etmiyor.
Kelly, sermaye birkaç yüz doların üzerine çıktığında (bu botta rutin bir
durum — `capital * max_position_pct` ile sınırlı, $1000 sermayede $200'e
kadar) rahatlıkla $4'ün çok üzerinde bir boyut öneriyor, `MAX_BET_CAP`
bunu $4.00'a indiriyor, sonra Golden Hour penceresinde (17-19 ET) boost
onu **$5.20**'ye (`$4.00 × 1.30`), Good Hour penceresinde (11/15/3/4 ET)
**$4.60**'a (`$4.00 × 1.15`) çıkarıyor. `ML_CAUTION` yolu bunun tersi
yönde çalışıyor (`size *= 0.5`, cap'i asla aşmaz) ama boost yolu hiç
kontrol edilmiyor.

### Somut senaryo
Sermaye $1000, güçlü bir 5dk BTC sinyali (edge yüksek, ADX≥20, ml_score
nötr). Kelly `position_size()` $50 öneriyor (gerçekçi: `max_position_pct`
default 0.20 → tavan $200). `MAX_BET_CAP` bunu $4.00'a indiriyor —
loglar `MAX_BET_CAP: ... $50.00 → $4.00` diyor. Saat 17:25 ET (Golden
Hour). `GOLDEN_HOUR` bloğu çalışıyor: `size = 4.00 * 1.30 = 5.20` —
log `GOLDEN_HOUR: ... $4.00→$5.20 (×1.30)`. Sinyal `$5.20` boyutla
döner ve gerçek emir olarak `place_order()`'a gider. Bu, botun kendi
belgelediği per-trade risk tavanının **%30 üzerinde** gerçek sermaye ile
açılan bir pozisyon — sessizce, hiçbir uyarı olmadan (log satırı bile
"başarı" gibi görünüyor, cap ihlali olarak işaretlenmiyor).

Bu sınıf hata bu deponun geçmişinde defalarca görülen "yanlış pozisyon
boyutlandırma" hata ailesiyle aynı (ör. 22. çalışma — risk-bazlı size cut'ın
Kelly floor'a geri clamp'lenmesi, #43; 14. çalışma — Monte Carlo'nun farklı
MAX_POSITION_PCT default'u kullanması, #32) — burada da bir boyut
düzeltmesinin (cap) bir başka boyut düzeltmesi (boost) tarafından
sırayla ezilmesi söz konusu.

### Düzeltme
`strategies/arbitrage_engine.py::_evaluate_market()` — Golden/Good Hour
boost bloğunun sonuna, cap'i tekrar uygulayan üç satır eklendi:

```python
elif _gh_hour in _GOOD_HOURS:
    ...
# Re-apply the hard cap: the boost above is documented as
# "capped at max_bet" but multiplying after MAX_BET_CAP already
# ran can otherwise push size past _MAX_BET (e.g. $4.00 × 1.30 =
# $5.20), a real oversized live position.
if size > _MAX_BET:
    size = _MAX_BET
```

Üretim davranışı değişmedi (boost'un kendisi, oranları hâlâ aynı) — sadece
yorumun vaat ettiği "capped at max_bet" garantisi artık gerçekten
uygulanıyor.

### Test
`tests/test_golden_hour_boost_bypasses_max_bet_cap.py` (1 test) —
`ArbitrageEngine.analyze()`'i gerçek BTC market/feed mock'larıyla, saat UTC
21:30 (=17:30 ET, Golden Hour) donmuş şekilde çalıştırıyor;
`engine.kelly.position_size` cap'i çok aşan sabit bir değer ($50), `engine.
ml.predict` nötr skor (0.0) döndürecek şekilde mock'lanmış (ML_CAUTION'ın
sonucu maskelemesini engellemek için). `signals[0].size <= 4.0` doğrulanıyor.

- Düzeltme öncesi kaynakla (`git stash`): **1 failed** —
  `AssertionError: GOLDEN_HOUR boost pushed size to $5.20, above the
  documented $4.00 MAX_BET_CAP ceiling` (hata reprodüklendi, log satırları
  `MAX_BET_CAP: ... $50.00 → $4.00` ardından `GOLDEN_HOUR: ... $4.00→$5.20`
  gösteriyor).
- Düzeltme sonrası: **1 passed**.
- Tam suite: `pytest tests/ -q` → **698 passed, 1 failed, 2 skipped**
  (701 toplam = 697 taban + bu turun 1 yeni testi). Tek başarısızlık,
  bu PR'ın değişikliğiyle **alakasız**: `tests/
  test_reduce_verdict_size_not_double_applied.py::
  test_reduce_verdict_does_not_shrink_size_multiplier_below_suggested` —
  `agents/autonomous_engine.py`'nin `LOW_LIQUIDITY_HOURS` (UTC 0-6) gerçek
  duvar-saati kontrolü yüzünden zaman-bağımlı flaky bir test (suite bu
  turda UTC 02:10'da çalıştı). Bu, tam olarak açık/unmerged **PR #66**'nın
  ("38. çalışma") ele aldığı ve düzelttiği (sadece test dosyasında
  `time.gmtime()` mock'lanarak) bulgu — çakışmayı önlemek için bu PR aynı
  test dosyasına dokunmuyor. `git stash` ile bu PR'ın değişikliği
  devre dışı bırakılıp aynı test tekrar çalıştırıldığında **aynı şekilde
  failed** oluyor — yani bu çalışmanın değişikliğinin sebep olduğu bir
  regresyon değil, önceden var olan ve PR #66'da zaten iş listesinde olan
  bir durum.
- `data/autonomous_state.json` (test suite'in yan etkisi) commit öncesi
  geri alındı.

## Sonuç
`strategies/arbitrage_engine.py::_evaluate_market()`'teki Golden/Good Hour
boost artık $4.00 MAX_BET_CAP'i gerçekten aşamıyor. Sıradaki tur için
öneriler: (1) PR #66 merge olduktan sonra bu PR'ın rebase edilmesi (tek
kalan başarısızlık kendiliğinden çözülecek), (2) `agents/subagents/*` ve
`strategies/walk_forward.py` hâlâ derinlemesine incelenmedi, (3)
`_evaluate_market()` içindeki `_hour_et` hesaplaması `_get_current_et_hour()`
patch point'ini kullanmıyor — kendi ayrı `datetime.now(timezone.utc)`
çağrısını yapıyor; bu, GOLDEN_HOUR/GOOD_HOUR bloğunu test eden gelecekteki
testlerin de gerçek duvar saatine bağımlı hale gelmemesi için bilinmesi
gereken bir nokta (bu PR'ın kendi testi, `ae.datetime`'ı doğrudan
monkeypatch'leyerek bunu atlatıyor).
