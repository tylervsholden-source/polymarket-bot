# 121. Tur Strateji İncelemesi — 2026-09-22

## Kapsam
Planlı ("her gün stratejini gözden geçir, sermayenin %10'u kadar kazanma
hedefi için gereken kararları al") görevin bu turdaki çalıştırması.

## Durum tespiti
- GitHub API ile doğrulandı (`list_pull_requests` state=open): açık PR
  yok. Yerel `HEAD` (`35c511a`) `origin/main` ile birebir eşit — 120.
  tur PR'ı (#220) zaten merge edilmiş, bu turda üzerine düşen bir merge
  işi yok.
- Bu sandbox'ta hâlâ canlı bot örneği yok: `data/control.json`,
  `data/status.json`, `data/positions.json` mevcut değil → %10 sermaye
  hedefine karşı bu turdan da doğrudan ölçülebilir ilerleme
  sağlanamıyor (106. turdan beri değişmeyen, 113. turda kullanıcıya
  iletilmiş durum).
- Kadans: 120. tur commit'i `2026-09-22T03:06:52Z`, bu tur `~04:03Z`
  başladı — fark ~57 dakika. 106. turdan beri açık olan "günlük yerine
  saatlik tetikleniyor" bulgusu bu turda da doğrulandı.

## Sandbox ortam notu (yeni gözlem, kod değişikliği gerektirmiyor)
Bu turda `pytest` komutu doğrudan çalıştırıldığında (`pip install -r
requirements.txt` sonrası) 114 collection hatası verdi
(`ModuleNotFoundError: No module named 'loguru'` vb.). Kök neden kod
tabanında değil: bu sandbox'ta `pytest` komutu `/root/.local/bin/pytest`
üzerinden izole bir `uv tool` ortamına (`/root/.local/share/uv/tools/
pytest/...`) çözümleniyor ve proje bağımlılıklarını görmüyor.
`python3 -m pytest` ile (proje bağımlılıklarının kurulu olduğu
`/usr/local/lib/python3.11/dist-packages` yorumlayıcısı) çalıştırıldığında
sonuç önceki turlarla birebir aynı: **1790 passed, 4 skipped**. Bu, bu
oturuma özgü bir PATH/araç kurulumu tuhaflığı — `requirements.txt` veya
kod tabanında bir eksiklik değil, dolayısıyla düzeltme gerektirmiyor;
sadece gelecek turlar için not: test paketini `python3 -m pytest` ile
çalıştırmak, çıplak `pytest` komutundan daha güvenilir.

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
2. **Zamanlama sıklığı** (106. turdan beri açık, yukarıda tekrar
   doğrulandı): rutin hâlâ "günlük" yerine saatlik mertebede
   tetikleniyor; hesap seviyesinde bir ayar, bu oturumdan
   değiştirilemiyor.

`python3 -m pytest` ile tam test paketi çalıştırıldı (`tests/`,
`calibration/tests/`, `execution_realism/tests/`,
`crypto_directional/tests/`, `signal_bridge/tests/` dahil):
**1790 passed, 4 skipped** — regresyon yok, önceki turlarla birebir aynı
sonuç. Test sonrası `git status --short` temiz (state-leak yok).

## Bu turda yeni bulgu
Kodda yok. Sadece bu sandbox'a özgü, kod dışı bir test-çalıştırma notu
eklendi (yukarıya bakın) — düzeltme gerektirmiyor, ilerideki turlar için
bilgilendirme amaçlı.

## Bildirim kararı
113. tur, iki açık bulguyu (onay kuyruğu bypass, saatlik tetiklenme) ve
sandbox'ta canlı pozisyon verisi olmadığı gerçeğini gerçek bir push
bildirimiyle iletti. Bu turda altta yatan bulgularda bir değişiklik yok
ve yeni gözlem kod tabanını etkilemiyor (sadece test komutu seçimiyle
ilgili), bu yüzden bu round için de ayrı bir push bildirimi
gönderilmedi (114-120. turların "yeni bilgi yoksa bildirme" politikasıyla
tutarlı).

## Sonuç
Kod tabanı sağlıklı (1790/1790, regresyon yok), yeni bir live bug yok.
Açık aksiyon kalemleri (onay kuyruğu/doğrudan emir çelişkisi çözümü,
rutin tetikleyicisinin hesap seviyesinde günlük aralığa çekilmesi, canlı
pozisyon verisinin bu sandbox'a bağlanıp bağlanmayacağı) değişmeden
kullanıcı kararını bekliyor; 113. turda zaten iletildi.
