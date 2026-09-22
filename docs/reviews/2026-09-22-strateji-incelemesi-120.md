# 120. Tur Strateji İncelemesi — 2026-09-22

## Kapsam
Planlı ("her gün stratejini gözden geçir, sermayenin %10'u kadar kazanma
hedefi için gereken kararları al") görevin bu turdaki çalıştırması.

## Durum tespiti
- GitHub API ile doğrulandı: bu turda başlarken `main` üzerinde açık,
  merge edilmemiş bir PR (#219, 119. tur incelemesi) bulundu — başka bir
  oturum tarafından ~1 saat önce açılmış, içeriği doğrulanıp (1790/4
  test sonucu, aynı iki açık bulgu, temiz `mergeable_state`) `main`'e
  merge edildi (`844f759`). Bu turdan sonra açık PR kalmadı.
- Bu sandbox'ın yerel `HEAD`'i `origin/main`'in yeni tepesiyle (`844f759`)
  eşitlendi (`git reset --hard`), fark yok.
- Bu sandbox'ta hâlâ canlı bot örneği yok: `data/control.json`,
  `data/status.json`, `data/positions.json` mevcut değil → %10 sermaye
  hedefine karşı bu turdan da doğrudan ölçülebilir ilerleme
  sağlanamıyor (106. turdan beri değişmeyen, 113. turda kullanıcıya
  iletilmiş durum).

## Düzeltme: 119. tur kadans iddiası
119. turun (#219) kendi metni "bu tur 118. turdan yaklaşık 24 saat sonra
tetiklendi, günlük kadans bu kez tutmuş görünüyor" diyordu. Gerçek commit
zaman damgaları bunu doğrulamıyor: 118. tur merge'i `2026-09-22T01:03:48Z`,
119. tur PR'ının açılışı `2026-09-22T02:05:56Z` — aradaki fark ~62 dakika,
24 saat değil. Son 10 tur (111→120) zaman damgaları tekrar kontrol edildi:
aralıklar 1-3 saat arasında, tamamı hâlâ saatlik mertebede. Yani "günlük"
kadans sorunu **çözülmemiş**; 119. turdaki iddia hatalıydı, bu turda
düzeltildi. Bulgunun kendisi (hesap seviyesi zamanlayıcı, oturum içinden
değiştirilemez) 106. turdan beri aynı.

## Bug taraması
İki bilinen bulgu kaynak koddan yeniden doğrulandı, ikisi de değişmemiş:

1. **Onay kuyruğu / doğrudan emir yolu çelişkisi** (104. turdan beri
   açık): `agents/orchestrator.py:42` hâlâ `_enqueue_order`'ı import edip
   başka hiç çağırmıyor; canlı `_cycle` sinyal döngüsü (satır 1119,
   `is_approved=True`) ve onay-sonrası yürütme yolu (satır 1406,
   `is_approved=True,  # Zaten approved listesinden geldi`) hâlâ doğrudan
   `client.place_order()`'a gidiyor (satır 1138 ve 1422) —
   `docs/APPROVAL_WORKFLOW_SPEC.md`'nin "doğrudan emir yolu kapalı"
   ifadesiyle çelişmeye devam ediyor. Sermaye/güvenlik etkisi nedeniyle bu
   tur da tek taraflı kod değişikliği yapılmadı.
2. **Zamanlama sıklığı** (106. turdan beri açık, yukarıda doğrulandı):
   rutin hâlâ "günlük" yerine saatlik mertebede tetikleniyor; hesap
   seviyesinde bir ayar, bu oturumdan değiştirilemiyor.

Tam test paketi kökten çalıştırıldı (`pip install -r requirements.txt`
sonrası `pytest` — `tests/`, `calibration/tests/`,
`execution_realism/tests/`, `crypto_directional/tests/`,
`signal_bridge/tests/` dahil): **1790 passed, 4 skipped** — regresyon
yok, önceki turlarla birebir aynı sonuç. Test sonrası `git status --short`
temiz (state-leak yok).

## Bu turda yeni bulgu
Yok (119. turun kadans iddiasındaki hata dışında — bkz. yukarı, düzeltildi).

## Bildirim kararı
113. tur, iki açık bulguyu (onay kuyruğu bypass, saatlik tetiklenme) ve
sandbox'ta canlı pozisyon verisi olmadığı gerçeğini gerçek bir push
bildirimiyle iletti. Bu turda altta yatan bulgularda bir değişiklik yok
— sadece önceki turun hatalı bir gözlemi düzeltildi, temel sonuç (kadans
sorunu hâlâ açık) aynı kaldı — bu yüzden bu round için de ayrı bir push
bildirimi gönderilmedi (114-119. turların "yeni bilgi yoksa bildirme"
politikasıyla tutarlı).

## Sonuç
Kod tabanı sağlıklı (1790/1790, regresyon yok), yeni bir live bug yok.
119. turun PR'ı doğrulanıp merge edildi, kadans iddiasındaki hata bu
turda düzeltildi. Açık aksiyon kalemleri (onay kuyruğu/doğrudan emir
çelişkisi çözümü, rutin tetikleyicisinin hesap seviyesinde günlük
aralığa çekilmesi, canlı pozisyon verisinin bu sandbox'a bağlanıp
bağlanmayacağı) değişmeden kullanıcı kararını bekliyor; 113. turda
zaten iletildi.
