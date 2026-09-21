# 107. Tur Strateji İncelemesi — 2026-09-21

## Kapsam
Planlı ("her gün stratejini gözden geçir, sermayenin %10'u kadar kazanma hedefi
için gereken kararları al") görevin bu turdaki çalıştırması.

## Bu Turda Yapılanlar
- `git fetch origin main` → HEAD zaten `86b8bba` (PR #201 ile senkron), açık PR yok.
- Tam test paketi çalıştırıldı: **1790 passed, 4 skipped** (106. turdaki temel
  ile birebir aynı — regresyon yok).
- Kod tabanında bu tur için yeni bir canlı bug bulunmadı; 102-106. turlarda
  bulunan gerçek hatalar (regime decay guard, spread z-score, SpreadModel,
  OVERPRICED_BLOCK eşiği, regime stability tek-pencere false-GREEN, vb.)
  zaten main'de.

## Kritik Bulgu (106. turdan devam ediyor, henüz çözülmedi)
106. tur bu deponun "günlük" incelemesinin aslında **saatlik, hatta zaman zaman
eş zamanlı çoklu oturum** halinde tetiklendiğini tespit etmişti (2026-09-20'de
~14 tur, tek bir turda 9 paralel oturum çakışması). Bu tur sırasında bunun
hâlâ sürdüğüne dair **doğrudan kanıt** gözlemlendi: test paketi çalışırken
`data/autonomous_state.json` dosyasının `last_update` alanı başka bir oturum
tarafından canlı olarak güncellendi — yani bu incelemeyi yazarken en az bir
başka "günlük inceleme" oturumu da paralel çalışıyordu.

Bunun pratik sonucu:
1. **Bu sandbox'ta canlı/bağlı bir bot örneği yok.** `data/positions.json`,
   `data/control.json` gibi canlı durum dosyaları mevcut değil; yalnızca
   2026-03 tarihli `.bak`/`shadow_journal` gibi durgun veriler var. Yani bu
   inceleme turları kod tabanını düzeltebilir ama **gerçek sermaye üzerinde
   hiçbir işlem yapamaz** — "sermayenin %10'u kadar kazan" hedefine bu
   turlardan hiçbiri doğrudan katkı sağlayamıyor, çünkü kazanılacak/kaybedilecek
   gerçek bir pozisyon yok.
2. Saatlik/eş-zamanlı tetiklenme, aynı dosyalar üzerinde tekrarlanan, çoğunlukla
   "no new bug found" sonucu veren incelemeler üretiyor (114 inceleme dosyası
   birikti) ve gereksiz PR/merge trafiği yaratıyor.

## Öneri
Kullanıcının zamanlanmış görev sıklığını (muhtemelen claude.ai zamanlama
ayarları) "saatlik"ten gerçek "günlük"e çekmesi, ve/veya bu görevin canlı bot
verisine (gerçek `positions.json`/`control.json`) erişimi olan bir ortamda
çalıştırılması gerekiyor — aksi halde bu tur dizisi yalnızca kod kalitesi
denetimi yapabilir, kâr hedefine karşı ölçülebilir ilerleme sağlayamaz.

## Sonuç
Kod tabanı sağlıklı (1790/1790 geçti, açık PR yok). Aksiyon gerektiren asıl
konu teknik değil, operasyonel: zamanlama sıklığı ve canlı veri bağlantısı.
