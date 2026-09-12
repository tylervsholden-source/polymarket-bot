# Günlük Strateji İncelemesi — 2026-09-12 (3. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç.

## Durum özeti
- Bu checkout'ta yine `data/control.json`, `data/positions.json` veya `.env` yok
  (ikisi de gitignore'da, runtime'da üretiliyor) → bugün kapatılacak/açılacak
  gerçek bir pozisyon yok, acil bir işlem kararı gerekmedi.
- `origin/main` ile bu branch tam senkron (`ff5649a`), açık PR yok, çalışma
  ağacı temiz. `pytest tests/` → **574 passed, 2 skipped**.

## Düzeltme: bugünkü önceki iki incelemenin FIX_CONFLICT_REPORT.md hakkındaki
tespiti güncel değildi

Önceki iki inceleme (`-incelemesi.md`, `-incelemesi-2.md`) "3 BLOCKER hâlâ
canlı kod yolunda duruyor" dedi ve `FIX_CONFLICT_REPORT.md` (2026-03-22)
satır numaralarına atıfta bulundu. Kodu satır satır kontrol ettim — o rapordan
bu yana yapılan (tarihsiz ama koddaki yorumlarla doğrulanan) refactor'lar
üçünü de zaten çözmüş:

1. **Triple NO block** — `strategies/arbitrage_engine.py:1521`: `"REGIME_STR_CAP
   + REGIME_DECAY kaldırıldı — sinyal neyse o"`. Engine'in 0.75 hard-cap'i
   tamamen kaldırılmış. Orchestrator'daki FIX-E (`edge < 0.15` NO block) artık
   `agents/orchestrator.py`'de yok. Kalan tek katman: `coordinator.py:328-350`
   FIX-B (reviewer API fail → NO blok + YES edge<0.08), ve eşiği zaten
   engine'in 0.08 taban edge'i ile hizalı — üçlü çakışma yok, redundant kat
   kalmamış.
2. **15m double-dampening** — `arbitrage_engine.py:1155-1158`: yorum "FIX-2c:
   REMOVED 15M_PATTERN_DAMPEN — Bayesian already dampens 15m (0.90/0.75).
   Double-dampening made 15m signals too pessimistic" — pattern dampening
   kaldırılmış, sadece Bayesian dampening kalmış (if/elif yapısı 15m ve
   diğer timeframe'ler için ayrı, kasıtlı iki farklı katsayı — bug değil).
3. **Kelly compounding → $0.01 bet** — `kelly_criterion.py`'de regime
   azaltması artık max %10 (eskiden %50), streak floor 0.70'te sabit; ayrıca
   `agents/orchestrator.py:685` `_effective_min = max(1.0, min(...))` ile
   $1 altına asla inmiyor. Rapordaki $1.75 senaryosu artık mümkün değil.

**Sonuç: FIX_CONFLICT_REPORT.md'deki 3 BLOCKER için bugün kod değişikliği
gerekmedi — zaten çözülmüşler.** Rapor artık tarihi bir belge, canlı koda
karşı yeniden doğrulanmadan referans alınmamalı.

## Düzeltme: "readiness-gate sistemi orchestrator'a hiç bağlı değil" iddiası da yanlıştı

İlk incelemedeki bu iddiayı da kontrol ettim: `agents/orchestrator.py`
`control_plane.live_gate.check_live_gate()`'i iki yerde (satır 774, 999)
gerçekten çağırıyor. Bu fonksiyon 11 kontrolün hepsini uyguluyor (process
lock, live_trading, readiness_verdict tazeliği + `TINY_PILOT_CANDIDATE`,
daily_stop, position_count, rate_limit, reentry, expiry, entry_window,
approval, capital) — tek bir fail = emir tamamen engellenir. `shadow_runner`
modülü de orchestrator'a import edilmiş (journal/types). Doğru olan kısım:
`calibration/calibrator.py` doğrudan orchestrator'dan import edilmiyor —
ama bu kasıtlı bir ayrım, çünkü calibration offline bir araç;
`data/readiness_verdict.json` dosyasını üretiyor, orchestrator da o dosyayı
okuyup gate'i uyguluyor. Kırık bir bağlantı değil, dosya-tabanlı bir
sınır — canlıda bu dosya yoksa/eskiyse (>26 saat) gate zaten tüm emirleri
engelliyor.

## HALA ÇÖZÜLMEDİ — kullanıcı kararı gerekiyor (2. kez bildiriliyor)

`check_live_gate`'in 11 kontrolünden biri (**#4 daily_stop**) canlı yolda
etkisiz: `agents/orchestrator.py:778, 970, 976`'da
`daily_loss_exceeded=False` / `daily_stop = False` sabit kodlanmış, yorum:
`# devre dışı — kullanıcı talebi (2026-03-21)`. Bu CLAUDE.md'nin "Temel
Kurallar (Değiştirme)" bölümündeki tek maddeyle doğrudan çelişiyor
("Günlük stop-loss: -%15 → bot o gün durur"), ama koddaki tarihli yorum
gerçek bir kullanıcı talebini işaret ediyor. Bu yüzden bugün de tek taraflı
değiştirilmedi (ne geri açıldı ne de dokunulmadı bırakıldı — sadece
doğrulandı ve tekrar bayraklandı).

Diğer 10 gate maddesi (readiness_verdict dahil) sağlam duruyor, yani şu an
canlıya alınsa bile readiness_verdict.json tazelenmeden hiçbir emir
geçmeyecek. Ama stop-loss'un kendisi, o dosya var olsa bile, ruin'e karşı
hiçbir koruma sağlamıyor.

**Karar gerekiyor:** Günlük -%15 stop-loss yeniden açılsın mı (ve hangi
eşikte), yoksa mevcut devre dışı durum bilinçli olarak korunsun mu?
Bu olmadan hedefe (sermayenin %10'u) güvenle ilerlenemez — koruma
mekanizması eksik durumda.

## Bugün yapılan
- Kod tabanı, testler (574 passed / 2 skipped), git senkronu doğrulandı.
- Yukarıdaki iki düzeltme dışında davranış değişikliği yapılmadı (yapılacak
  bir şey yoktu — tespit edilen "sorunlar" zaten çözülmüştü).
- Bu doküman commit edilip pushlandı.
