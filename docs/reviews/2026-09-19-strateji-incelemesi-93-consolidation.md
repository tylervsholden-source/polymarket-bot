# Günlük Strateji İncelemesi — 2026-09-19 (93. tur, konsolidasyon)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = `06ea309` (#163, 91. inceleme sonrası merge).
İki açık PR vardı, ikisi de "92. tur" etiketli, iki farklı eşzamanlı oturum
tarafından aynı `06ea309` üzerine açılmış:

- **#164** (08:13, dal `claude/brave-faraday-4hkusa`) —
  `_record_shadow_decisions()`'ın `execution_realism.compute_executable_ev()`'in
  kendi `passes_gate` sonucunu hiç okumadığını, EXECUTE adaylarının execution
  reality UNFILLABLE/PARTIAL/STALE dese bile her zaman EXECUTE olarak
  kaydedildiğini buldu ve düzeltti.
- **#165** (09:13, dal `claude/brave-faraday-gx75nk`) — `_record_shadow_decisions()`'ın
  REJECT (sinyal eşleşmeyen) adaylar için `rejection_reason`'ı yalnızca
  `NoSideStatus`/`NO_SIGNAL_PRODUCED`'dan ürettiğini, fiyat bayatlığı veya
  şüpheli YES+NO ask toplamı olsa bile bunu hiç kontrol etmediğini buldu ve
  düzeltti.

İkisi de 91. turun "sıradaki tur" notunda bırakılan aynı açık maddeyi
(`shadow_runner/summary_metrics.py`'nin `STALE_PRICING`/
`SUSPICIOUS_UNDERROUND`/`PARTIAL_FILL_REJECTED` string'lerinin canlı yolda
hiç üretilmemesi) bağımsız olarak ele almış, ama **aynı fonksiyonun farklı
dallarını** değiştirmişler: #164 `if is_execute:` dalını (EXECUTE adayının
execution-realism gerekçesiyle REJECT'e düşürülmesi), #165 `elif` zincirini
(zaten REJECT olan adaylar için neden sınıflandırması) değiştiriyor. İkisi de
`mergeable_state=clean` ama birbirine karşı `mergeable_state=clean` değil —
git ikisini art arda merge etmeye çalışırsa `agents/orchestrator.py`'de
çakışır. Bu yüzden bu tur bir **konsolidasyon** turu oldu (89. turdaki
#151-160 konsolidasyonuyla aynı desen).

## Bu turda yapılanlar

### 1. Her iki PR bağımsız olarak doğrulandı
- Diff'ler tek tek okundu, iddiaları kaynağa karşı teyit edildi
  (`execution_realism/core.py`'deki `passes_gate` mantığı, `FillDecision(str,
  Enum)` — yani `er.fill_sim.fill_decision == "PARTIAL"` karşılaştırması
  geçerli —, `calibration/types.py`'deki `CalibrationRejectionReason`,
  `BINARY_SANITY_MIN/MAX_ASK_SUM_LIVE`, `LIVE_CAL_CONFIG.max_snapshot_age_seconds`
  değerleri).
- `pip install -r requirements.txt` ile bağımlılıklar kuruldu (bu oturumda
  başlangıçta `pytest`/`loguru` bile yoktu).
- `origin/main` (`06ea309`) üzerinde baseline: **1731 passed, 4 skipped**.
- `#164` dalı ayrı checkout edilip: yeni testler (`tests/
  test_shadow_execution_realism_gate.py`) 5/5 passed; tam suite → **1736
  passed, 4 skipped** — PR'ın iddiasıyla birebir eşleşti.
- `#165` dalı ayrı checkout edilip: yeni testler (`tests/
  test_shadow_stale_underround_rejection_reason.py`) 4/4 passed; tam suite →
  **1735 passed, 4 skipped** — PR'ın kendi açıklamasında verdiği "895
  passed" sayısı `pytest tests/` gibi daha dar bir alt kümeye aitti, kök
  dizinden tam `pytest -q` çalıştırıldığında 1731+4=1735 ile tutarlı.
- Her iki checkout'ta da testler `data/autonomous_state.json`'a yan etki
  yazıyor; commit öncesi `git checkout -- data/autonomous_state.json` ile
  geri alındı.

### 2. Konsolidasyon: iki düzeltme elle birleştirildi
`#164` önce merge edildi (`git merge --no-ff`, çakışmasız — main'e ilk
değişiklik oydu). Ardından `#165`'in mantığı, `#164`'ün zaten yeniden
yapılandırdığı `if/elif` zincirine elle eklendi:

```python
if _is_execute_after_realism:
    _rejection_reason = None
elif _er_rejection_reason is not None:        # #164: execution_realism gerekçesi
    _rejection_reason = _er_rejection_reason
elif _snapshot_age > LIVE_CAL_CONFIG.max_snapshot_age_seconds:   # #165
    _rejection_reason = CalibrationRejectionReason.STALE_PRICING.value
elif not (BINARY_SANITY_MIN_ASK_SUM_LIVE <= (ask_yes + _ask_no)  # #165
          <= BINARY_SANITY_MAX_ASK_SUM_LIVE):
    _rejection_reason = CalibrationRejectionReason.SUSPICIOUS_UNDERROUND.value
elif _side_diag and _side_diag.direction_reason:
    _rejection_reason = _side_diag.direction_reason
else:
    _rejection_reason = "NO_SIGNAL_PRODUCED"
```

Sıralamanın mantığı: `_is_execute_after_realism` ve `_er_rejection_reason`
yalnızca sinyal eşleşen (`is_execute` başlangıçta True olan) adaylarda
dolu olabilir — bu adaylar zaten `compute_executable_ev()`'in kendi
staleness kontrolünden geçmiş durumda, dolayısıyla #165'in bayatlık/underround
kontrolüne hiç ulaşmazlar (üstteki dal onları yakalar). #165'in eklediği iki
`elif`, yalnızca sinyal eşleşmeyen (`is_execute` hiç True olmamış) REJECT
adayları için devreye giriyor — bu da tam olarak #165'in kendi testlerinin
kapsadığı popülasyon (`sig_match is None`). İki düzeltme farklı aday
kümelerini kapsadığından mantıksal çakışma yok, yalnızca aynı fonksiyondaki
komşu kod satırlarını değiştiriyorlardı.

`calibration.types` import'u (`BINARY_SANITY_MAX_ASK_SUM_LIVE`,
`BINARY_SANITY_MIN_ASK_SUM_LIVE`, `LIVE_CAL_CONFIG`,
`CalibrationRejectionReason`) `#165`'ten olduğu gibi `agents/orchestrator.py`
başına eklendi. `#165`'in test dosyası (`tests/
test_shadow_stale_underround_rejection_reason.py`) değişikliksiz kopyalandı.

**Doğrulama:**
- İki testin birlikte çalıştırılması: `pytest tests/
  test_shadow_execution_realism_gate.py tests/
  test_shadow_stale_underround_rejection_reason.py -v` → **9/9 passed**
  (5 + 4, hiçbiri diğerini bozmadı).
- Tam suite: **1740 passed, 4 skipped** (baseline 1731 + 9 yeni test,
  sıfır regresyon — tam olarak beklenen aritmetik).
- `data/autonomous_state.json` yan etkisi commit öncesi tekrar geri alındı.

## Sıradaki tur için notlar
- 92. turun (her iki versiyonun da) bıraktığı açık madde geçerliliğini
  koruyor: `SUSPICIOUS_UNDERROUND` tespiti yalnızca REJECT (sinyal
  eşleşmeyen) adaylar için eklendi; `is_execute=True` olup execution_realism
  gate'ini geçen bir adayın YES+NO ask toplamı da teorik olarak "şüpheli"
  aralıkta olabilir — ama pratikte bu adaylar zaten üst kademedeki
  SUM_MONITOR/OVERPRICED sinyal filtrelerinden (bkz. `bot_log.txt`'deki
  `OVERPRICED`/`SUM_MONITOR` satırları) geçmiş olduğundan bu bir sonraki tur
  için düşük öncelikli, doğrulanması gereken bir soru olarak bırakılıyor.
- `agents/whale_tracker.py:48`'deki `market` query param sorusu hâlâ
  doğrulanamadı — bu oturumda da `data-api.polymarket.com`'a ağ erişimi
  engelliydi (proxy 403). Kalıcı bir açık madde.
- Zamanlama sıklığı sorunu (86./89. turlarda kullanıcıya bildirildi) — aynı
  gün içinde art arda birden fazla bağımsız oturumun aynı 5 dakikalık
  pencerede (~08:13 ve ~09:13) çakışan PR'lar açması, bu sorunun hâlâ devam
  ettiğinin somut bir kanıtı. Tekrar ayrıca bildirilmiyor (önceden net
  raporlandı), ama bu turun konsolidasyon ihtiyacının doğrudan nedeni bu.
