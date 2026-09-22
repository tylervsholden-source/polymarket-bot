# 131. Tur Strateji İncelemesi — 2026-09-22

## Kapsam
Planlı ("her gün stratejini gözden geçir, sermayenin %10'u kadar kazanma
hedefi için gereken kararları al") görevin bu turdaki çalıştırması.
Tetiklenme: ~15:03 UTC.

## Durum tespiti
- Bu tur başladığında `main` üzerinde açık bir PR vardı: #231 ("130. tur"),
  14:08:12 UTC'de başka bir oturum tarafından oluşturulmuş,
  `mergeable_state: clean`, 0 CI check (repoda CI workflow yok, önceki
  turlarla tutarlı). İçeriği bağımsız doğrulandı ve merge edildi
  (`c9baa08`):
  - Tek değişiklik `docs/reviews/2026-09-22-strateji-incelemesi-130.md`
    dosyasının eklenmesiydi — kod değişikliği yok.
  - İki bilinen bulgu (`_enqueue_order` çağrılmıyor, `is_approved=True`
    satır 1119/1406) kaynaktan yeniden doğrulandı, değişmemiş.
  - Local branch `origin/main`'e sıfırlandı (`git checkout -B` ile) —
    kayıp iş yoktu.
- Kadans: 130. tur PR'ı 14:08:12 UTC oluşturuldu, bu tur ~15:03 UTC
  başladı — fark ~55 dakika. 106. turdan beri açık olan "günlük yerine
  saatlik tetikleniyor" bulgusu bu turda da (11. kez art arda ~1 saatlik
  aralıkla) doğrulandı. Bu tur ayrıca bu oturumun kendi zamanlama
  araçlarını (`CronList`) kontrol etti: bu oturuma bağlı hiçbir
  zamanlanmış görev yok — tetikleyici, bu oturumdan görünmeyen/erişilemeyen
  hesap seviyesinde bir mekanizma. Önceki turların "bu oturumdan
  değiştirilemez" tespiti doğrulandı, yeni bir çözüm yolu bulunamadı.

## Bug taraması — bilinen bulgular (değişmedi)
1. **Onay kuyruğu / doğrudan emir yolu çelişkisi** (104. turdan beri açık,
   113. turda kullanıcıya iletildi) — `control_plane/approval_queue.py`
   içindeki `ApprovalQueue` mekanizması `agents/orchestrator.py` içine
   `_enqueue_order` olarak import ediliyor ama hiç çağrılmıyor (`grep -c
   '_enqueue_order(' agents/orchestrator.py` → 0); emirler doğrudan
   `is_approved=True` ile (satır 1119 ve 1406) canlıya gidiyor. Bu turda
   kod akışı yeniden okundu: `is_approved=True` muhtemelen kasıtlı —
   otonom 60-120sn döngüsünde insan onayını bekleyen bir emir bot'un asıl
   amacını (otonom trading ile %10 hedefi) engeller. Ama bu bir varsayım;
   iki yönün de sermaye/güvenlik etkisi var (bypass = insan denetimsiz
   emir vs. queue'ya bağlama = bot'un trade atamaması). Sermaye etkisi
   nedeniyle bu tur da tek taraflı değiştirilmedi; karar kullanıcıda.
2. **Zamanlama sıklığı** (106. turdan beri açık) — hesap seviyesinde bir
   zamanlayıcı ayarı; bu tur `CronList` ile bu oturumun kendi zamanlama
   yüzeyi kontrol edildi, ilgisiz çıktı (session-only, bu tetikleyiciyle
   bağlantısız). Değişiklik yapılamadı.

Bu turda yeni bir bulgu yok.

## Testler
Tam test paketi kökten çalıştırıldı (`python3 -m pip install -r
requirements.txt` sonrası `python3 -m pytest`): **1790 passed, 4
skipped** — regresyon yok, önceki turlarla birebir aynı sonuç.

## Bildirim kararı
114. turdan beri uygulanan "yeni bilgi yoksa bildirme" politikası bu
turda da uygulandı: 130. turun PR'ı doğrulanıp merge edildi, iki bilinen
bulgu (onay kuyruğu çelişkisi, zamanlama sıklığı) değişmeden duruyor,
yeni bir karar ihtiyacı ya da live bug yok. Ayrı bir push bildirimi
gönderilmedi.

## Sonuç
Kod tabanı sağlıklı (1790/1790, regresyon yok). Açık aksiyon kalemleri
(onay kuyruğu/doğrudan emir çelişkisinin hangi yönde çözüleceği, rutin
tetikleyicisinin hesap seviyesinde günlük aralığa çekilmesi) değişmeden
kullanıcı kararını bekliyor — her iki kalem de defalarca kez (17+ tur)
yeniden doğrulandı ve bu oturumdan çözülemeyeceği kesinleşti.
