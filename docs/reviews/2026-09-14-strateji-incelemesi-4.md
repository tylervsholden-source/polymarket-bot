# Günlük Strateji İncelemesi — 2026-09-14 (28. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum özeti
- Bu oturum açıldığında `origin/main` üzerinde (`cd2604e`, 26. çalışmanın
  sonucu) **PR #48** (27. çalışma, paralel bir oturumdan) bekliyordu:
  `ReviewerAgent`'ın Claude'un JSON cevabını trade kimliği yerine ham array
  pozisyonuna göre eşlemesi hatası. Bağımsız olarak doğrulandı — izole bir
  worktree'de fix öncesi/sonrası A/B test (`git stash` ile pre-fix kodda
  2/4 yeni test fail, post-fix 4/4 pass) ve tam suite (645 passed, 2
  skipped, PR açıklamasıyla birebir eşleşiyor) — sonra squash-merge edildi
  (`4ef322b`). Yerel branch `origin/main`'e sıfırlandı.
- Ardından `docs/reviews/2026-09-14-strateji-incelemesi-3.md`'de listelenen
  "bu turda incelenip hata bulunamayan" alanlar (coordinator.py,
  signal_agent_v2.py, kelly_criterion.py, polymarket_client.py,
  position_manager.py, main.py, backtesting/engine.py, trade_analyzer.py,
  orchestrator.py'nin directional_count kısmı) hariç tutularak, canlı
  yoldaki henüz incelenmemiş dosyalarda (`agents/autonomous_engine.py`,
  `strategies/arbitrage_engine.py`, `strategies/bayesian.py` vb.) yeni
  bir tur inceleme yapıldı.

## Bugün yapılan işlem: AutonomousDecisionEngine'in HIGH risk dalı, STREAK_FILTER'ın SKIP kararını sessizce eziyordu

### Hata
`agents/autonomous_engine.py::AutonomousDecisionEngine.evaluate()`:

Motorun kendi döngü-kaybı (loss-streak) savunması olan STREAK_FILTER,
4+ ardışık kayıptan sonra edge < 0.08 ise `action = ActionType.SKIP`
atıyor — "zayıf sinyalle kötü bir seri sırasında işleme devam etme"
kuralının kendisi. Ancak birkaç satır sonra, "RİSK SEVİYESİNE GÖRE BOYUT
AYARI" bloğu `RiskLevel.HIGH` için `action`'ı koşulsuz eziyordu:

```python
elif risk_level == RiskLevel.HIGH:
    size_mult = min(size_mult, 0.50)
    action = ActionType.EXECUTE_REDUCED        # SKIP'i sessizce siliyor
    reasons.append("HIGH_RISK: size×0.50")
elif risk_level == RiskLevel.CRITICAL:
    size_mult = min(size_mult, 0.50)
    action = ActionType.EXECUTE_REDUCED if action != ActionType.SKIP else action  # korumalı
    reasons.append("CRITICAL: size×0.50")
```

Hemen altındaki `CRITICAL` dalı zaten aynı korumaya sahip — ama `HIGH`
hiç sahip değildi. `_assess_risk()`'in kendisi zayıf edge (< 0.08) için
risk_score'a +2 eklediğinden (STREAK_FILTER'ı tetikleyen aynı koşul),
STREAK_FILTER'ı tetikleyen bir sinyal neredeyse her zaman `HIGH` bandına
da düşüyor (skor 5-7: zayıf edge +2, NO yönü +2, iki risk flag +2 = 6
gibi). Bu son derece yaygın durumda, amaçlanan SKIP sessizce siliniyor
ve trade yine de (sadece yarı boyutta) çalıştırılıyordu — tam olarak
korumak için var olduğu senaryoda loss-streak korumasını devre dışı
bırakıyordu.

Bu, `agents/orchestrator.py`'nin 715. satırında ana sinyal döngüsünde
çağrılan `autonomous_engine.evaluate()`'in doğrudan çıktısı — gerçek emir
verme `auto_decision.should_execute`'a bağlı, yani hata canlı yolda.
`git blame` ile orijinal `feat` commit'ine (`9b5fd52`) kadar izlendi:
CRITICAL dalı baştan beri doğru yazılmış, HIGH dalı sadece atlanmış;
önceki 27 günlük incelemenin hiçbiri bu satıra dokunmamış.

### Düzeltme
`HIGH` dalına da `CRITICAL` ile aynı koruma eklendi:
```python
action = ActionType.EXECUTE_REDUCED if action != ActionType.SKIP else action
```
Başka hiçbir davranış değiştirilmedi (MED/LOW dalları, CRITICAL'ın kendi
mantığı, reviewer verdict entegrasyonu dokunulmadı).

`tests/test_streak_filter_skip_survives_high_risk.py` eklendi (3 test):
1. Kurulan senaryonun gerçekten `HIGH` risk bandına düştüğünü doğrulayan
   sağlık kontrolü.
2. Asıl regresyon: STREAK_FILTER'ın SKIP'i HIGH boyutlandırma bloğundan
   sağ çıkıyor mu (`decision.action == SKIP`, `should_execute is False`).
3. CRITICAL dalının koruması hâlâ çalışıyor mu (regresyon değil, mevcut
   davranışın korunduğunu doğrulayan kontrol).

## Doğrulama
- Fix öncesi (`git stash` ile fix geri alınmış): yeni testlerden biri
  **fail** — `got ActionType.EXECUTE_REDUCED` (`STREAK_FILTER: 4 kayıp +
  edge=0.060 < 0.08` gerekçesi loglanmasına rağmen trade yine de
  REDUCED olarak çalıştırılıyor).
- Fix sonrası: `pytest tests/test_streak_filter_skip_survives_high_risk.py -v`
  → **3/3 pass**.
- Tam suite (fix sonrası): `pytest tests/ -q` → **648 passed, 2 skipped**
  (645'ten 648'e — sadece bu turun 3 yeni testi, sıfır regresyon).
- `git diff agents/autonomous_engine.py` → tek satırlık koşullu değişiklik
  + açıklayıcı yorum; `CRITICAL` dalının kendi mantığı, MED/LOW dalları,
  reviewer verdict entegrasyonu dokunulmadı.
- Test çalıştırmalarının yan etkisi olan `data/autonomous_state.json`
  commit öncesi eski haline döndürüldü.

## Sonuç
28. çalışma önce paralel bir oturumdan gelen PR #48'i bağımsız doğrulayıp
merge etti. Ardından canlı karar motorunun (`AutonomousDecisionEngine`)
kendi loss-streak güvenlik freni olan STREAK_FILTER'ın, HIGH risk
sınıflandırmasıyla aynı anda tetiklendiğinde (ki bu istatistiksel olarak
sık rastlanan bir çakışma) sessizce ezildiğini buldu — CLAUDE.md'nin
"günlük stop-loss" ve genel risk yönetimi ruhunu doğrudan zedeleyen bir
sınıf hatası. `CRITICAL` dalındaki mevcut korumanın aynısı `HIGH` dalına
eklenerek, üç regresyon testiyle kilitlenerek kapatıldı.
