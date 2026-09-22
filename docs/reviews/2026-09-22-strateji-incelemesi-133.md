# 133. Tur Strateji İncelemesi — 2026-09-22

## Kapsam
Planlı ("her gün stratejini gözden geçir, sermayenin %10'u kadar kazanma
hedefi için gereken kararları al") görevin bu turdaki çalıştırması.
Tetiklenme: ~17:07 UTC.

## Durum tespiti
- Bu tur başladığında `main` üzerinde açık bir PR vardı: #233 ("132. tur"),
  16:08:33 UTC'de başka bir oturum tarafından oluşturulmuş,
  `mergeable_state: clean`. İçeriği bağımsız doğrulandı ve merge edildi
  (`61fd329`):
  - Tek değişiklik `docs/reviews/2026-09-22-strateji-incelemesi-132.md`
    dosyasının eklenmesiydi — kod değişikliği yok.
  - PR gövdesi doğrudan okundu: 132. tur, biriken iki bulgunun (onay
    kuyruğu çelişkisi 29 tur, saatlik tetikleme 12 tur) yanı sıra üçüncü
    bir tespiti de içeriyordu — bu sandbox'ın canlı bot durumuna (sermaye,
    pozisyon, P&L) hiçbir zaman erişimi olmadığı için rutinin asıl
    hedefine ("%10 sermaye kazancı") karşı ölçülebilir ilerleme rapor
    edemeyeceği. Bu üç madde nedeniyle 114. turdan beri süren "yeni bilgi
    yoksa bildirme" politikasından bilinçli sapılıp kullanıcıya bildirim
    gönderildiği PR gövdesinde belirtilmiş.
  - Local branch `git fetch` + `git merge --ff-only origin/main` ile
    güncellendi — kayıp iş yoktu.
- Kadans: 132. tur PR'ı 16:08:33 UTC oluşturuldu, bu tur ~17:07 UTC
  başladı — fark ~59 dakika. "Günlük yerine saatlik tetikleniyor" bulgusu
  bu turda da (**13. kez art arda** ~1 saatlik aralıkla) doğrulandı.
  `CronList` bu oturumda da boş çıktı verdi ("No scheduled jobs") —
  tetikleyici bu oturumun erişemediği hesap seviyesinde bir mekanizma,
  önceki 12 turun tespitiyle tutarlı.

## Bug taraması — bilinen bulgular (bağımsız yeniden doğrulandı, değişmedi)
1. **Onay kuyruğu / doğrudan emir yolu çelişkisi** (104. turdan beri açık,
   **şimdi 30. turdur açık**) — kaynak kod bu turda doğrudan (subagent
   raporuna güvenmeden) okundu:
   - `agents/orchestrator.py:42` → `core.approval_queue.enqueue`'ı
     `_enqueue_order` adıyla import ediyor; `grep -c '_enqueue_order('
     agents/orchestrator.py` → **0** çağrı — dosyada hiç kullanılmıyor.
   - Satır 1119: doğrudan/otonom emir yolunda `is_approved=True` sabit
     geçiliyor (queue'dan geçmeden).
   - Satır 1406: `_execute_approved_orders()` içinde, `_get_approved_orders()`
     (dashboard onay listesi) üzerinden gelen emirler için `is_approved=True`
     — bu ikinci call site yapısal olarak tutarlı (zaten onaylanmış liste),
     ama hiçbir emir `_enqueue_order()` çağrılmadığı için o listeye hiç
     düşmüyor; yani mekanizma canlıda fiilen ölü kod.
   - `control_plane/approval_queue.py` docstring'i: *"INC-2026-03-15-001
     dersi: Sinyal → emir arasında insan onayı ZORUNLU."* — dokümante
     edilmiş bir güvenlik dersi. Sermaye/güvenlik etkisi olan bu
     çelişkiyi bu tur da tek taraflı değiştirmedi; 132. turda zaten
     kullanıcıya iletildi, karar bekleniyor.
2. **Zamanlama sıklığı** (106. turdan beri açık, **13. kez art arda**
   doğrulandı) — hesap seviyesinde, bu oturumdan değiştirilemiyor.

Bu turda kod tabanında yeni bir bulgu yok.

## Testler
Tam test paketi kökten çalıştırıldı (`python3 -m pip install -r
requirements.txt` sonrası `python3 -m pytest`): **1790 passed, 4
skipped** — regresyon yok, önceki turlarla birebir aynı sonuç.

## Bildirim kararı
114. turdan beri uygulanan "yeni bilgi yoksa bildirme" politikası bu
turda uygulandı: 132. tur bir tur önce (≈1 saat önce) aynı iki bulgu
(onay kuyruğu, zamanlama) + üçüncü meta-tespit (sandbox'ın canlı duruma
asla erişemeyeceği) için zaten kullanıcıya bildirim gönderdi. Bu turda
kod tabanında veya bulgularda hiçbir değişiklik yok; aynı bilgiyi tekrar
bildirim olarak göndermek gürültü olur. Ayrı bir push bildirimi
gönderilmedi.

## Sonuç
Kod tabanı sağlıklı (1790/1790, regresyon yok). Açık aksiyon kalemleri
değişmeden duruyor ve kullanıcı kararını bekliyor:
1. Onay kuyruğu güvenlik açığı — hangi yönde çözülecek (bypass'ı koru vs.
   queue'ya bağla) kullanıcı kararı gerektiriyor, 132. turda iletildi.
2. Rutin tetikleyicisinin hesap seviyesinde günlük aralığa çekilmesi —
   bu oturumdan yapılamıyor, kullanıcının hesap/zamanlayıcı ayarından
   düzeltmesi gerekiyor.
