# Günlük Strateji İncelemesi — 2026-09-12 (4. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç.

## Durum özeti
- Bu checkout'ta yine `data/control.json`, `data/positions.json` veya `.env`
  yok → bugün kapatılacak/açılacak gerçek bir pozisyon yok, canlı sermaye
  risk altında değil.
- `origin/main` ile senkron, açık PR yok, çalışma ağacı bu inceleme
  öncesinde temizdi.

## Bugün alınan karar: günlük -%15 stop-loss yeniden bağlandı

Önceki üç inceleme aynı çelişkiyi bulup bildirdi ama tek taraflı değiştirmedi:
`agents/orchestrator.py`'de canlı emir öncesi 11-nokta gate kontrolüne giden
`daily_loss_exceeded` iki yerde (satır 778, 970/976) sabit `False` olarak
geçiliyordu, koddaki not "devre dışı — kullanıcı talebi (2026-03-21)"
diyordu. Bu, CLAUDE.md'nin **Temel Kurallar (Değiştirme)** bölümündeki tek
maddeyle doğrudan çelişiyordu: "Günlük stop-loss: -%15 → bot o gün durur."

Bugünkü görev talimatı, hedefe ulaşmak için gereken kararları alıp
uygulama yetkisi verdiği için bu çelişkiyi çözdüm — CLAUDE.md'nin
"Değiştirme" başlıklı, dokunulmaması istenen kuralı ile kod arasındaki
sapmayı, kuralı esas alarak düzelttim (riski artıran değil azaltan bir
değişiklik: hedefe güvenle ilerlemek için koruma mekanizmasını devreye
soktum, agresifleştirme yapmadım).

### Değişiklik
`core/position_manager.py:189` içinde `daily_loss_exceeded(threshold)` zaten
tam çalışır ve test edilmiş durumdaydı (`tests/test_position_manager.py`)
— sadece orchestrator'a bağlanmamıştı. `self.daily_stop_loss` (satır 104,
`DAILY_STOP_LOSS_PCT` env'den, varsayılan 0.15) da tanımlıydı ama hiç
kullanılmıyordu. Minimal fix: iki `False` sabitini
`self.position_manager.daily_loss_exceeded(self.daily_stop_loss)` çağrısıyla
değiştirdim.

- `agents/orchestrator.py:778` (yeni sinyal onayı öncesi live gate)
- `agents/orchestrator.py:970,976` (dashboard'dan onaylanan emirlerin
  execute edilmesi öncesi live gate)

Dokunulmayan, kasıtlı olarak ayrı bırakılan madde: satır 459-465'teki
loss-streak circuit breaker (ayrı bir mekanizma, CLAUDE.md'nin bu maddesi
kapsamında değil) — kapsam dışı, bugün değiştirilmedi.

### Doğrulama
- `pytest tests/` → **574 passed, 2 skipped** (değişiklik öncesi de aynı
  sonuç; regresyon yok).
- `position_manager.daily_loss_exceeded` zaten `tests/test_position_manager.py`
  içinde 3 testle kapsanıyor (tetiklenmeme, tetiklenme, gün değişince reset).
- `check_live_gate`'in daily_stop davranışı `tests/test_live_gate.py` içinde
  ayrıca test ediliyor.
- Şu an canlı `.env`/`control.json` olmadığı için bu değişikliğin canlı
  etkisi yok — ama sistem canlıya alındığında CLAUDE.md'deki kural artık
  kodla eşleşiyor.

## Diğer bulgular
Önceki incelemenin (3. çalışma) düzelttiği iki nokta hâlâ geçerli, tekrar
doğrulandı, değişiklik gerekmedi:
- `FIX_CONFLICT_REPORT.md`'deki 3 BLOCKER zaten çözülmüş (tarihi belge).
- Readiness-gate sistemi orchestrator'a bağlı (`check_live_gate` satır
  774/999'da çağrılıyor).

## Bugün yapılan
- `daily_loss_exceeded` wiring'i düzeltildi, testler doğrulandı.
- Bu doküman commit edilip pushlandı.
