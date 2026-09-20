# Günlük Strateji İncelemesi — 2026-09-20 (104. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = `f47c5cc` (#186 dahil, 103./103b. turların PR'ları
merge edilmiş). Bu branch (`claude/brave-faraday-icgr92`) `origin/main` ile tam
eşit başladı, açık farkı yoktu.

Konteynerde çalışan bir bot instance'ı yok (`data/status.json`/`control.json`/
`data/positions.json` bu sandbox'ta yok, canlı Polymarket/Anthropic API'lerine
ağ erişimi yok) — %10 hedefine karşı gerçek zamanlı sermaye ilerlemesi bu
oturumdan doğrulanamıyor. Katkı, önceki turların bıraktığı yerden kod/strateji
doğruluğu seviyesinde devam etti.

## Baseline doğrulama
- `pip install -r requirements.txt` çalıştırıldı.
- `python3 -m pytest tests/ calibration/tests execution_realism/tests signal_bridge/tests crypto_directional/tests -q`
  → **1773 passed, 4 skipped** (bu branch'in başlangıç durumu; 103b'nin
  1771'inden +2, aradan main'e giren küçük bir artış).
- `data/autonomous_state.json` yan etkisi (bilinen davranış) her çalıştırma
  sonrası `git checkout -- data/autonomous_state.json` ile geri alındı.

## Bu turda incelenen alanlar
103./103b. turların "sıradaki tur için notlar" bölümlerinde işaret edilen,
o zamana kadar hiç ayrı ayrı derin taranmamış iki aday kümesi ele alındı:

1. **`agents/subagents/reviewer_agent.py`** (455 satır) ve
   **`agents/subagents/research_agent.py`** (379 satır) — tamamı satır
   satır okundu.
2. **`shadow_runner/{readiness,validation,runner,replay,summary_metrics}.py`**
   (toplam ~1250 satır) — tamamı satır satır okundu, 103'ün "muhtemelen ölü
   kod, teyit edilmeli" notu doğrulandı.

### `agents/subagents/reviewer_agent.py` / `research_agent.py` — canlı yola bağlı, zaten yoğun sertleştirilmiş

`git log --oneline -- agents/subagents/reviewer_agent.py` tek bir commit
gösteriyor olsa da (#117, 69. tur — dosya o zaman toplu eklenmiş), dosyanın
içeriği zaten çok sayıda belgelenmiş savunma içeriyor: `ReviewDecision.approved`
VETO'yu bilerek dahil ediyor (autonomous_engine'in "severely reduced but not
skipped" dalının ölü kalmaması için), `_parse_claude_response()` Claude'un
JSON cevabını `trade_number` ile eşleştiriyor (pozisyonel zip yerine, sırası
karışan/atlanan trade'lere karşı), `suggested_size_pct` 0-1'e clamp'leniyor,
rule-based fallback'teki risk-flag kontrolleri exact-match kullanıyor (prefix
eşleşmesi hariç `COUNTER_REGIME_*` için — o da doğru). `agents/subagents/
research_agent.py::_extract_regime()` de benzer şekilde önceden düzeltilmiş
bir hata (`get_market_regime()` yerine var olmayan `_regime` attribute'unun
okunması) hakkında ayrıntılı yorum içeriyor.

`agents/subagents/coordinator.py` (`_re_enrich_signals()`) ve
`agents/autonomous_engine.py` (`evaluate()`'in REVIEWER_VETO/REDUCE entegrasyon
bloğu, ~satır 218-246) ile çapraz okundu — reviewer verdict'inin
suggested_size_pct'i coordinator'da bir kez uygulanıyor, orchestrator/
autonomous_engine'de tekrar uygulanmıyor (double-application önceki turlarda
zaten düzeltilmiş, 52. tur). `elif edge < 0.05: action = SKIP` (autonomous_engine
satır 259-261), önceki STREAK_FILTER/HIGH_RISK/REVIEWER_VETO'nun
"action != SKIP ise EXECUTE_REDUCED'a çevir" koruma desenini bozmuyor — bu
satır zaten var olan bir SKIP'i geri EXECUTE'a çevirmiyor, sadece yeni,
bağımsız bir SKIP koşulu ekliyor.

Bu iki dosyada (ve çapraz kontrol edilen coordinator.py/autonomous_engine.py
parçalarında) yeni bir hata **bulunamadı**.

### `spot_macd` — ölü alan (yeni tespit, dokunulmadı)
`research_agent.py::get_market_context()`'in ürettiği `spot_macd` alanı
hem `signal_agent_v2.py`'de hem `coordinator.py::_re_enrich_signals()`'da
hiç okunmuyor (`grep -rn spot_macd` → sadece üretildiği satır). Sıfır canlı
etkisi olduğu için (CLAUDE.md sadelik kuralı, repo'nun `check_paired_profit`/
`quality_filter.py` ile aynı örüntüsü) düzeltme/kaldırma yapılmadı, sadece
kaydediliyor.

### `shadow_runner/*` — 103'ün "muhtemelen ölü kod" şüphesi YANLIŞ, paket canlı yola bağlı

103. turun notu: *"`shadow_runner/{runner,replay,reporting,validation}.py`
(yalnızca shadow/paper mod mu besliyor yoksa gerçek karara sızıyor mu
netleştirilmeli)"*. Bu tur doğrulandı: **paket canlı yola bağlı**.
`agents/orchestrator.py` `shadow_runner.journal`/`shadow_runner.types`'ı
doğrudan import ediyor ve her cycle'da paralel bir "shadow decision" kaydı
üretiyor; bu kayıtlar `shadow_runner/summary_metrics.py` →
`shadow_runner/readiness.py::assess_readiness()` → `monitoring/daily_review.py`
→ `data/readiness_verdict.json` → `control_plane/live_gate.py::_check_readiness()`
zinciriyle **gerçek emir verme yolunu gate'liyor** (`verdict !=
"TINY_PILOT_CANDIDATE"` ise canlı trading bloklanıyor). `calibration/
decision_policy.py::decide()`'ın kendisi (canonical politika) hiçbir yerden
`agents/`/`strategies/`/`core/` tarafından doğrudan çağrılmıyor — sadece
`shadow_runner/runner.py::ShadowRunner.evaluate()` üzerinden shadow modda
çalışıyor; `agents/orchestrator.py` kendi inline mantığıyla bu politikayı
"mirror" ediyor (satır ~2830 civarındaki uzun yorumlar bu paralelliği ve
geçmişte bulunan sapmaları belgeliyor). Yani `decision_policy.decide()`
canlı emir yoluna girmiyor ama onun ÇIKTISI (readiness metrikleri üzerinden)
canlı trading'in açılıp açılmayacağını belirliyor — "ölü kod" değil,
"read-only ama gate'leyen" bir zincir.

**Sonraki turlar için düzeltme**: bu şüphe artık kapandı, tekrar
"muhtemelen ölü kod" olarak listelenmemeli.

### `shadow_runner/readiness.py::assess_readiness()` — mantık doğru, docstring eski

Modülün başındaki "Design decisions" listesi (madde 3): *"FAIL checks: 2 or
more FAILs produce NO_GO; 1 FAIL produces CONDITIONAL."* Ama gerçek kod
(`elif len(fails) >= 1: verdict = NO_GO`) **tek bir FAIL'de bile NO_GO**
üretiyor — docstring ile kod çelişiyor. `tests/test_live_pilot_readiness.py`
bunu açıkça doğruluyor: `test_...` — *"Only underround > 25% → 1 FAIL → NO_GO
(Task 5.3: any FAIL blocks pilot)"* — yani mevcut davranış **kasıtlı ve
test'lerle kilitlenmiş** (madde 5 "GO requires 0 FAILs" ile de tutarlı);
madde 3'ün metni sadece Task 5.3'ten önceki eski tasarımdan kalma, hiç
güncellenmemiş bir docstring. `control_plane/live_gate.py::_check_readiness()`
zaten sadece `verdict == "TINY_PILOT_CANDIDATE"` mi diye bakıyor —
`NO_GO` ile `CONDITIONAL_REVIEW` canlı gate açısından eşdeğer (ikisi de
bloklar) — yani bu bir davranış hatası değil, sadece yanıltıcı bir yorum.

**Düzeltme**: docstring madde 3, gerçek/test edilmiş davranışı yansıtacak
şekilde güncellendi (davranış/kod değişmedi):

```
3. FAIL checks: any FAIL produces NO_GO (Task 5.3: any FAIL blocks pilot —
   tightened from an earlier "1 FAIL -> CONDITIONAL" draft; see
   tests/test_live_pilot_readiness.py's "1 FAIL -> NO_GO" cases).
```

Bunu bir "hata düzeltmesi" olarak değil, doğruluğu bozulmuş bir yorum
düzeltmesi olarak sınıflandırıyorum — sıfır davranış değişikliği, sıfır
regresyon riski. Amacı: bir sonraki turun bu docstring'i "1 FAIL →
CONDITIONAL olmalı ama değil" şeklinde yanlış bir lead olarak tekrar
keşfetmesini önlemek.

### `shadow_runner/summary_metrics.py::partial_fill_execute_count` — dataclass yorumu ile implementasyon arasında küçük, etkisiz bir fark

`ShadowSummaryMetrics.partial_fill_execute_count` alanının yorumu: *"executes
with fill_fraction in `[0.10, 0.95)`"*. Ama `compute_summary_metrics()`'teki
gerçek hesap yalnızca üst sınırı uyguluyor:

```python
partial_fill_exec = sum(
    1 for r in executes
    if r.decision_summary.fill_fraction is not None
    and r.decision_summary.fill_fraction < 0.95
)
```

Alt sınır (`>= 0.10`) hiç kontrol edilmiyor — `fill_fraction` 0.10'un altında
(örn. 0.02) olan bir execute de bu sayaca dahil oluyor. `grep -rn
partial_fill_execute_count` → tek tüketicisi kendi test dosyası
(`tests/test_shadow_acceptance_metrics.py`, sadece `>= 0 ve <= execute_count`
kontrol ediyor, alt sınırı test etmiyor) ve `artifacts/*.json` örnek
çıktıları — `monitoring/readiness_checks.py`'nin hiçbir check'i bu alanı
okumuyor (okuduğu `partial_fill_rejection_rate`, ayrı bir alan:
`partial_fill_rejected_count / total`, REJECT kararları için, EXECUTE'lar
için değil). Yani bu **canlı gate'e hiç girmeyen, salt-raporlama** bir alan;
CLAUDE.md'nin sadelik kuralı gereği (test'le kilitlenmemiş, davranışı
belirsiz bir alt sınırı "düzeltmek" spekülatif bir değişiklik olurdu)
dokunulmadı, sadece kaydediliyor.

## Sonuç
`agents/subagents/reviewer_agent.py`, `agents/subagents/research_agent.py`
ve `shadow_runner/{readiness,validation,runner,replay,summary_metrics}.py`
satır satır incelendi. Pozisyon boyutlandırma, yön, risk gate'leme veya
sermaye muhasebesini bozan **gerçek** bir canlı-karar hatası bulunamadı.
En değerli sonuç negatif+düzeltici: 103. turun "shadow_runner muhtemelen ölü
kod" şüphesi yanlış olduğu doğrulandı — paket gerçekten `data/
readiness_verdict.json` üzerinden canlı trading'i gate'liyor, bu yüzden
gelecekteki turlarda tekrar "muhtemelen dead code" olarak taranmamalı.
Ayrıca `shadow_runner/readiness.py`'deki yanıltıcı bir docstring
(kod/test davranışıyla çelişen "1 FAIL → CONDITIONAL" ifadesi) düzeltildi —
davranış değişikliği yok.

## Tam suite
- Baseline (oturum başı): **1773 passed, 4 skipped**.
- Docstring düzeltmesinden sonra: **1773 passed, 4 skipped** (0 regresyon,
  0 yeni test — beklenen, çünkü davranış değişmedi, sadece yorum düzeltildi).

## Sermaye/performans notu
Bu oturumda çalışan bir bot instance'ı yok — %10 hedefine karşı gerçek
ilerleme doğrulanamıyor. Bu turun katkısı: (1) reviewer/research subagent
katmanının canlı yola doğru bağlı ve önceki turlarda zaten sertleştirilmiş
olduğunun doğrulanması, (2) shadow_runner/calibration zincirinin gerçekten
canlı trading'i gate'lediğinin kesinleştirilmesi (önceki bir turun yanlış
şüphesini düzeltiyor) — ikisi de gelecekteki tur zaman bütçesinin doğru
alanlara yönlenmesini sağlıyor.

## Sıradaki tur için notlar
- `agents/subagents/reviewer_agent.py`, `agents/subagents/research_agent.py`,
  `shadow_runner/{readiness,validation,runner,replay,summary_metrics}.py`
  artık tamamen taranmış ve temiz — kısa vadede tekrar bakmak düşük getirili.
- `shadow_runner/calibration` paketinin canlı gate'e bağlı olduğu artık
  kesinleşti (bkz. yukarı) — "ölü kod olabilir" listesinden çıkarıldı.
- Henüz ayrı ayrı derin taranmamış adaylar: `shadow_runner/{journal,
  reporting,types}.py`, `monitoring/{readiness_checks,regime_review,
  drift_monitor,metrics,alerts}.py` (readiness zincirinin geri kalanı —
  `check_ev_haircut_pct` vb. tek tek fonksiyon seviyesinde henüz
  doğrulanmadı), `agents/hit_rate_tracker.py`, `agents/copytrade.py`,
  `agents/enhanced_signals.py` (103b'nin notu, bu turda ele alınmadı).
- Zero-canlı-etki, dokunulmayan iki yeni tespit kaydedildi: `research_agent.py`
  `get_market_context()`'in `spot_macd` alanı hiç okunmuyor;
  `summary_metrics.py::partial_fill_execute_count`'un alt sınırı
  (`>= 0.10`) dataclass yorumunda var ama kodda uygulanmıyor — ikisi de
  test'le kilitlenmemiş, canlı gate'e girmeyen alanlar, bir sonraki tur
  "yeni keşif" olarak tekrar raporlamamalı.
