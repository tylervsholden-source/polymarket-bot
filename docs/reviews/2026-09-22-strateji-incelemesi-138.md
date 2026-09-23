# 138. Tur Strateji İncelemesi — 2026-09-22

## Kapsam
Planlı ("her gün stratejini gözden geçir, sermayenin %10'u kadar kazanma
hedefi için gereken kararları al") görevin bu turdaki çalıştırması.
Tetiklenme: ~23:03 UTC.

## Durum tespiti
- Bu tur başladığında `main` üzerinde açık bir PR vardı: #238 ("137.
  tur"), 22:06:03 UTC'de başka bir oturum tarafından oluşturulmuş,
  `mergeable_state: clean`. İçeriği (`get_files` ile patch) doğrudan
  okundu ve merge edildi (`40c193d`):
  - Tek değişiklik `docs/reviews/2026-09-22-strateji-incelemesi-137.md`
    dosyasının eklenmesiydi — kod değişikliği yok.
  - PR gövdesi: 137. tur, açılışta açık PR bulunmadığını (136. turun
    PR'ı #237 önceki turda zaten merge edilmişti), iki bilinen bulguyu
    kaynaktan yeniden doğruladığını ve değişiklik bulmadığını
    raporluyor.
  - Local branch `git fetch origin main` + `git merge --ff-only
    origin/main` ile güncellendi.
- Kadans: 137. tur PR'ı 22:06:03 UTC oluşturuldu, bu tur ~23:03 UTC
  başladı — fark ~57 dakika. "Günlük yerine saatlik/düzensiz aralıklarla
  tetikleniyor" bulgusu bu turda da (**18. kez art arda**) doğrulandı.
  `CronList` bu oturumda da erişilmedi/boş çıktı verdi — tetikleyici bu
  oturumun erişemediği hesap seviyesinde bir mekanizma, önceki 17 turun
  tespitiyle tutarlı.

## Bug taraması — bilinen bulgular (kaynaktan bağımsız yeniden doğrulandı, değişmedi)
1. **Onay kuyruğu / doğrudan emir yolu çelişkisi** (104. turdan beri açık,
   **şimdi 35. turdur açık**) — bu turda da subagent raporuna güvenmeden
   doğrudan kaynak okundu (`agents/orchestrator.py`):
   - Satır 41-42: `core.approval_queue.enqueue`'ı `_enqueue_order` adıyla
     import ediyor; `grep -n "_enqueue_order(" agents/orchestrator.py` →
     **sıfır çağrı** — fonksiyon dosyada import edildiği yer dışında hiç
     kullanılmıyor.
   - Satır 1119 (`_process_signal` içindeki otonom/doğrudan emir yolu):
     `check_live_gate(...)` çağrısına sabit `is_approved=True` geçiyor,
     hemen ardından (satır 1137) **kod içinde kendi yorumuyla işaretli**
     doğrudan emir veriyor: `# ── DOĞRUDAN EMİR VER (onay kuyruğu
     bypass) ──`. Bu, kaynağın kendisinin bypass'ı açıkça belgelediğini
     gösteriyor — yorum eskiden kalma değil, bu turda tekrar okundu.
   - Satır 1406 (`_execute_approved_orders()`) farklı: `_get_approved_orders()`
     (satır 1351, `get_approved` alias'ı) ile onay kuyruğundan gerçekten
     okuyor; oradaki `is_approved=True` zaten onaylı listeden gelen
     emirler için bir "recheck" bayrağı (yorum: "Zaten approved
     listesinden geldi") — bu ikinci yol bypass değil. Asıl sorun sadece
     birinci (satır 1119) doğrudan yol.
   - `control_plane/approval_queue.py` başlığı: *"INC-2026-03-15-001
     dersi: Sinyal → emir arasında insan onayı ZORUNLU."* — dokümante
     edilmiş güvenlik dersi, canlı koddaki doğrudan yol tarafından fiilen
     bypass edilmiş durumda. 132. turda kullanıcıya iletildi, karar
     bekleniyor; sermaye/güvenlik etkisi nedeniyle bu tur da tek taraflı
     değiştirilmedi. Kullanıcının bu göreve verdiği genel yetki
     ("gereken tüm kararları alabilirsin") strateji/pozisyon
     boyutlandırma kararları için yeterli görüldü, ama dokümante edilmiş
     bir olay dersini (zorunlu insan onayı) sessizce geri alan bir
     mimari/güvenlik modeli değişikliği için yeterli görülmedi — önceki
     34 turla aynı gerekçe.
2. **Zamanlama sıklığı** (106. turdan beri açık, **18. kez art arda**
   doğrulandı) — hesap seviyesinde bir ayar, bu oturumdan değiştirilemiyor.

Bu turda kod tabanında yeni bir bulgu yok. Son 20 commit tarandı
(`git log --stat`); son gerçek kod değişikliği yok, sadece
`docs/reviews/` ekleri.

## Canlı sermaye / pozisyon durumu
Bu bulut oturumunda `data/positions.json`, `data/control.json`,
`data/status.json` yok (128. turda tespit edildiği gibi, canlı bot
kullanıcının kendi yerel makinesinde/Antigravity IDE'de çalışıyor, bu
sandbox'ta değil). Bu nedenle bu oturumdan gerçek P&L veya "%10 kazanç"
hedefine ilerleme doğrudan gözlemlenemiyor — sadece kod tabanı/strateji
mantığı incelenebiliyor.

## Testler
Tam test paketi kökten çalıştırıldı (`python3 -m pip install -r
requirements.txt` sonrası `python3 -m pytest -q`): **1790 passed, 4
skipped** — regresyon yok, önceki turlarla birebir aynı sonuç.

## Bildirim kararı
114. turdan beri uygulanan "yeni bilgi yoksa bildirme" politikası bu
turda da uygulandı. 132. tur bu iki bulgu için kullanıcıya zaten
bildirim gönderdi; 133-138. turlar (bu tur dahil) kod tabanında veya
bulgularda hiçbir değişiklik bulmadı. Aynı bilgiyi tekrar tekrar
bildirim olarak göndermek gürültü olur. Ayrı bir push bildirimi
gönderilmedi.

## Sonuç
Kod tabanı sağlıklı (1790/1790, regresyon yok). Açık aksiyon kalemleri
değişmeden duruyor ve kullanıcı kararını bekliyor:
1. Onay kuyruğu güvenlik açığı — `agents/orchestrator.py` satır 1119
   civarındaki doğrudan emir yolu, kodun kendi yorumunda da belirtildiği
   gibi onay kuyruğunu bypass ediyor. Kullanıcının hangi yönde
   çözüleceğine (bypass'ı bilinçli olarak koru vs. bu yolu da queue'ya
   gerçekten bağla) karar vermesi gerekiyor.
2. Rutin tetikleyicisinin hesap seviyesinde günlük aralığa çekilmesi —
   bu oturumdan yapılamıyor; kullanıcının hesap/zamanlayıcı ayarından
   düzeltmesi gerekiyor. 18 tur/saattir kesintisiz saatlik/düzensiz
   tetikleniyor.
