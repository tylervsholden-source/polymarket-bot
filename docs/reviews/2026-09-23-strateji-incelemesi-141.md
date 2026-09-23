# 141. Tur Strateji İncelemesi — 2026-09-23

## Kapsam
Planlı ("her gün stratejini gözden geçir, sermayenin %10'u kadar kazanma
hedefi için gereken kararları al") görevin bu turdaki çalıştırması.
Tetiklenme: ~02:05 UTC (2026-09-23).

## Durum tespiti
- Bu tur başladığında `main` üzerinde açık bir PR vardı: #241 ("140.
  tur"), 2026-09-23 01:09:37 UTC'de başka bir oturum tarafından
  oluşturulmuş. İçeriği (`get_files` ile patch) doğrudan okundu:
  - Tek değişiklik `docs/reviews/2026-09-23-strateji-incelemesi-140.md`
    dosyasının eklenmesiydi — kod değişikliği yok.
  - PR gövdesi, 139. turun PR'ının (#240) merge edildiğini, iki bilinen
    bulgunun kaynaktan yeniden doğrulandığını ve değişmediğini, satır
    1406'daki `is_approved=True` kullanımının (approved listeden gelen
    emirler için recheck bayrağı) asıl bypass sorunundan (satır 1119)
    ayrı olduğunun netleştirildiğini ve 1790/4 test sonucunun tekrarlandığını
    raporluyor.
  - PR merge edildi (`8a4edc4`). Merge sonrası `list_pull_requests(state=open)`
    boş liste döndürdü — başka açık PR yok.
  - Local branch güncellendi: `git fetch origin main` ardından ayrı bir
    adımda `git merge --ff-only origin/main` — sorunsuz tamamlandı.
- Kadans: 140. tur PR'ı 01:09:37 UTC oluşturuldu, bu tur ~02:05 UTC
  başladı — fark ~56 dakika. "Günlük yerine saatlik/düzensiz aralıklarla
  tetikleniyor" bulgusu bu turda da (**21. kez art arda**) doğrulandı.
  `CronList` bu oturumda da "No scheduled jobs" döndürdü — tetikleyici bu
  oturumun erişemediği hesap seviyesinde bir mekanizma, önceki 20 turun
  tespitiyle tutarlı.

## Bug taraması — bilinen bulgular (kaynaktan bağımsız yeniden doğrulandı, değişmedi)
1. **Onay kuyruğu / doğrudan emir yolu çelişkisi** (104. turdan beri açık,
   **şimdi 38. turdur açık**) — bu turda da subagent raporuna güvenmeden
   doğrudan kaynak okundu (`agents/orchestrator.py`):
   - `grep -n "_enqueue_order(" agents/orchestrator.py` → **sıfır çağrı**
     (satır 42'de yalnızca import; gerçek çağrı hiçbir yerde yok).
   - Satır 1119 (`_process_signal` içindeki `check_live_gate` çağrısı):
     sabit `is_approved=True` geçiliyor — bu asıl bypass noktası.
   - Satır 1137: doğrudan emir veriliyor, kodun kendi yorumu bunu açıkça
     belgeliyor: `# ── DOĞRUDAN EMİR VER (onay kuyruğu bypass) ──`.
   - `control_plane/approval_queue.py` başlığı (satır 1-4) değişmedi:
     *"INC-2026-03-15-001 dersi: Sinyal → emir arasında insan onayı
     ZORUNLU."* — dokümante edilmiş güvenlik dersi, canlı koddaki bu
     doğrudan yol tarafından fiilen bypass edilmiş durumda.
   - `git log -1 --format=%ci -- agents/orchestrator.py
     control_plane/approval_queue.py` → 2026-09-20 08:16:37 UTC — üç
     günden beri bu dosyalarda hiçbir kod değişikliği yok, bulgu 35.
     turdan bu yana birebir aynı.
   - 132. turda kullanıcıya iletildi, karar bekleniyor; sermaye/güvenlik
     etkisi nedeniyle bu tur da tek taraflı değiştirilmedi. Görevin genel
     yetkilendirmesi ("gereken tüm kararları alabilirsin") strateji ve
     pozisyon boyutlandırma kararları için yeterli görülüyor, ama
     dokümante edilmiş bir olay dersini (zorunlu insan onayı) sessizce
     geri alan bir güvenlik-modeli değişikliğini kapsayacak kadar açık
     görülmüyor — önceki 37 turla aynı gerekçe.
2. **Zamanlama sıklığı** (106. turdan beri açık, **21. kez art arda**
   doğrulandı) — hesap seviyesinde bir ayar, bu oturumdan değiştirilemiyor.

Bu turda kod tabanında yeni bir bulgu yok. Son gerçek kod değişikliği
2026-09-20'den beri yok, son 3 gündür yalnızca `docs/reviews/` ekleri
birikiyor.

## Canlı sermaye / pozisyon durumu
128. turdan beri tespit edildiği gibi, bu bulut oturumunda
`data/positions.json`, `data/control.json`, `data/status.json` yok —
canlı bot kullanıcının kendi yerel ortamında çalışıyor, bu sandbox'ta
değil. `data/` altında yalnızca yardımcı/geçmiş/örnek dosyalar var,
bunlar canlı P&L'i yansıtmıyor. Bu nedenle "sermayenin %10'u kadar
kazanma" hedefine ilerleme bu oturumdan doğrudan gözlemlenemiyor —
yalnızca kod tabanı/strateji mantığı incelenip doğrulanabiliyor.

## Testler
Tam test paketi kökten çalıştırıldı (`python3 -m pip install -r
requirements.txt` sonrası `python3 -m pytest -q`): **1790 passed, 4
skipped** — regresyon yok, önceki turlarla birebir aynı sonuç.

## Bildirim kararı
114. turdan beri uygulanan "yeni bilgi yoksa bildirme" politikası bu
turda da uygulandı. 132. tur bu iki bulgu için kullanıcıya zaten bildirim
gönderdi; 133-140. turlar ve bu tur (141.) kod tabanında veya bulgularda
hiçbir değişiklik bulmadı. Aynı bilgiyi tekrar tekrar bildirim olarak
göndermek gürültü olur. Ayrı bir push bildirimi gönderilmedi.

## Sonuç
Kod tabanı sağlıklı (1790/1790, regresyon yok). Açık aksiyon kalemleri
değişmeden duruyor ve kullanıcı kararını bekliyor:
1. Onay kuyruğu güvenlik açığı — `agents/orchestrator.py` satır ~1137
   civarındaki doğrudan emir yolu, kodun kendi yorumunda da belirtildiği
   gibi onay kuyruğunu bypass ediyor. Kullanıcının hangi yönde
   çözüleceğine (bypass'ı bilinçli olarak koru vs. bu yolu da queue'ya
   gerçekten bağla) karar vermesi gerekiyor.
2. Rutin tetikleyicisinin hesap seviyesinde günlük aralığa çekilmesi —
   bu oturumdan yapılamıyor; kullanıcının hesap/zamanlayıcı ayarından
   düzeltmesi gerekiyor. 21 tur/saattir kesintisiz saatlik/düzensiz
   tetikleniyor.
