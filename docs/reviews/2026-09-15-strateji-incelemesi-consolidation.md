# Günlük Strateji İncelemesi — 2026-09-15 (konsolidasyon turu)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Bulgu — düzeltmeler `main`'e hiç ulaşmıyordu

Bu turun başında `main` hâlâ `a0e3d10` (37. çalışma) üzerindeydi. Aynı gün
içinde ~4 saatlik bir pencerede (01:08–05:17 UTC) açılmış **5 ayrı, unmerged
PR** vardı — #66, #67, #68, #69, #70 — her biri "Nth daily review" etiketli,
her biri kendi testleriyle doğrulanmış, hepsi `mergeable_state: clean`
(repoda CI yok, review zorunluluğu yok), ama hiçbiri `main`'e alınmamıştı.
Yani bu botun canlı davranışını gerçekten etkileyecek 5 doğrulanmış düzeltme
— içlerinde gerçek sermaye etkisi olan iki tanesi dahil — sadece açık PR
olarak bekliyordu.

**Neden önemli:** "günlük inceleme" sürecinin amacı canlı botu düzeltmektir;
bir düzeltme `main`'e girmeden botun davranışını değiştirmez. Süreç PR açmayı
otomatikleştirmiş ama merge adımını otomatikleştirmemiş — bu turdan önce
düzeltmeler PR kuyruğunda birikip hiçbir zaman devreye girmiyordu.

En kritik iki bulgunun canlı etkisi:
- **#67 (GOLDEN_HOUR/GOOD_HOUR):** $4.00 `MAX_BET_CAP`'ten SONRA uygulanan
  boost, cap'i yeniden uygulamıyordu — 17-19 ET saatlerinde gerçek pozisyon
  boyutu $5.20'ye kadar çıkabiliyordu (%30 fazla, gerçek sermaye ile).
- **#70 (_bond_cycle):** yerel `bond_capital` sayacı istenen `bet_size` ile
  değil gerçek CLOB emrinin maliyetiyle düşülmediği için, aynı döngü
  içinde gerçekte karşılanamayacak ek bond emirlerine izin verebiliyordu.

## Bu turda yapılanlar

Her 5 PR'ın diff'i tek tek okundu (kod + eklenen testler + PR açıklamasındaki
düzeltme-öncesi/sonrası test kanıtı). Hepsi aynı disiplinli, dar kapsamlı,
test-kanıtlı düzeltme paternini izliyordu (önceden merge edilmiş 60+
düzeltmeyle aynı stil). Sırayla `main`'e alındı:

1. `#66` — `LOW_LIQUIDITY_HOURS` gerçek duvar-saati bağımlılığı, REDUCE
   double-apply testini zaman-bağımlı flaky yapıyordu (test-only fix).
2. `#70` — `_bond_cycle()` gerçek emir maliyeti yerine istenen `bet_size` ile
   düşüyordu.
3. `#69` — 4h marketler ML gate'e training'den 16x farklı `window_minutes`
   besliyordu (train/serve skew, `ML_CAUTION` üzerinden gerçek Kelly boyutunu
   etkiliyor).
4. `#67` — GOLDEN_HOUR/GOOD_HOUR boost, `MAX_BET_CAP`'i aşıyordu.
5. `#68` — `TradeClassifier` edge train/serve skew + yönsüz "trade velocity"
   yönlü sinyal gibi oylanıyordu. Bu PR, `#66` ile aynı isimde yeni bir dosya
   ekliyordu (`docs/reviews/2026-09-15-strateji-incelemesi.md`) — add/add
   çakışması `main`'i merge edip PR'ın kendi incelemesini
   `2026-09-15-strateji-incelemesi-38b.md`'ye taşıyarak çözüldü (sadece
   dokümantasyon, mantık değişikliği yok), tam suite tekrar doğrulandı
   (713 passed, 2 skipped), sonra push + merge edildi.

Her merge sonrası `mergeable_state` kontrol edildi; kod çakışması hiçbirinde
çıkmadı (her PR farklı dosyaya ya da aynı dosyanın farklı bölgesine
dokunuyordu). Birleştirilmiş `main` üzerinde tam suite: **713 passed, 2
skipped, 0 failed** — önceki turların işaret ettiği `LOW_LIQUIDITY_HOURS`
flake'i de `#66` ile birlikte çözüldü.

## Gözlem — inceleme sıklığı

Bugünün 5 PR'ı ~4 saatlik bir pencerede açılmış (01:08–05:17 UTC), "günde
bir kez" beklentisinin oldukça üzerinde bir sıklık. Talimat "her gün
gözden geçirsin" diyor; zamanlamanın gerçekten günlük mü yoksa daha sık mı
tetiklendiğini bu oturumdan doğrulayamıyorum — sadece PR zaman damgalarından
gözlemleyebiliyorum. Kullanıcıya ayrıca bildirildi.

## Sıradaki tur için öneriler (PR #68'in bıraktığı yerden)
- `control_plane/entry_window_guard.py` ve `reentry_guard.py`'nin gerçek
  zamanlı davranışı henüz derinlemesine incelenmedi.
- `strategies/monte_carlo.py`'deki `net_edge*2.0` çift-sayımı şu an sadece
  log/`viable` alanına gidiyor (canlı karara etkisi yok) — ileride bağlanırsa
  yanlış EV raporlayabilir.
- `agents/subagents/research_agent.py`'nin whale/smart-money birleştirme
  mantığı taranmadı.
- **En önemlisi:** bundan sonraki her turun sonunda, o turun kendi PR'ını
  açtıktan sonra geriye dönüp *önceki* açık PR'ları da (varsa) gözden
  geçirip merge etmesi gerekiyor — aksi halde bu turun düzelttiği "biriken
  PR kuyruğu" sorunu tekrar oluşur.

## Sonuç
`main` artık `#66`–`#70` dahil tüm doğrulanmış düzeltmelere sahip
(`8501727`). Bu oturum yeni bir kod hatası bulmak yerine, zaten bulunmuş ve
doğrulanmış 5 düzeltmenin gerçekten canlı koda ulaşmasını sağladı — sermaye
hedefine en doğrudan katkı budur, çünkü merge edilmemiş bir düzeltmenin
canlı performansa hiçbir etkisi yoktur.
