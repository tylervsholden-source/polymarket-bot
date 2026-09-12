# Günlük Strateji İncelemesi — 2026-09-12

## Hedef
Mevcut sermayenin %10'u kadar kazanç.

## Bulgular

### 1. Canlı işlem şu an aktif değil, sermaye riski yok
- Bu checkout'ta `data/control.json`, `data/positions.json` veya `.env` yok.
- `.env.example:14` → `LIVE_TRADING_ENABLED=false`.
- `control_plane/live_gate.py` canlıya geçiş için 11 kontrol + `data/readiness_verdict.json` istiyor; hiçbiri mevcut değil.
- `data/bot.lock` içindeki PID'e karşılık gelen çalışan bir süreç yok; en son log kayıtları (`data/bot_log.txt`, `data/dashboard_log.txt`) Mart 2026 tarihli — ~6 ay eski, güncel değil.
- Sonuç: bugün gerçek para ile alınacak/kapatılacak bir pozisyon yok, bu yüzden bugünkü inceleme kapsamında işlem kararı uygulanmadı.

### 2. Kritik mimari sorun: dokümante edilen güvenlik katmanı canlı kod yolunda değil
- `main.py` → `agents/orchestrator.py`'yi çalıştırıyor; bu da `strategies/arbitrage_engine.py`'yi kullanıyor.
- `LIVE_PILOT_READINESS.md` ve `PAPER_MODE_POLICY.md`'nin tarif ettiği `calibration/`, `signal_bridge/`, `shadow_runner/` readiness-gate sistemi `agents/orchestrator.py` tarafından **hiç import edilmiyor**.
- Yani dokümanlarda "canlıya geçmeden önce zorunlu" diye tarif edilen doğrulama katmanı, gerçekte çalışan koddan kopuk.

### 3. Çözülmemiş BLOCKER seviyeli hatalar (FIX_CONFLICT_REPORT.md, 2026-03-22)
Hâlâ canlı kod yolunda (`agents/orchestrator.py` + `strategies/arbitrage_engine.py`) duran, düzeltilmemiş 3 BLOCKER + 4 önemli sorun:
1. NO sinyali için engine/coordinator/orchestrator katmanlarında üçlü tekrarlı veto.
2. 15 dakikalık zaman diliminde "double-dampening" — geçerli NO sinyallerini sessizce eliyor.
3. Sentetik spread kaynaklı edge şişmesi.
4. Kelly streak-çarpanı bazı durumlarda $0.01'lik anlamsız minimum bahislere yol açıyor.
5. Loss-cooldown gate sırası, yüksek edge'li sinyalleri bile eziyor.
6. Reviewer API hata eşiği tutarsızlığı.

### 4. Küçük tutarsızlık (davranışı etkilemiyor, ama kafa karıştırıcı)
- `agents/orchestrator.py:102` → `self.min_edge` env'den 0.08 varsayılanla okunuyor ama dosyanın geri kalanında hiç kullanılmıyor (ölü kod).
- Gerçekte kullanılan eşik `strategies/arbitrage_engine.py:202-204` → YES için 0.12, NO için 0.18.
- Davranışı bozmadığı için bugün dokunulmadı; ayrı bir temizlik olarak ele alınabilir.

## Bugün neden kod değişikliği yapılmadı
CLAUDE.md kuralı: "Karmaşık görevler (3+ adım): başlamadan önce planı yaz ve onayla." Yukarıdaki BLOCKER'ları düzeltmek çok-dosyalı, gerçek para riski taşıyan bir değişiklik olduğundan, onay alınmadan tek seferde uygulanmadı. Ayrıca canlıda bugün açık pozisyon/sermaye olmadığı için acil bir işlem kararı gerekmiyordu.

## Öneri — sıradaki adım
1. Hangi strateji yığınının (agents/orchestrator.py+arbitrage_engine.py mi, yoksa crypto_directional/+signal_bridge/+calibration mı) esas alınacağına karar verilmeli.
2. Karar sonrası, FIX_CONFLICT_REPORT.md'deki 3 BLOCKER onaylı bir plan dahilinde düzeltilmeli.
3. `LIVE_TRADING_ENABLED=true` yapılmadan önce readiness-gate sisteminin gerçekten çağrıldığı doğrulanmalı.
