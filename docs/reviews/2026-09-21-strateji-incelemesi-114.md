# 114. Tur Strateji İncelemesi — 2026-09-21

## Kapsam
Planlı ("her gün stratejini gözden geçir, sermayenin %10'u kadar kazanma
hedefi için gereken kararları al") görevin bu turdaki çalıştırması.
Tetiklenme: ~16:04 UTC. Bir önceki round'un (113, `22637fe`) push'u
14:16:09 UTC — aradan yine **~1 saat 48 dakika** (günlük değil).

## Durum tespiti
- `git fetch origin main` → HEAD zaten `21ec9bb` ile senkron (113. tur
  merge edilmiş), açık PR yok, `git log origin/main..HEAD` → 0 commit.
- Kod tabanı 113. turdan bu yana **hiç değişmemiş**.
- Tam test paketi (`pytest` — `tests/`, `calibration/tests`,
  `execution_realism/tests`, `crypto_directional/tests`) yeniden
  çalıştırıldı: **1790 passed, 4 skipped, 1 warning** — 113. tur ile
  birebir aynı sonuç, regresyon yok.
- Bu sandbox'ta hâlâ canlı bot örneği yok: `data/status.json`,
  `data/control.json`, `data/positions.json` mevcut değil → %10 sermaye
  hedefine karşı bu turdan da ölçülebilir ilerleme sağlanamıyor (bu
  ortamda gerçek pozisyon/sermaye verisi yok, sadece kod incelemesi
  yapılabiliyor).

## Bu turda yeni bulgu
Yok. Kaynak kod 113. turdan beri bayt bayt aynı; iki bilinen bulgu
tekrar doğrulandı, ikisi de değişmemiş:

1. **Onay kuyruğu / doğrudan emir yolu çelişkisi** (104. turdan beri
   açık): `agents/orchestrator.py:42` hâlâ `_enqueue_order`'ı import
   edip hiç çağırmıyor; satır 1119 ve 1406'da sinyaller `is_approved=True`
   sabitiyle `## ── DOĞRUDAN EMİR VER (onay kuyruğu bypass) ──` yorumunun
   altında doğrudan `client.place_order()`'a gidiyor —
   `docs/APPROVAL_WORKFLOW_SPEC.md`'nin "Dogrudan emir verme yolu
   kapatilmistir" ifadesiyle hâlâ çelişiyor. Bu sermaye/güvenlik riskini
   taşıyan bir tasarım kararı (zorunlu onay kuyruğu mu, kasıtlı doğrudan
   yürütme mi) — geçmiş turlarda tutarlı biçimde kullanıcı onayı
   gerektirdiği için tek taraflı kod değişikliği yine yapılmadı.
2. **Zamanlama sıklığı** (106. turdan beri açık): Bu tur da öncekinden
   ~1sa48dk sonra tetiklendi — hesap seviyesi rutin ayarı bu oturumdan
   değiştirilemiyor.

## Bildirim kararı
113. tur bu iki bulguyu az önce (14:16 UTC, ~1sa48dk önce) gerçek bir
push bildirimiyle kullanıcıya iletti. Bu turda durum bilgisayarda
hiçbir şey değişmedi (kod aynı, testler aynı, iki bulgu aynı) — aynı
mesajı tekrar göndermek gürültü olur. Bu round için push bildirimi
gönderilmedi; sonraki bir round'da kod veya bulgu durumu değişirse
tekrar bildirim değerlendirilecek.

## Sonuç
Kod tabanı sağlıklı (1790/1790, regresyon yok), yeni bir live bug yok.
Açık aksiyon kalemleri değişmedi ve zaten kullanıcıya iletildi: (a)
onay kuyruğu/doğrudan emir çelişkisinin hangi yönde çözüleceği, (b)
"Strateji Rutin" tetikleyicisinin günlük aralığa çekilmesi, (c) bu
sandbox'a canlı pozisyon verisi bağlanıp bağlanmayacağı.
