# 111. Tur Strateji İncelemesi — 2026-09-21

## Kapsam
Planlı ("her gün stratejini gözden geçir, sermayenin %10'u kadar kazanma
hedefi için gereken kararları al") görevin bu turdaki çalıştırması.
Tetiklenme: 12:22:56 UTC. Bir önceki round'un (110, konsolidasyon dahil)
son merge'i 11:22:15 UTC (`7340a31`) — aradan **~1 saat** geçmiş.

## Durum tespiti
- `git fetch origin main` → `HEAD` zaten `7340a31` ile senkron, açık PR yok
  (yalnızca farklı bir otomasyonun ürettiği docs-only konsolidasyon PR'ı
  #209 açık, bu round'un kapsamı dışında).
- Kod tabanı 110. turdan bu yana **hiç değişmemiş** (`git log
  origin/main..HEAD` → 0 commit).
- Tam test paketi (`pytest.ini`'deki 5 testpath) bağımsız olarak yeniden
  çalıştırıldı: **1790 passed, 4 skipped** — regresyon yok. Test sonrası
  `git status --short` boş (state izolasyonu hâlâ çalışıyor).
- Bu sandbox'ta hâlâ canlı bot örneği yok: `data/status.json`,
  `data/control.json`, `data/positions.json` mevcut değil → %10 sermaye
  hedefine karşı bu turdan da doğrudan ölçülebilir ilerleme sağlanamıyor.
  Repodaki tek performans kanıtı durgun 2026-03 verileri (`data/3day_eval.txt`:
  44 trade, net +$1.01, YES tarafı -$14.39, NO tarafı +$15.40).

## Bug taraması
Kod 110. turdan bu yana bayt bayt aynı olduğu için tam yeniden tarama
yapılmadı (aynı taban üzerinde 100+ tur zaten tarandı); bunun yerine iki
açık bulgu kaynak koddan yeniden doğrulandı:

1. **Onay kuyruğu / doğrudan emir yolu çelişkisi** (104. turdan beri açık,
   değişmedi): `agents/orchestrator.py:42` `core.approval_queue.enqueue`'u
   `_enqueue_order` adıyla import ediyor ama `grep -n enqueue
   agents/orchestrator.py` dosyada başka **hiçbir çağrı** göstermiyor —
   fonksiyon import edilip hiç kullanılmıyor. Sinyaller hâlâ satır
   1119/1138 ve 1406/1422'de `is_approved=True` sabitiyle doğrudan
   `client.place_order()`'a gidiyor; kodun kendi yorumu bunu "DOĞRUDAN
   EMİR VER (onay kuyruğu bypass)" olarak açıkça işaretliyor (satır
   ~1133). `docs/APPROVAL_WORKFLOW_SPEC.md` ise hâlâ "Dogrudan emir verme
   yolu kapatilmistir" diyor — kod ile doküman doğrudan çelişiyor. Bu,
   kasıtlı bir mimari tercih gibi görünüyor (kod kendi bypass'ını
   belgeliyor) ama hangi tarafın (kod mu, doküman mı) doğru davranışı
   yansıttığı hâlâ kullanıcı kararı gerektiriyor; sermaye/güvenlik etkisi
   olduğu için bu tur da tek taraflı değişiklik yapılmadı.
2. **Zamanlama sıklığı** (106. turdan beri açık, kötüleşerek devam ediyor):
   Bu turun kendisi buna yeni bir kanıt — "günlük" tanımlanan görev
   ~1 saat arayla tekrar tetiklendi. `CronList` ile bu oturumun kendi
   zamanlanmış işleri kontrol edildi (boş) — "Strateji Rutin" tetikleyicisi
   (`trig_018B5PhS4xUw24eBYCFrUW6X`) oturum-içi `CronCreate` mekanizmasına
   değil, hesap seviyesi bir rutine bağlı; bu oturumun araçlarıyla
   görülemiyor veya değiştirilemiyor. Düzeltme yalnızca kullanıcının
   rutin ayarларından mümkün.

## Bu turda yeni bulgu
Yok. Test paketi yeşil, kod değişmemiş, iki bilinen bulgu aynı durumda.

## Bildirim kararı
106-110. turlarda bu iki konu zaten (iddiaya göre) kullanıcıya bildirildi.
Bu turda ikisi için de yeni bilgi yok (aynı kod, aynı durum) — tekrarlayan
bir bildirim göndermek yerine bu not bırakılıyor. Zamanlama sıklığı sorunu
somutlaşmaya devam ediyor (art arda ~1 saatlik tetiklenmeler) ve kullanıcı
henüz yanıt vermediyse birikmiş oturum/PR maliyeti artıyor; kullanıcı bir
sonraki gerçek etkileşiminde bu iki maddeyi hatırlatmakta fayda var.

## Sonuç
Kod tabanı sağlıklı (1790/1790, regresyon yok), yeni bir canlı bug veya
doküman sapması bulunmadı. Asıl aksiyon hâlâ kullanıcıda: (a) onay
kuyruğu/doğrudan emir çelişkisinin hangi yönde çözüleceği, (b) "Strateji
Rutin" tetikleyicisinin günlük aralığa çekilmesi.
