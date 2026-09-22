# 122. Tur Strateji İncelemesi — 2026-09-22

## Kapsam
Planlı ("her gün stratejini gözden geçir, sermayenin %10'u kadar kazanma
hedefi için gereken kararları al") görevin bu turdaki çalıştırması.

## Durum tespiti
- Bu tur başlarken `main` üzerinde **iki paralel** açık PR bulundu, ikisi
  de 121. tur incelemesini taşıyordu:
  - #221 (`nisx8v`, 04:06 UTC) — içerik doğru ve ek bir gözlem taşıyordu:
    bu sandbox'ta çıplak `pytest` komutu proje bağımlılıklarından izole
    bir `uv tool` Python'una çözümleniyor ve toplama başarısız oluyor;
    `python3 -m pip install -r requirements.txt` sonrası `python3 -m
    pytest` sorunsuz çalışıyor.
  - #222 (`7dwcp1`, 05:06 UTC) — aynı sonucu (kod değişmemiş, iki bilinen
    bulgu aynı, 1790/4) tekrarlıyordu, ek bilgi yoktu.
  - #221 bağımsız doğrulandıktan sonra merge edildi (`f1b6014`); #222
    duplicate olarak kapatıldı, gerekçe PR'a yorum olarak eklendi.
- Bu doğrulama bu oturumda da tekrarlandı: `python3 -m pytest` çıplak
  `/root/.local/bin/pytest`'in (v9.0.2, proje bağımlılıkları olmadan)
  toplamayı başaramadığını, `pip show pytest` proje Python'unda paketin
  bulunmadığını doğruladı — #221'in gözlemi birebir teyit edildi.
- Bu sandbox'ta hâlâ canlı bot örneği yok: `data/control.json`,
  `data/status.json`, `data/positions.json` mevcut değil → %10 sermaye
  hedefine karşı bu turdan da doğrudan ölçülebilir ilerleme
  sağlanamıyor (106. turdan beri değişmeyen, 113. turda kullanıcıya
  iletilmiş durum).

## Bug taraması
İki bilinen bulgu kaynak koddan yeniden doğrulandı, ikisi de değişmemiş:

1. **Onay kuyruğu / doğrudan emir yolu çelişkisi** (104. turdan beri
   açık): `agents/orchestrator.py:42` hâlâ `_enqueue_order`'ı import edip
   başka hiç çağırmıyor; canlı `_cycle` sinyal döngüsü (satır 1119,
   `is_approved=True`) ve onay-sonrası yürütme yolu (satır 1406,
   `is_approved=True,  # Zaten approved listesinden geldi`) hâlâ doğrudan
   `client.place_order()`'a gidiyor — `docs/APPROVAL_WORKFLOW_SPEC.md`'nin
   "doğrudan emir yolu kapalı" ifadesiyle çelişmeye devam ediyor.
   Sermaye/güvenlik etkisi nedeniyle bu tur da tek taraflı kod değişikliği
   yapılmadı.
2. **Zamanlama sıklığı** (106. turdan beri açık): rutin hâlâ "günlük"
   yerine saatlik mertebede tetikleniyor — bu turda ayrıca iki paralel
   oturumun aynı saat içinde (04:06 ve 05:06 UTC) tetiklenmiş olması
   (üstüne bu turun 06:15 UTC'de üçüncü tetiklenme olması) bunu somut
   şekilde gösteriyor; hesap seviyesinde bir ayar, bu oturumdan
   değiştirilemiyor.

Tam test paketi kökten çalıştırıldı (`python3 -m pip install -r
requirements.txt` sonrası `python3 -m pytest`): **1790 passed, 4
skipped** — regresyon yok, önceki turlarla birebir aynı sonuç. Test
sonrası `git status --short` temiz (state-leak yok).

## Bu turda yeni bulgu
Yok. Tek yeni bilgi, aynı saatlik pencere içinde üç ayrı oturumun aynı
görevi tetiklemiş olmasının somut zaman damgalarıyla teyidi — bu da
zaten bilinen kadans bulgusunun bir örneği, ayrı bir kök neden değil.

## Bildirim kararı
113. tur, iki açık bulguyu (onay kuyruğu bypass, saatlik tetiklenme) ve
sandbox'ta canlı pozisyon verisi olmadığı gerçeğini gerçek bir push
bildirimiyle iletti. Bu turda altta yatan bulgularda bir değişiklik yok
— sadece iki duplicate PR'ın konsolidasyonu ve kadans bulgusunun (üç
oturum/saat) somut bir örneği var — bu yüzden bu round için de ayrı bir
push bildirimi gönderilmedi (114-121. turların "yeni bilgi yoksa
bildirme" politikasıyla tutarlı).

## Sonuç
Kod tabanı sağlıklı (1790/1790, regresyon yok), yeni bir live bug yok.
121. turun iki paralel PR'ı konsolide edildi (#221 merge, #222
duplicate kapatıldı). Açık aksiyon kalemleri (onay kuyruğu/doğrudan emir
çelişkisi çözümü, rutin tetikleyicisinin hesap seviyesinde günlük
aralığa çekilmesi, canlı pozisyon verisinin bu sandbox'a bağlanıp
bağlanmayacağı) değişmeden kullanıcı kararını bekliyor; 113. turda
zaten iletildi.
