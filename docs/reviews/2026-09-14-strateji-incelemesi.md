# Günlük Strateji İncelemesi — 2026-09-14

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## ⚠️ Önemli bulgu: `main` geriye alınmış, ~40 commit'lik düzeltme kaybolmuş

Bu oturum başladığında `origin/main`, `9b5fd52` ("feat: full bot update",
2026-04-23 tarihli) commit'indeydi. Ancak açık **PR #49**'un base commit'i
`4ef322b` idi — bu repo'daki günlük inceleme serisinin 27. çalışmasının
sonucu ve `main`'e daha önce PR #1–#48 ile tek tek merge edilmiş görünen
~40 commit'lik bir zincirin ucu. `git merge-base --is-ancestor 4ef322b
origin/main` **başarısız** — yani `4ef322b`, şu anki `main`'in atası değil.
Bu, `main`'in bir noktada `9b5fd52`'ye (5 ay öncesine) geri alındığını ve
o zincirdeki tüm düzeltmelerin (double-count, stop-loss wiring,
FRESH_PRICE_ABORT NO-side koruması, streak filter, sinyal ayrıştırma vb.)
`main`'den kaybolduğunu gösteriyor. Bu oturumun kendi container'ındaki
yerel klon bu zinciri hâlâ `claude/brave-faraday-td29s1` dalında taşıyordu,
ama uzak (origin) tarafta o dal artık **silinmiş** durumda.

**Alınan önlem:** Kaybolmaya devam etmesin diye zincirin ucu (`4ef322b`)
`backup/daily-review-chain-2026-09-14` adıyla origin'e push edildi
(hiçbir mevcut dal/branch'e dokunulmadı, sadece yeni ve ekstra bir yedek
dal oluşturuldu). Kullanıcıya bu resetin kasıtlı mı (temizlik) yoksa kaza
mı olduğunu sorulması, ve öyleyse hangi düzeltmelerin geri getirilmesi
gerektiğine karar verilmesi gerekiyor — bu karar bu oturumun kapsamı
dışında tutuldu çünkü tek taraflı geri getirme (~40 commit) riskli ve
büyük bir işlem.

Bu oturum bunun yerine şu anki (resetlenmiş) `main` üzerinde, doğrulanmış
ve gerçekten hâlâ canlı kodda var olan **iki spesifik hatayı** minimal
şekilde düzeltti (aşağıda).

## Düzeltme 1 — `AutonomousDecisionEngine`: HIGH risk STREAK_FILTER'ın SKIP'ini eziyordu

PR #49'un (artık `main`'de yok olan bir base'e dayandığı için birleşemez
durumdaki) açıkladığı hatayı, doğrulayıp şu anki `main` üzerine yeniden
uyguladım (fix aynı, sadece güncel `main`'e karşı yeniden yazıldı ve
regresyon testi eklendi — orijinal PR'ın açıklamasındaki test dosyası
gerçek diff'inde yoktu).

`agents/autonomous_engine.py::AutonomousDecisionEngine.evaluate()`'in
`RiskLevel.HIGH` dalı, `action`'ı koşulsuz olarak `EXECUTE_REDUCED` ile
eziyordu — STREAK_FILTER (4+ ardışık kayıp + edge < 0.08 → kendi devre
kesici koruması) az önce `action = SKIP` yapmış olsa bile.
`RiskLevel.CRITICAL` dalı zaten bu tam senaryoya karşı korumalıydı; HIGH
dalında bu koruma hiç yoktu. Düşük edge risk skoruna da katkı yaptığından,
STREAK_FILTER'ı tetikleyen sinyaller büyük olasılıkla HIGH bandına da
düşüyor — yani niyet edilen SKIP çoğu zaman sessizce iptal ediliyor ve
trade yarı boyutla gerçekten açılıyordu. Bu doğrudan `orchestrator.py`'nin
`auto_decision.should_execute` kapısına, yani gerçek emir verme yoluna
besleniyor.

**Fix:** CRITICAL dalındaki aynı koruma (`action = EXECUTE_REDUCED if
action != SKIP else action`) HIGH dalına da uygulandı.

**Doğrulama:** `tests/test_streak_filter_skip_survives_high_risk.py` (3
test) — fix öncesi kaynağa karşı 1/3 fail (regresyon senaryosu), fix
sonrası 3/3 pass.

## Düzeltme 2 — `agents/signal_agent.py`: tüm test suite'ini import hatasıyla bloke eden syntax hatası

`main`'in resetiyle birlikte, aynı zincirde daha önce (11 Eylül) düzeltilmiş
başka bir hata da geri gelmişti: prompt template'i, dıştaki f-string ile
**aynı** üçlü tırnak (`f"""..."""`) karakterini iç içe kullanıyordu — bu
Python 3.12 öncesinde geçersiz ve modülü import edilemez hâle getiriyor,
dolayısıyla **pytest'in hiçbir testi toplayamamasına** (tüm suite'i
maskeleyerek) yol açıyordu. Bu, `main`'deki hiçbir testin aslında
çalışmadığı/çalışamadığı anlamına geliyordu.

**Fix:** Bu spesifik satırlar için orijinal, zaten doğrulanmış commit'i
(`d20820d`, kaybolan zincirden, `backup/daily-review-chain-2026-09-14`
üzerinden) cherry-pick ettim — iç f-string'leri `f'''...'''`'e çevirip
tek karakterlik tırnak çakışmasını gideriyor, ayrıca `test_execution_path.py`
içindeki güncel `_YES_MAX_PRICE` penceresinin dışında kalmış eski bir
fixture fiyatını düzeltiyor.

## Doğrulama (tam suite)
```
python3 -m pytest tests/ -q
568 passed, 2 skipped
```
(Reset öncesi zincirde suite büyüklüğü 565+3=568 idi — eşleşiyor, regresyon yok.)

## Sonuç / kullanıcıya açık soru
- İki güvenlik-kritik hata düzeltildi ve `main`'e PR olarak açıldı.
- **Açık kalan asıl soru:** `main`'in `9b5fd52`'ye reset edilmesi kasıtlı
  mıydı? Eğer kaza ise, `backup/daily-review-chain-2026-09-14` dalındaki
  ~40 commit'lik düzeltme seti (bkz. yukarıda) gözden geçirilip toplu
  olarak geri getirilmeli — aksi halde bot, aylar önce zaten bulunup
  düzeltilmiş çok sayıda bilinen hatayla canlıda çalışıyor demektir.
