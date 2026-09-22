# 137. Tur Strateji İncelemesi — 2026-09-22

## Kapsam
Planlı ("her gün stratejini gözden geçir, sermayenin %10'u kadar kazanma
hedefi için gereken kararları al") görevin bu turdaki çalıştırması.
Tetiklenme: ~22:03 UTC.

## Durum tespiti
- Bu tur başladığında açık PR yoktu (`list_pull_requests(state=open)` →
  boş liste). 136. tur PR'ı (#237) önceki turda zaten merge edilmişti;
  local branch `origin/main` ile birebir aynı noktadan (`c688fc8`)
  başladı, ekstra bir fetch/merge adımına gerek kalmadı.
- Kadans: 136. tur ~20:03 UTC tetiklendi, bu tur ~22:03 UTC — fark ~2
  saat. "Günlük yerine saatlik/düzensiz aralıklarla tetikleniyor" bulgusu
  bu turda da (**17. kez art arda**) doğrulandı. `CronList` bu oturumda
  da "No scheduled jobs" döndürdü — tetikleyici bu oturumun erişemediği
  hesap seviyesinde bir mekanizma, önceki turların tespitiyle tutarlı.

## Bug taraması — bilinen bulgular (kaynaktan bağımsız yeniden doğrulandı, değişmedi)
1. **Onay kuyruğu / doğrudan emir yolu çelişkisi** (104. turdan beri açık,
   **şimdi 34. turdur açık**) — subagent raporuna güvenmeden doğrudan
   kaynak okundu (`agents/orchestrator.py`):
   - Satır 41-42: `core.approval_queue.enqueue`'ı `_enqueue_order` adıyla
     import ediyor.
   - `grep -n "_enqueue_order(" agents/orchestrator.py` → **sıfır çağrı**
     — fonksiyon dosyada import edildiği yer dışında hiç kullanılmıyor.
   - Satır 1119 (`_process_signal` içindeki otonom/doğrudan emir yolu) ve
     satır 1406 (`_execute_approved_orders()` içindeki ikinci yol) her
     ikisi de `is_approved=True` sabit değerini geçiyor; hiçbiri
     `approval_queue`'dan geçmiyor.
   - `control_plane/approval_queue.py` başlığı: *"INC-2026-03-15-001
     dersi: Sinyal → emir arasında insan onayı ZORUNLU."* — dokümante
     edilmiş güvenlik dersi, canlı kodda fiilen bypass edilmiş durumda.
     132. turda kullanıcıya iletildi, karar bekleniyor; sermaye/güvenlik
     etkisi nedeniyle bu tur da tek taraflı değiştirilmedi. Bu, strateji
     veya pozisyon boyutlandırma kararından farklı, canlı sermayenin
     güvenlik modelini değiştiren bir mimari karar — kullanıcının genel
     yetkilendirmesi ("gereken tüm kararları alabilirsin") bunu sessizce
     tek taraflı geri almak için yeterli görülmedi.
2. **Zamanlama sıklığı** (106. turdan beri açık, **17. kez art arda**
   doğrulandı) — hesap seviyesinde bir ayar, bu oturumdan değiştirilemiyor.

Bu turda kod tabanında yeni bir bulgu yok.

## Testler
Tam test paketi kökten çalıştırıldı (`pip install -r requirements.txt`
sonrası `python3 -m pytest -q`): **1790 passed, 4 skipped** — regresyon
yok, önceki turlarla birebir aynı sonuç.

## Bildirim kararı
114. turdan beri uygulanan "yeni bilgi yoksa bildirme" politikası bu
turda da uygulandı. 132. tur bu iki bulgu için kullanıcıya zaten bildirim
gönderdi; 133-137. turlar (bu tur dahil) kod tabanında veya bulgularda
hiçbir değişiklik bulmadı. Aynı bilgiyi tekrar tekrar bildirim olarak
göndermek gürültü olur. Ayrı bir push bildirimi gönderilmedi.

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
   düzeltmesi gerekiyor. 17 saattir/turdur kesintisiz saatlik/düzensiz
   tetikleniyor.
