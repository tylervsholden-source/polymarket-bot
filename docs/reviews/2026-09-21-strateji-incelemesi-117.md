# 117. Tur Strateji İncelemesi — 2026-09-21

## Kapsam
Planlı ("her gün stratejini gözden geçir, sermayenin %10'u kadar kazanma
hedefi için gereken kararları al") görevin bu turdaki çalıştırması.

## Durum tespiti
- GitHub API ile doğrulandı (`list_commits` sha=main, `list_pull_requests`
  state=open): `main` tepesi `5b27915` (115. tur PR #216 merge commit'i,
  116. tur incelemesini içeriyor). Bu sandbox'ın yerel `HEAD`'i de
  `5b27915` — birebir aynı, git-fetch/API arasında tutarsızlık yok (116.
  turda tespit edilen önbellek sorunu bu turda tekrarlanmadı).
- Açık PR yok (`list_pull_requests state=open` → boş liste).
- `ab345fc` (116. tur review commit'i) ile şu anki `HEAD` arasında
  `git diff --stat` boş — kod tabanı 116. turdan bu yana bayt bayt aynı.
- Bu sandbox'ta hâlâ canlı bot örneği yok: `data/control.json`,
  `data/status.json`, `data/positions.json` mevcut değil → %10 sermaye
  hedefine karşı bu turdan da doğrudan ölçülebilir ilerleme
  sağlanamıyor (106. turdan beri değişmeyen, 113. turda kullanıcıya
  iletilmiş durum).

## Bug taraması
İki bilinen bulgu kaynak koddan yeniden doğrulandı, ikisi de değişmemiş:

1. **Onay kuyruğu / doğrudan emir yolu çelişkisi** (104. turdan beri
   açık): `agents/orchestrator.py:42` hâlâ `_enqueue_order`'ı import
   edip başka hiç çağırmıyor; canlı `_cycle` sinyal döngüsü (satır 1119)
   ve onay-sonrası yürütme yolu (satır 1406) hâlâ `is_approved=True`
   sabitiyle doğrudan `client.place_order()`'a gidiyor (satır 1138,
   1422) — `docs/APPROVAL_WORKFLOW_SPEC.md`'nin "doğrudan emir yolu
   kapalı" ifadesiyle çelişmeye devam ediyor. Sermaye/güvenlik etkisi
   nedeniyle bu tur da tek taraflı kod değişikliği yapılmadı.
2. **Zamanlama sıklığı** (106. turdan beri açık): rutin hâlâ "günlük"
   yerine yaklaşık saatlik aralıklarla tetikleniyor (116. ve 117. tur
   arası ~1 saat); hesap seviyesinde bir ayar, bu oturumdan
   değiştirilemiyor.

Tam test paketi kökten çalıştırıldı (`pip install -r requirements.txt`
sonrası `pytest` — `testpaths` içindeki `tests/`, `calibration/tests/`,
`execution_realism/tests/`, `crypto_directional/tests/`,
`signal_bridge/tests/` dahil): **1790 passed, 4 skipped** — regresyon
yok, 116. turla birebir aynı sonuç. Test sonrası `git status --short`
temiz (state-leak yok).

## Bu turda yeni bulgu
Yok. Kod, açık bulgular ve test sonuçları 116. turla birebir aynı.

## Bildirim kararı
113. tur, iki açık bulguyu (onay kuyruğu bypass, saatlik tetiklenme) ve
sandbox'ta canlı pozisyon verisi olmadığı gerçeğini gerçek bir push
bildirimiyle iletti. Bu turda ne kodda ne bulgularda ne de %10 hedefine
karşı ölçülebilir durumda bir değişiklik var, bu yüzden bu round için de
ayrı bir push bildirimi gönderilmedi (114-116. turların "yeni bilgi
yoksa bildirme" politikasıyla tutarlı).

## Sonuç
Kod tabanı sağlıklı (1790/1790, regresyon yok), yeni bir live bug yok.
Açık aksiyon kalemleri (onay kuyruğu/doğrudan emir çelişkisi çözümü,
rutin tetikleyicisinin hesap seviyesinde günlük aralığa çekilmesi, canlı
pozisyon verisinin bu sandbox'a bağlanıp bağlanmayacağı) değişmeden
kullanıcı kararını bekliyor; 113. turda zaten iletildi.
