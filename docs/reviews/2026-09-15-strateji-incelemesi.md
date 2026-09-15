# Günlük Strateji İncelemesi — 2026-09-15 (37. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Kritik bulgu: `main`'e doğrudan, incelemesiz büyük bir push geldi ve 36
## günlük incelemenin en kritik düzeltmelerini geri aldı

### Kapsam
Oturum açıldığında bu dalın tabanı (`b82ceb4`, 36. çalışma, PR #62) hâlâ
güncel sanılıyordu, ama `origin/main` o commit'ten SONRA `9b5fd52` ("feat:
full bot update — smart filters, autonomous engine, dashboard, trade
analyzer", yazar `lcladm`, PR süreci dışında doğrudan push) ile ilerlemiş.
Bu commit `agents/orchestrator.py`, `core/position_manager.py`,
`agents/autonomous_engine.py` gibi tam da 36 günlük incelemenin üzerinde
çalıştığı dosyaları eski/farklı bir soy ile baştan yazmış — net -434 satır.
`docs/reviews/` (36 incelemenin tam arşivi) de bu push'ta tamamen kayboldu.

Karşılaştırma (`git diff b82ceb4 9b5fd52`) şunu gösterdi: bu değişiklik en
azından üç, zaten düzeltilmiş kritik hatayı sessizce geri getirmiş:

1. **CLAUDE.md'nin "Değiştirme" başlığı altındaki -%15 günlük stop-loss
   kuralı tamamen devre dışı bırakılmış.** `agents/orchestrator.py`'de hem
   `_cycle()` içindeki doğrudan emir yolunda hem de
   `_execute_approved_orders()` (dashboard onay kuyruğu) yolunda:
   ```python
   daily_loss_exceeded=False,  # devre dışı — kullanıcı talebi (2026-03-21)
   ...
   daily_stop = False  # devre dışı — kullanıcı talebi (2026-03-21)
   ```
   Bu, 11. çalışmanın (#15, "wire daily -15% stop-loss into live gate, was
   hardcoded to False") tam olarak düzelttiği hatanın birebir aynısı. Yorum
   bir "kullanıcı talebi" iddia ediyor ama CLAUDE.md hâlâ bu kuralı
   değiştirilemez olarak listeliyor ve hiçbir dokümana bu istisna
   yansımamış — bu regresyonun eski/paralel bir koddan geldiğine işaret
   ediyor, gerçek bir yeni karara değil. **Etki: canlı modda gerçek bir
   -%15+ günlük kayıp artık HİÇBİR yeni emri engellemiyor.**

2. **`_sync_real_balance()` yine `daily.pnl`'i güncellemeden `capital`'i
   düzeltiyor** — 36. çalışmanın (#62) düzelttiği hatanın birebir tekrarı.
   CLOB bakiye düzeltmesi artık günlük stop-loss paydasına hiç yansımıyor.

3. **`_sync_real_balance()`'daki `locked` hesabı yine süresi geçmiş ama
   resolve olmamış pozisyonları hariç tutuyor** — 33. çalışmanın (#59)
   düzelttiği hatanın birebir tekrarı. `capital` (dolayısıyla Kelly
   boyutlandırma, SURVIVAL eşiği ve stop-loss paydası) o pozisyonlar kadar
   eksik hesaplanıyor.

Ayrıca: `agents/subagents/coordinator.py`'nin (35. ve 37. çalışmaların
bulgu alanı) bu commit'te var olup olmadığı, ve iki açık PR'ın (#63, #64 —
aşağıya bakın) bulgularının yeni `main`'de hâlâ geçerli olup olmadığı derin
incelenmedi (kapsam dışı bırakıldı — aşağıdaki "Sıradaki tur" bölümüne
taşındı).

### Düzeltme
Üç hata da `b82ceb4`'teki daha önce doğrulanmış haliyle geri getirildi:

- `daily_loss_exceeded=False` / `daily_stop = False` → her iki çağrı
  noktasında `self.position_manager.daily_loss_exceeded(self.daily_stop_loss)`.
- `_sync_real_balance()`: `locked` artık tüm açık pozisyonları kapsıyor
  (end_time filtresi kaldırıldı); `capital` düzeltmesi aynı miktarda
  `data["daily"]["pnl"]`'e de yansıtılıyor (gün geçişi kontrolü dahil).

Düzeltme sırasında `_sync_real_balance()` içinde daha önce fark edilmemiş
dördüncü bir hata ortaya çıktı: fonksiyonun `return`'den sonraki (asla
çalışmayan) ölü kod bloğu içinde tekrar eden bir `from datetime import
datetime, timezone` satırı vardı. Python'da fonksiyon içinde herhangi bir
yerde yapılan import/atama, o ismi fonksiyonun TAMAMI için lokal yapar —
bu yüzden yeni eklediğim `datetime.now(...)` çağrısı, hiç çalışmayan o ölü
kod satırı yüzünden `UnboundLocalError` fırlatıyordu. Fazladan/ölü import
satırı silindi (üst düzey `datetime`/`timezone` importu zaten yeterli).

### Yan bulgu: tüm test suite'i toplanamıyordu
`agents/signal_agent.py` (belgede "DEVRE DIŞI" olarak işaretli, canlı yolda
kullanılmayan eski kod) Python 3.11'de geçersiz bir nested f-string
kullanıyordu (`f"""..."""` içinde tekrar `f"""..."""`, PEP 701 öncesi aynı
tırnak karakterinin iç içe kullanımı yasak). Bu, `pytest tests/`'in HİÇBİR
testi toplayamamasına (`Interrupted: 19 errors during collection`) yol
açıyordu — yani düzeltmemi doğrulamak için önce bunu çözmem gerekti. İç
`f"""`'ler `f'''`'e çevrildi, davranış değişmedi.

### Test
- `tests/test_sync_balance_daily_pnl_desync.py` (36. çalışmanın kayıp
  arşivden geri getirilen testi, 3 test) — 9b5fd52 kaynağına karşı 1/3 fail
  (kök senaryo reprodüklendi), düzeltme sonrası 3/3 pass.
- `tests/test_daily_stop_loss_hardcoded_false.py` (yeni, 2 test) —
  9b5fd52 kaynağına karşı kök senaryo fail (`assert False is True`),
  düzeltme sonrası 2/2 pass. İkinci test simetri: eşik altı bir gün
  hardcoded `True`'ya da dönmediğini doğruluyor.
- Tam suite: `pytest tests/ -q` → düzeltme öncesi (signal_agent.py hariç
  diğer düzeltmeler geri alınmış): toplanamıyor. Düzeltme sonrası: **569
  passed, 2 skipped, 1 failed**. Kalan 1 fail
  (`test_execution_path.py::test_full_sim_execution_chain`) düzeltme
  öncesi `9b5fd52`'de de aynı şekilde fail ediyor — bu PR'ın kapsamına
  girmeyen, önceden var olan ayrı bir hata (sıradaki tur için not edildi).

### Doğrulama
- `git stash` ile düzeltme öncesi kaynak: her iki yeni/geri getirilen test
  dosyasının kök senaryosu fail ediyor (yukarıda gösterildiği gibi).
- Düzeltme sonrası: ilgili 5 test pass, tam suite'te sıfır yeni regresyon
  (569 passed vs. düzeltme-öncesi-signal_agent-fix-sonrası 564 passed +
  5 yeni test).

## Açık PR'lar hakkında not (#63, #64)
Bu oturumdan önceki iki paralel "37. çalışma" PR'ı (#63: onay kuyruğu
token_id + coordinator confluence re-sort; #64: YES pozisyonu gerçek sıfır
bid maskeleme) `b82ceb4` tabanından açılmış ve `9b5fd52` push'undan
etkilenmemiş — ama artık güncel `main`'in gerisindeler ve `agents/
orchestrator.py`/`core/position_manager.py` bu push'ta baştan yazıldığı
için muhtemelen doğrudan merge edilemeyecekler (dosyalar çok değişti).
Bulguları hâlâ geçerli görünüyor (bu incelemenin dokunduğu alanlardan
bağımsız), ama yeni `main`'e karşı yeniden uygulanmaları/doğrulanmaları
gerekiyor. Bu oturum bunu yapmadı — kapsam ve süre nedeniyle sıradaki tura
bırakıldı.

## Sonuç
`main`'e giden 9b5fd52 push'u, 36 günlük incelemenin en kritik güvenlik
bulgusunu (günlük -%15 stop-loss) ve iki muhasebe düzeltmesini (#59, #62)
sessizce geri almıştı. Üçü de bu turda yeniden uygulandı ve testle
kanıtlandı. Kullanıcıya bulgu anında (düzeltme tamamlanmadan) bildirim
gönderildi, çünkü gerçek sermayeyi koruyan bir mekanizmanın devre dışı
kalması riski, düzeltmenin tamamlanmasını beklemeyecek kadar acildi.

**Sıradaki tur için önerilen kapsam:**
1. PR #63 ve #64'ün bulgularını yeni `main`'e karşı yeniden doğrula/uygula.
2. `agents/subagents/coordinator.py`, `strategies/monte_carlo.py`,
   `control_plane/*`'in 9b5fd52 ile ne kadar değiştiğini tarayıp başka
   geri alınmış düzeltme olup olmadığını kontrol et.
3. `CIRCUIT_BREAKER tamamen kaldırıldı — kullanıcı talebi` (loss-streak
   cooldown) — bu, 9b5fd52'den ÖNCE de mevcuttu (yeni bir regresyon değil),
   ama CLAUDE.md'de belgelenmemiş bir risk kontrolü kaldırma olarak
   kullanıcıya doğrulatılmalı.
4. `test_execution_path.py::test_full_sim_execution_chain` (önceden var
   olan, bu PR'ın kapsamı dışında bırakılan fail).
