# 115. Tur Strateji İncelemesi — 2026-09-21

## Kapsam
Planlı ("her gün stratejini gözden geçir, sermayenin %10'u kadar kazanma
hedefi için gereken kararları al") görevin bu turdaki çalıştırması.

## Yapılan
- Bu round için **iki paralel oturumun** bağımsız ürettiği PR bulundu
  (#213 `ha8q7a`, #214 `b17xdv`), ikisi de aynı dosya yolunu
  (`docs/reviews/2026-09-21-strateji-incelemesi-114.md`) ve aynı sonucu
  taşıyordu (kod 113. turdan beri değişmemiş, 1790/1790, iki bilinen
  bulgu aynı, yeni bildirim gerekmiyor). #213 merge edildi (`95538ba`);
  #214 içerik olarak birebir aynı sonucu tekrarladığı için (farklı bir
  dosya adına yeniden adlandırmak yalnızca gürültü ekleyeceğinden)
  duplicate olarak kapatıldı ve gerekçesi PR'da açıklandı.
- `agents/orchestrator.py` bu oturumdan bağımsız olarak yeniden okundu:
  `_enqueue_order` (satır 42) hâlâ hiç çağrılmıyor (`grep -c` → 1, sadece
  import); ana canlı sinyal döngüsündeki (`_cycle`, satır 1119) emir yolu
  `is_approved=True` sabitiyle doğrudan `client.place_order()`'a gidiyor
  — `# ── DOĞRUDAN EMİR VER (onay kuyruğu bypass) ──` yorumu bunu açıkça
  belirtiyor. İkinci çağrı (satır 1406/1422) incelendi ve *farklı* bir
  yol olduğu doğrulandı: bu, zaten onaylanmış emirleri (`order_req`,
  `is_recheck_after_approval=True`) işleyen ayrı bir yürütme adımı —
  bulgunun kendisi asıl olarak `_cycle` içindeki ilk yoldan kaynaklanıyor.
  Bu, `docs/APPROVAL_WORKFLOW_SPEC.md`'nin "Dogrudan emir verme yolu
  kapatilmistir" ifadesiyle hâlâ çelişiyor — sermaye/güvenlik etkisi
  nedeniyle tek taraflı düzeltme yapılmadı (104. turdan beri aynı karar).
- Tam test paketi bağımsız olarak sıfırdan kuruldu (`pip install -r
  requirements.txt`, bu container'da önceden kurulu değildi) ve
  çalıştırıldı: **1790 passed, 4 skipped** — regresyon yok. Test sonrası
  `git status --short` temiz (state-leak yok).
- **Zamanlama sıklığı bulgusu doğrudan test edildi**: bu oturumun
  `CronList` aracı "No scheduled jobs" döndürdü — yani bu "günlük" rutin
  bu session içinde `CronCreate` ile kurulmuş bir iş değil (o araç
  session-only ve 7 günde otomatik siliniyor; bu görev zaten 115 tur
  sürmüş). Bu, önceki turların "hesap seviyesinde bir ayar, session
  içinden değiştirilemez" tespitini **doğrudan doğruluyor** (varsayım
  değil, araç çağrısıyla teyit edilmiş bulgu).

## Bu turda yeni bulgu
Yok (kod tabanı sağlam, 113/114. turdan beri bayt bayt aynı). İki bilinen
bulgu (onay kuyruğu bypass, saatlik tetiklenme) değişmeden duruyor.

## Bildirim kararı
113. tur bu iki bulguyu az önce gerçek bir push bildirimiyle iletti;
114. turun iki paralel oturumu da haklı olarak tekrar bildirim
göndermedi. Bu turda da ne kodda ne bulgularda yeni bir gelişme yok —
yalnızca iki duplicate PR'ın konsolidasyonu ve zamanlama bulgusunun
doğrudan doğrulanması var. Bu nedenle bu round için de ayrı bir push
bildirimi gönderilmedi (aynı politika: yeni bilgi yoksa bildirme).

## Sonuç
Kod tabanı sağlıklı (1790/1790, regresyon yok), yeni bir live bug yok.
Açık aksiyon kalemleri (onay kuyruğu/doğrudan emir çelişkisi çözümü,
rutin tetikleyicisinin hesap seviyesinde günlük aralığa çekilmesi, canlı
pozisyon verisinin bu sandbox'a bağlanıp bağlanmayacağı) değişmeden
kullanıcı kararını bekliyor; 113. turda zaten iletildi.
