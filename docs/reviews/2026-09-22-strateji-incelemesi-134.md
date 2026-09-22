# 134. Tur Strateji İncelemesi — 2026-09-22

## Kapsam
Planlı ("her gün stratejini gözden geçir, sermayenin %10'u kadar kazanma
hedefi için gereken kararları al") görevin bu turdaki çalıştırması.
Tetiklenme: ~18:03 UTC.

## Durum tespiti
- Bu tur başladığında `main` üzerinde açık bir PR vardı: #234 ("133. tur"),
  17:08:24 UTC'de başka bir oturum tarafından oluşturulmuş,
  `mergeable_state: clean`. İçeriği (`get_files` ile patch) doğrudan okundu
  ve merge edildi (`80f836e`):
  - Tek değişiklik `docs/reviews/2026-09-22-strateji-incelemesi-133.md`
    dosyasının eklenmesiydi — kod değişikliği yok.
  - PR gövdesi: 133. tur, 132. turda merge edilen ve iki bilinen bulguyu
    (onay kuyruğu çelişkisi, saatlik tetikleme) yeniden doğrulayıp kod
    tabanında yeni bir şey bulmadığını, ve 132. turun bir saat önce zaten
    aynı bulgular için kullanıcıya bildirim gönderdiğini belirterek
    kendisi ayrı bir bildirim göndermediğini raporluyor.
  - Local branch `git fetch` + `git merge --ff-only origin/main` ile
    güncellendi — kayıp iş yoktu.
- Kadans: 133. tur PR'ı 17:08:24 UTC oluşturuldu, bu tur ~18:03 UTC
  başladı — fark ~55 dakika. "Günlük yerine saatlik tetikleniyor" bulgusu
  bu turda da (**14. kez art arda** ~1 saatlik aralıkla) doğrulandı.
  `CronList` bu oturumda da boş çıktı verdi ("No scheduled jobs") —
  tetikleyici bu oturumun erişemediği hesap seviyesinde bir mekanizma.

## Bug taraması — bilinen bulgular (kaynaktan bağımsız yeniden doğrulandı, değişmedi)
1. **Onay kuyruğu / doğrudan emir yolu çelişkisi** (104. turdan beri açık,
   **şimdi 31. turdur açık**) — bu turda da subagent raporuna güvenmeden
   doğrudan kaynak okundu:
   - `agents/orchestrator.py:42` → `enqueue`'ı `_enqueue_order` adıyla
     import ediyor.
   - `grep -n "_enqueue_order(" agents/orchestrator.py` → **sıfır sonuç**
     (satır 1437'deki tek eşleşme bir yorum satırı, çağrı değil) —
     fonksiyon dosyada hiç çağrılmıyor.
   - Satır 1119 (`_process_signal` içindeki otonom/doğrudan emir yolu) ve
     satır 1406 (`_execute_approved_orders()` içindeki ikinci yol) her
     ikisi de `is_approved=True` sabit değerini geçiyor; hiçbiri
     `approval_queue`'dan geçmiyor.
   - `control_plane/approval_queue.py` başlığı: *"INC-2026-03-15-001
     dersi: Sinyal → emir arasında insan onayı ZORUNLU."* — dokümante
     edilmiş güvenlik dersi, canlı kodda fiilen bypass edilmiş durumda.
     132. turda kullanıcıya iletildi, karar bekleniyor; sermaye/güvenlik
     etkisi nedeniyle bu tur da tek taraflı değiştirilmedi.
2. **Zamanlama sıklığı** (106. turdan beri açık, **14. kez art arda**
   doğrulandı, ~14 saatlik sürekli saatlik tetiklenme) — hesap seviyesinde
   bir ayar, bu oturumdan değiştirilemiyor.

Bu turda kod tabanında yeni bir bulgu yok.

## Testler
Tam test paketi kökten çalıştırıldı (`python3 -m pip install -r
requirements.txt` sonrası `python3 -m pytest`): **1790 passed, 4
skipped** — regresyon yok, önceki turlarla birebir aynı sonuç.

## Bildirim kararı
114. turdan beri uygulanan "yeni bilgi yoksa bildirme" politikası bu
turda da uygulandı. 132. tur ~2 saat önce aynı iki bulgu için zaten
kullanıcıya bildirim gönderdi; 133. tur ve bu tur (134.) kod tabanında
veya bulgularda hiçbir değişiklik bulmadı. Aynı bilgiyi tekrar tekrar
bildirim olarak göndermek gürültü olur ve kullanıcının "önemli olanı
öğrenmek için bildirim" beklentisini zedeler. Ayrı bir push bildirimi
gönderilmedi.

## Sonuç
Kod tabanı sağlıklı (1790/1790, regresyon yok). Açık aksiyon kalemleri
değişmeden duruyor ve kullanıcı kararını bekliyor:
1. Onay kuyruğu güvenlik açığı — `agents/orchestrator.py` içinde
   `_enqueue_order()` hiç çağrılmıyor, otonom emirler onay adımını
   tamamen atlıyor. Kullanıcının hangi yönde çözüleceğine (bypass'ı
   bilinçli olarak koru vs. queue'ya gerçekten bağla) karar vermesi
   gerekiyor.
2. Rutin tetikleyicisinin hesap seviyesinde günlük aralığa çekilmesi —
   bu oturumdan yapılamıyor; kullanıcının hesap/zamanlayıcı ayarından
   düzeltmesi gerekiyor. 14 saattir saatlik tetikleniyor.
