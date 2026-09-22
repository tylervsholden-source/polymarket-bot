# 123. Tur Strateji İncelemesi — 2026-09-22

## Kapsam
Planlı ("her gün stratejini gözden geçir, sermayenin %10'u kadar kazanma
hedefi için gereken kararları al") görevin bu turdaki çalıştırması.

## Durum tespiti
- Bu tur başladığında (07:11Z) açık bir PR vardı: #223 ("122. tur"),
  06:19Z'de başka bir oturum tarafından oluşturulmuş, `mergeable_state:
  clean`, 0 CI check (repoda CI workflow yok, önceki turlarla tutarlı).
  İçeriği bağımsız doğrulandı (bkz. aşağı) ve merge edildi (`d43f368`).
  Yerel `claude/brave-faraday-a58b1a` dalı `origin/main`'e sıfırlandı
  (`git checkout -B ... origin/main`) — dalda zaten sadece merge edilmiş
  geçmiş vardı, kayıp iş yok.
- Kadans: 122. tur PR'ı 06:19Z oluşturuldu, bu tur 07:11Z başladı — fark
  ~52 dakika. 106. turdan beri açık olan "günlük yerine saatlik
  tetikleniyor" bulgusu bu turda da (üçüncü kez art arda ~1 saatlik
  aralıkla) doğrulandı.

## Bug taraması
İki bilinen bulgu kaynak koddan yeniden doğrulandı (`agents/orchestrator.py`),
ikisi de değişmemiş:

1. **Onay kuyruğu / doğrudan emir yolu çelişkisi** (104. turdan beri açık):
   satır 42'de `_enqueue_order` import ediliyor ama dosyada hiçbir yerde
   çağrılmıyor (`grep '_enqueue_order(' agents/orchestrator.py` → 0 sonuç);
   canlı sinyal döngüsü (satır 1119) ve onay-sonrası yürütme yolu (satır
   1406) hâlâ `is_approved=True` ile doğrudan yürütmeye gidiyor. Sermaye/
   güvenlik etkisi nedeniyle bu tur da tek taraflı kod değişikliği
   yapılmadı — kullanıcı kararı bekleniyor (113. turda iletildi).
2. **Zamanlama sıklığı** (106. turdan beri açık, yukarıda tekrar
   doğrulandı).
- Sandbox'ta hâlâ canlı bot örneği yok: `data/control.json`,
  `data/status.json`, `data/positions.json` gitignore'lu ve mevcut değil
  (`data/` altındaki `positions_backup.json` vb. dosyalar Mart ayından
  kalma test fixture'ları, canlı durum değil — son değişiklikleri #141
  PR'ından, 18 Eylül). %10 sermaye hedefine karşı bu turdan da doğrudan
  ölçülebilir ilerleme sağlanamadı.

`python3 -m pip install -r requirements.txt && python3 -m pytest` ile tam
paket çalıştırıldı: **1790 passed, 4 skipped** — regresyon yok, önceki
turlarla birebir aynı. Test sonrası `git status --short` temiz.

## Bu turda yeni bulgu
Yok. #223'ün (122. tur) tespitleri bağımsız olarak yeniden doğrulandı ve
merge edilerek konsolide edildi.

## Bildirim kararı
Bulgularda bir değişiklik yok; kullanıcıya 113. turda zaten iletildi ve
114-122. turlarda "yeni bilgi yoksa bildirme" politikasıyla tutarlı şekilde
tekrar bildirilmedi. Bu tur da aynı politika uygulandı — ayrı bir push
bildirimi gönderilmedi.

## Sonuç
Kod tabanı sağlıklı (1790/1790, regresyon yok), yeni bir live bug yok.
Açık aksiyon kalemleri (onay kuyruğu/doğrudan emir çelişkisi çözümü, rutin
tetikleyicisinin hesap seviyesinde günlük aralığa çekilmesi, canlı pozisyon
verisinin bu sandbox'a bağlanıp bağlanmayacağı) değişmeden kullanıcı
kararını bekliyor.
