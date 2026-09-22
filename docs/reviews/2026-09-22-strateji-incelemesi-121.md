# 121. Tur Strateji İncelemesi — 2026-09-22

## Kapsam
Planlı ("her gün stratejini gözden geçir, sermayenin %10'u kadar kazanma
hedefi için gereken kararları al") görevin bu turdaki çalıştırması.
Tetiklenme: ~05:03 UTC. Bir önceki round'un (120, `9f999ff` → PR #220
`35c511a`) merge'i 03:07:11 UTC — aradan bu kez **~1 saat 56 dakika**
(önceki birkaç round'daki ~44-62 dakikalık aralıktan daha uzun, ama
yine de "günlük" değil).

## Durum tespiti
- `git fetch origin main` → HEAD zaten `35c511a` (PR #220 ile merge
  edilmiş 120. tur) ile senkron, açık PR yok.
- Kod tabanı 120. turdan bu yana **hiç değişmemiş**
  (`git log origin/main..HEAD` → 0 commit).
- Tam test paketi bağımsız olarak yeniden çalıştırıldı (`python -m
  pytest`, `pytest.ini`'deki tüm `testpaths`): **1790 passed, 4
  skipped** — regresyon yok, önceki turlarla birebir aynı sayı.
- Bu sandbox'ta hâlâ canlı bot örneği yok: `data/status.json`,
  `data/control.json`, `data/positions.json` mevcut değil → %10 sermaye
  hedefine karşı bu turdan da ölçülebilir ilerleme sağlanamıyor.

## Bu turda yeni bulgu
Yok. Kod 120. turdan beri bayt bayt aynı; iki bilinen bulgu kaynak
koddan tekrar doğrulandı, ikisi de değişmemiş:

1. **Onay kuyruğu / doğrudan emir yolu çelişkisi** (104. turdan beri
   açık, 113. turda kullanıcıya bildirildi): `agents/orchestrator.py:42`
   hâlâ `_enqueue_order`'ı import edip hiç çağırmıyor; satır 1119 ve
   1406'da sinyaller `is_approved=True` sabitiyle doğrudan
   `client.place_order()`'a gidiyor. Sermaye/güvenlik etkisi nedeniyle
   bu tur da tek taraflı kod değişikliği yapılmadı.
2. **Zamanlama sıklığı** (106. turdan beri açık, 113. turda kullanıcıya
   bildirildi): Bu tur ~2 saat sonra tetiklendi — "günlük" olması
   gereken görev hâlâ saatlik mertebede çalışıyor, hesap seviyesi
   rutin ayarı bu oturumdan değiştirilemiyor.

## Bildirim kararı
Her iki bulgu da 113. turda gerçek bir push bildirimiyle kullanıcıya
zaten iletildi ve o zamandan beri (114-120. turlar) tekrar tekrar aynı
şekilde doğrulandı, hiçbiri değişmedi. Bu turda da ne yeni bir canlı bug
ne de eldeki iki konuda kullanıcının karar vermesini gerektirecek yeni
bir gelişme var — sadece tetiklenme aralığı bu kez biraz daha uzundu
(~2 saat), ki bu zaten bilinen "günlük değil, düzensiz saatlik" sorununun
bir başka veri noktası, yeni bir gerçek değil. Bu nedenle bu round için
yeni bir push bildirimi gönderilmedi.

## Sonuç
Kod tabanı sağlıklı (1790/1790, regresyon yok), yeni bir live bug yok.
Asıl aksiyon hâlâ kullanıcıda: (a) onay kuyruğu/doğrudan emir
çelişkisinin hangi yönde çözüleceği, (b) "Strateji Rutin"
tetikleyicisinin günlük aralığa çekilmesi, (c) bu sandbox'a canlı
pozisyon verisi bağlanıp bağlanmayacağı.
