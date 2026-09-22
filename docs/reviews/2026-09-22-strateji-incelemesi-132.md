# 132. Tur Strateji İncelemesi — 2026-09-22

## Kapsam
Planlı ("her gün stratejini gözden geçir, sermayenin %10'u kadar kazanma
hedefi için gereken kararları al") görevin bu turdaki çalıştırması.
Tetiklenme: ~16:03 UTC.

## Durum tespiti
- Bu tur başladığında `main` üzerinde açık bir PR vardı: #232 ("131. tur"),
  15:08:09 UTC'de başka bir oturum tarafından oluşturulmuş,
  `mergeable_state: clean`, 0 CI check (repoda CI workflow yok, önceki
  turlarla tutarlı). İçeriği bağımsız doğrulandı ve merge edildi
  (`d25aea5`):
  - Tek değişiklik `docs/reviews/2026-09-22-strateji-incelemesi-131.md`
    dosyasının eklenmesiydi — kod değişikliği yok.
  - İki bilinen bulgu (`_enqueue_order` çağrılmıyor, `is_approved=True`
    satır 1119/1406) kaynaktan yeniden doğrulandı, değişmemiş.
  - Local branch `git rebase origin/main` ile güncellendi — kayıp iş
    yoktu.
- Kadans: 131. tur PR'ı 15:08:09 UTC oluşturuldu, bu tur ~16:03 UTC
  başladı — fark ~55 dakika. 106. turdan beri açık olan "günlük yerine
  saatlik tetikleniyor" bulgusu bu turda da (**12. kez art arda** ~1
  saatlik aralıkla) doğrulandı.
- `incident_bundle/`, `incident_bundle_v2/`, `review_bundle/` dizinleri
  kontrol edildi — bunlar `git ls-files` ile doğrulanmış, kasıtlı olarak
  git-tracked dizinler (127/211 dosya), 18 Eylül'den beri değişmemiş.
  Yeni bir bulgu değil, sadece önceki turda `data/` altında bulunan
  kalıntı dosyalarla (backup'lar) karıştırılmasın diye kaydedildi.

## Bug taraması — bilinen bulgular (değişmedi)
1. **Onay kuyruğu / doğrudan emir yolu çelişkisi** (104. turdan beri açık,
   113. turda kullanıcıya iletildi, **şimdi 29. turdur açık**) —
   `control_plane/approval_queue.py` dosya başlığı açıkça şunu söylüyor:
   *"INC-2026-03-15-001 dersi: Sinyal → emir arasında insan onayı
   ZORUNLU."* Tam state machine (PENDING→APPROVED→EXECUTED) kodlanmış ve
   test edilmiş (`tests/test_approval_queue.py`), ama `agents/orchestrator.py`
   satır 1137'deki yorum bunu açıkça atlıyor: `# DOĞRUDAN EMİR VER (onay
   kuyruğu bypass)`, `is_approved=True` sabit geçiliyor (satır 1119).
   İkinci bir kod yolu (satır ~1360-1420) onaylanmış emirleri işlemek
   üzere kurulmuş ve çalışır durumda, ama hiçbir yerde `_enqueue_order()`
   çağrılmadığından bu yola hiç emir düşmüyor — kuyruk mekanizması canlıda
   fiilen ölü kod. Bu, dokümante edilmiş bir olay dersinin (insan onayı
   zorunlu) sessizce geri alınmış olabileceğini gösteriyor. Sermaye/can
   parası etkisi olan bu değişikliği bu oturum yine tek taraflı yapmadı.
2. **Zamanlama sıklığı** (106. turdan beri açık, **12. kez art arda**
   doğrulandı) — hesap seviyesinde bir zamanlayıcı ayarı, bu oturumdan
   değiştirilemiyor.

Bu turda kod tabanında yeni bir bulgu yok.

## Testler
Tam test paketi kökten çalıştırıldı (`python3 -m pip install -r
requirements.txt` sonrası `python3 -m pytest`): **1790 passed, 4
skipped** — regresyon yok, önceki turlarla birebir aynı sonuç.

## Bildirim kararı
114. turdan beri uygulanan "yeni bilgi yoksa bildirme" politikasından bu
tur **bilinçli olarak sapıldı** ve kullanıcıya bildirim gönderildi.
Gerekçe: kod tabanında yeni bir bulgu olmasa da, aşağıdaki üç durum
kümülatif olarak eşik aştı:
1. Zamanlama sıklığı bulgusu artık 12 kez art arda (~12 saat) doğrulandı
   — "günlük" olması gereken görev saatlik çalışıyor, ~12x kaynak israfı
   (her turda tam pytest + PR + review dosyası).
2. Onay kuyruğu bulgusu 29 turdur (104. turdan beri) çözümsüz — ve bu
   turda kök nedeni netleşti: dokümante edilmiş bir güvenlik dersinin
   (insan onayı zorunlu) canlı kodda fiilen devre dışı bırakılmış olması,
   kullanıcının aktif bir kararı olmadan sürüyor.
3. Bu sandbox'ın canlı bot durumuna (sermaye, pozisyon, P&L) hiçbir
   zaman erişimi olmayacağı 128. turda mimari olarak kanıtlandı — yani bu
   rutin, kendi asıl hedefine ("%10 sermaye kazancı için karar al") karşı
   hiçbir zaman ölçülebilir ilerleme rapor edemeyecek; sadece kod
   tabanının statik sağlığını doğrulayabiliyor.

## Sonuç
Kod tabanı sağlıklı (1790/1790, regresyon yok). Rutin, kurulduğu haliyle
asıl amacına (günlük, canlı sermaye bazlı strateji kararı) hizmet
edemiyor — sadece saatlik kod-sağlığı doğrulaması yapabiliyor. İki açık
karar kalemi (onay kuyruğu güvenlik açığı, zamanlama düzeltmesi)
kullanıcı aksiyonu bekliyor; bu turun bildirimi bu ikisini netleştirmek
içindir.
