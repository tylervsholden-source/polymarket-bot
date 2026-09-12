# Günlük Strateji İncelemesi — 2026-09-12 (5. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç.

## Durum özeti
- `origin/main` = `f0baf6a` (3. incelemeyle aynı), çalışma ağacı temiz.
  `pip install -r requirements.txt` sonrası `pytest tests/` → **574 passed,
  2 skipped**. Kod tabanında bu inceleme ile 3. inceleme arasında hiçbir
  değişiklik yok.
- Yine `data/control.json` / `data/positions.json` / `.env` yok (gitignore +
  runtime-üretimi) → gerçek açık pozisyon veya acil müdahale gerektiren bir
  durum yok. `data/` altındaki `.bak`/`_backup` dosyaları 2026-03 tarihli,
  eski test/sim verisi — canlı durumu yansıtmıyor.

## Bulgu: 3. incelemenin bıraktığı açık karar iki ayrı PR'da tek taraflı çözülmüş

3. inceleme, günlük -%15 stop-loss'un `agents/orchestrator.py`'de
(`daily_loss_exceeded=False` / `daily_stop=False`, yorum: "devre dışı —
kullanıcı talebi 2026-03-21") sabit kodlu olarak etkisiz bırakıldığını,
bunun CLAUDE.md'nin değiştirilemez kural listesiyle çelişse de üçüncü kez
tek taraflı değiştirilmediğini kaydetmişti.

Bu çalışmada gördüm ki, bu incelemeden sonra **iki ayrı zamanlanmış
inceleme** (birbirinden habersiz, ~3 saat arayla) bu maddeyi kendi
inisiyatifiyle çözmüş ve aynı iki-satırlık düzeltmeyi (`daily_loss_exceeded`
/ `daily_stop`'u gerçek `position_manager.daily_loss_exceeded(...)` çağrısına
bağlamak) iki ayrı PR'da açmış:
- **#15** — testli versiyon (`tests/test_daily_stop_loss_wiring.py` eklenmiş,
  576 passed / 2 skipped iddia ediyor).
- **#16** — aynı kod değişikliği, testsiz.

İkisi de "CLAUDE.md'nin sabit kuralı, koddaki tarihli yorumdan üstündür"
gerekçesiyle karar almış ama hiçbiri koddaki "2026-03-21 kullanıcı talebi"
iddiasının doğruluğunu/yanlışlığını doğrulayan yeni bir kanıt sunmuyor —
sadece CLAUDE.md'nin yazılı otoritesine dayanıyor.

## Bugün yapılan
- Kod tabanı, testler, git senkronu doğrulandı (değişiklik yok).
- **#16'yı #15'in duplicate'i olarak kapattım** (aynı iki satırlık değişiklik,
  #15 testli olduğu için daha eksiksiz) ve PR'a bunun gerekçesini + insan
  onayı gerektiğini belirten bir not bıraktım.
- **#15'i merge etmedim.** Bu, gerçek canlı trading risk davranışını
  değiştiren ve koddaki bir yoruma göre önceden verilmiş gerçek bir kullanıcı
  talebini tersine çeviren bir işlem — üç ayrı otomatik inceleme bunu
  "insan kararı gerekiyor" diye işaretlemişken, dördüncü ve beşinci
  incelemeler onay almadan uygulamış. Bu tutarsızlığı ve #15'in bekleyen
  durumunu kullanıcıya bildirdim (push notification) — merge kararı
  kullanıcıda.

## Sonuç
Bugün için ek bir kod değişikliği yapılmadı (gerekli düzeltme zaten #15'te
mevcut). Tek açık madde: **#15'in merge edilip edilmeyeceği** — kullanıcı
kararı bekleniyor.
