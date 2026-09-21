# 113. Tur Strateji İncelemesi — 2026-09-21

## Kapsam
Planlı ("her gün stratejini gözden geçir, sermayenin %10'u kadar kazanma
hedefi için gereken kararları al") görevin bu turdaki çalıştırması.
Tetiklenme: 14:13:13 UTC. Bir önceki round'un (112, `01f44ef`) push'u
13:29:25 UTC — aradan yine **~44 dakika**.

## Durum tespiti
- `git fetch origin main` → HEAD zaten `3311925` (PR #211 ile merge edilmiş
  112. tur) ile senkron, açık PR yok.
- Kod tabanı 112. turdan bu yana **hiç değişmemiş** (`git log
  origin/main..HEAD` → 0 commit).
- Tam test paketi bağımsız olarak yeniden çalıştırıldı: **1790 passed, 4
  skipped** — regresyon yok. 112. turun `_isolate_status_writer_file`
  fix'i doğrulandı: test sonrası `data/status.json` **oluşmadı** (önceki
  turda bulunan sızıntı düzeltilmiş durumda kaldı).
- Bu sandbox'ta hâlâ canlı bot örneği yok: `data/status.json`,
  `data/control.json`, `data/positions.json` mevcut değil → %10 sermaye
  hedefine karşı bu turdan da ölçülebilir ilerleme sağlanamıyor.

## Bu turda yeni bulgu
Yok. Kod 112. turdan beri bayt bayt aynı; iki bilinen bulgu kaynak koddan
tekrar doğrulandı, ikisi de değişmemiş:

1. **Onay kuyruğu / doğrudan emir yolu çelişkisi** (104. turdan beri açık):
   `agents/orchestrator.py:42` hâlâ `_enqueue_order`'ı import edip hiç
   çağırmıyor; satır 1119 ve 1406'da sinyaller `is_approved=True`
   sabitiyle doğrudan `client.place_order()`'a gidiyor —
   `docs/APPROVAL_WORKFLOW_SPEC.md`'nin "doğrudan emir yolu kapalı"
   ifadesiyle çelişiyor. Sermaye/güvenlik etkisi nedeniyle bu tur da
   tek taraflı kod değişikliği yapılmadı.
2. **Zamanlama sıklığı** (106. turdan beri açık): Bu tur da öncekinden
   ~44 dakika sonra tetiklendi — "günlük" olması gereken görev art arda
   saatlik aralıklarla çalışıyor, hesap seviyesi rutin ayarı bu oturumdan
   değiştirilemiyor.

## Bildirim kararı
106-112. turlarda bu iki konu tekrar tekrar kod/commit seviyesinde
belgelendi ama hiçbiri doğrulanabilir şekilde kullanıcıya gerçek bir
bildirim kanalıyla ulaştığı teyit edilmedi (108. tur bunu açıkça
"kullanıcıya hiç ulaşmadı" olarak işaretlemişti). Kanıt artık yeterince
birikti — 8+ turdur aynı iki konu, hepsi kod incelemesiyle sınırlı kaldı.
Bu round bu ikisini gerçek bir push bildirimiyle kullanıcıya iletiyor:
(a) sandbox'ta canlı bot bağlantısı olmadığı için %10 hedefine karşı bu
görevin ölçülebilir ilerleme sağlayamadığı, (b) onay kuyruğu/doğrudan
emir çelişkisinin kullanıcı kararı gerektirdiği, (c) rutinin günlük değil
saatlik tetiklendiği.

## Sonuç
Kod tabanı sağlıklı (1790/1790, regresyon yok), yeni bir live bug yok.
Asıl aksiyon kullanıcıda: (a) onay kuyruğu/doğrudan emir çelişkisinin
hangi yönde çözüleceği, (b) "Strateji Rutin" tetikleyicisinin günlük
aralığa çekilmesi, (c) bu sandbox'a canlı pozisyon verisi bağlanıp
bağlanmayacağı.
