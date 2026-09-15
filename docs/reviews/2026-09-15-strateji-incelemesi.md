# Günlük Strateji İncelemesi — 2026-09-15 (38. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Bölüm 1: birikmiş PR kuyruğu temizlendi

Oturum açıldığında `main` hâlâ `b82ceb4` (36. çalışma, PR #62) üzerindeydi ve
paralel oturumlardan kalan **2 açık, unmerged PR** vardı — ikisi de aynı
tabandan (`b82ceb4`) dallanmış, ikisi de kendi testleriyle doğrulanmış, ikisi
de `mergeable_state: clean`, ikisi de "37. çalışma" etiketli:

| PR | Dosya | Bulgu |
|----|-------|-------|
| #63 (37. çalışma) | `agents/orchestrator.py::_execute_approved_orders()` / `agents/subagents/coordinator.py::_re_enrich_signals()` | Approval-queue yolunda `token_id` pozisyona yazılmıyordu (NO fiyatlama fallback riski) + PHASE 2 confluence yeniden hesaplandıktan sonra liste yeniden sıralanmıyordu (MAX_DIRECTIONAL/risk budget yanlış sinyali seçebiliyordu) |
| #64 (37. çalışma) | `core/position_manager.py::update_positions()` | Gerçek `best_bid == 0.0` kotasyonu "kotasyon yok" ile karıştırılıp `entry_price`'a geri düşülüyordu — 45dk timeout'ta YES pozisyonu LOSS yerine NEUTRAL kapanıyordu |

Her ikisi de ayrı ayrı doğrulandı: diff'ler satır satır okundu, PR'a özel
testler izole çalıştırıldı (`#63`: 2/2 passed, `#64`: 4/4 passed), sonra
tam suite her PR için ayrı ayrı doğrulandı. İkisi de `main`'e `squash` ile
alındı (`#63` → `13c9e3c`, `#64` → `a0e3d10`). Kod tarafında sıfır çakışma:
biri `orchestrator.py`+`coordinator.py`'ye, diğeri `position_manager.py`'ye
dokunuyordu.

Tam suite, birleşik `main` üzerinde (698 test + 2 skip'e çıkmadan önce, o an
697 passed) **1 test hariç** yeşildi — bu tek başarısızlık bir sonraki
bölümün konusu.

## Bölüm 2: yeni bulgu (38.) — zaman-bağımlı flaky test, gerçek regresyonu 6 saat/gün gizliyor

### Kapsam
Birleşik suite'i (`pytest tests/ -q`) `main`'in yeni HEAD'inde (`a0e3d10`)
çalıştırırken `tests/test_reduce_verdict_size_not_double_applied.py::
test_reduce_verdict_does_not_shrink_size_multiplier_below_suggested`
başarısız oldu:

```
AssertionError: expected no additional shrink beyond coordinator's own ×0.6,
got size_multiplier=0.7 (would compound to ×0.420 total)
assert 0.7 == 1.0
...
LOW_LIQUIDITY_HOURS: UTC 1
```

### Kök neden
`agents/autonomous_engine.py::AutonomousDecisionEngine.evaluate()` her
çağrıda `time.gmtime()` ile **gerçek duvar saatini** okuyor ve UTC 0-6
arasıysa (CLAUDE.md'nin belgelediği kasıtlı, gerçek üretim davranışı —
"Gece saatleri → düşük likidite (×0.70)") `size_mult = min(size_mult, 0.7)`
uyguluyor. Bu satır 267-268'de, REVIEWER_REDUCE bloğundan (satır 233-246)
*sonra* çalışıyor ve `size_mult`'ı bağımsızca küçültebiliyor.

`test_reduce_verdict_size_not_double_applied.py` (36. çalışmanın REDUCE
double-apply düzeltmesini kilitleyen test) hiçbir risk flag'i olmayan "temiz"
bir sinyalle `size_multiplier == 1.0` bekliyor — ama `time.gmtime()`'ı hiç
mock'lamıyor. Sonuç: bu test suite, çalıştığı ana bağlı olarak **günün
%25'inde (UTC 0-6) deterministik olarak başarısız oluyor**, geri kalan
%75'inde geçiyor. Bu sadece kozmetik bir CI gürültüsü değil — tam da bu
saatlerde REVIEWER_REDUCE double-apply regresyonu gerçekten geri gelse,
aynı "beklenen" görünen `0.7 != 1.0` uyuşmazlığı olarak görünür ve gerçek
regresyon fark edilmeden geçer. Test, kendi var olma amacını (double-apply'ı
yakalamak) tam da onu en çok ihtiyaç duyduğu saatlerde kaybediyor.

Reprodüksiyon (düzeltme öncesi kaynakla, `git stash` sonrası, UTC 01:07'de
çalıştırıldı):
```
1 failed, 1 passed in 0.13s
```

### Düzeltme
`tests/test_reduce_verdict_size_not_double_applied.py`'deki iki teste de
`monkeypatch.setattr(time, "gmtime", lambda *a: _DAYTIME_UTC)` eklendi
(`_DAYTIME_UTC` = sabit UTC 12:00 `struct_time`) — repo'da zaten
`tests/test_daily_loss_midnight_rollover.py`'nin kullandığı "modül seviyesi
zaman referansını monkeypatch'le dondur" deseniyle tutarlı. Üretim kodunda
(`agents/autonomous_engine.py`) hiçbir değişiklik yok; LOW_LIQUIDITY_HOURS
davranışı kasıtlı ve doğru, sadece test onu hesaba katmıyordu.

### Test
- Düzeltme öncesi (`git stash`, UTC 01:07 gerçek saatte): **1 failed, 1
  passed** — kök senaryo tam olarak reprodüklendi (LOW_LIQUIDITY_HOURS UTC
  0-6 penceresinde).
- Düzeltme sonrası: **2/2 passed**, artık çalıştırıldığı saatten bağımsız.
- Tam suite: `pytest tests/ -q` → **698 passed, 2 skipped** (697 taban + bu
  PR'ın kendisi yeni test eklemiyor, sadece 2 mevcut testi deterministik
  hale getiriyor), sıfır regresyon.
- `data/autonomous_state.json` (test suite'in yan etkisi) commit öncesi
  geri alındı.

## Sonuç
`main` artık 37 daily-review düzeltmesinin tamamına (34 açık PR dahil) ve bu
turun test-determinizm düzeltmesine sahip. Sıradaki tur için taranmamış alan
olarak `control_plane/*`, `strategies/monte_carlo.py`,
`strategies/ml_classifier.py` öneriliyor — henüz derinlemesine incelenmedi.
Ayrıca `agents/autonomous_engine.py` ve `core/polymarket_client.py`'deki
diğer `time.gmtime()`/gerçek-duvar-saati bağımlılıklarının testlerde benzer
şekilde mock'lanıp mock'lanmadığı sistematik olarak taranmadı — bu turun
kapsamı sadece gözlemlenen somut başarısızlığı kapsıyor.
