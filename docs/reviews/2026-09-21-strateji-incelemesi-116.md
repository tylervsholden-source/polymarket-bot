# 116. Tur Strateji İncelemesi — 2026-09-21

## Kapsam
Planlı ("her gün stratejini gözden geçir, sermayenin %10'u kadar kazanma
hedefi için gereken kararları al") görevin bu turdaki çalıştırması.

## Durum tespiti
- Bu oturumun `git fetch origin main` çıktısı bu sandbox'ta **eski**
  (container oluşturma anına, 18 Eylül'e sabit) — `origin/main` git
  protokolüyle `3355a01` (#145) gösteriyor. Bu, gerçek bir geri alma
  (rollback) gibi görünse de GitHub API'sinden (`list_commits`,
  `pull_request_read`) doğrudan sorgulanınca **yanlış alarm** olduğu
  netleşti: API, `main`'in gerçek tepesinin `371d3d4` (115. tur, PR
  #215, `merged: true`) olduğunu ve bu container'daki mevcut checkout'un
  zaten o commit'te olduğunu doğruluyor. Yani kod kaybı/geri alma yok —
  sadece bu sandbox'ın git-fetch önbelleği bayat. Not: ileriki turlar
  "main neredeyim" sorusunu `git fetch` yerine GitHub API ile
  doğrulamalı; bu ayrım bu turda ilk kez açıkça test edilip kayda
  geçirildi.
- Açık PR yok (`list_pull_requests state=open` → boş liste).
- Bu sandbox'ta hâlâ canlı bot örneği yok: `data/control.json`,
  `data/status.json`, `data/positions.json` mevcut değil → %10 sermaye
  hedefine karşı bu turdan da doğrudan ölçülebilir ilerleme
  sağlanamıyor (106. turdan beri değişmeyen durum).
- `CronList` yine "No scheduled jobs" döndürdü — zamanlama sıklığı
  bulgusunun (rutin hesap seviyesinde ayarlı, session içinden
  değiştirilemez) doğrulaması tekrarlandı.

## Bug taraması
Kod 115. turdan bu yana bayt bayt aynı. İki bilinen bulgu kaynak koddan
yeniden doğrulandı, ikisi de değişmemiş:

1. **Onay kuyruğu / doğrudan emir yolu çelişkisi** (104. turdan beri
   açık): `agents/orchestrator.py:42` hâlâ `_enqueue_order`'ı import
   edip başka hiç çağırmıyor; canlı `_cycle` sinyal döngüsü (satır 1119)
   ve onay-sonrası yürütme yolu (satır 1406) hâlâ `is_approved=True`
   sabitiyle doğrudan `client.place_order()`'a gidiyor —
   `docs/APPROVAL_WORKFLOW_SPEC.md`'nin "doğrudan emir yolu kapalı"
   ifadesiyle çelişmeye devam ediyor. Sermaye/güvenlik etkisi nedeniyle
   bu tur da tek taraflı kod değişikliği yapılmadı.
2. **Zamanlama sıklığı** (106. turdan beri açık): rutin hâlâ "günlük"
   yerine yaklaşık saatlik aralıklarla tetikleniyor; hesap seviyesinde
   bir ayar, bu oturumdan değiştirilemiyor.

Tam test paketi bağımsız olarak sıfırdan kuruldu (`pip install -r
requirements.txt`) ve çalıştırıldı: **1790 passed, 4 skipped** —
regresyon yok. Test sonrası `git status --short` temiz (state-leak yok).

## Bu turda yeni bulgu
Yok. Yeni olan tek şey, git-fetch önbelleği ile GitHub API arasındaki
tutarsızlığın tespit edilip yanlış alarm olarak elenmesi — koddaki veya
bulgulardaki durumu değiştirmiyor.

## Bildirim kararı
113. tur, iki açık bulguyu (onay kuyruğu bypass, saatlik tetiklenme) ve
sandbox'ta canlı pozisyon verisi olmadığı gerçeğini gerçek bir push
bildirimiyle iletti. Bu turda ne kodda ne bulgularda ne de %10 hedefine
karşı ölçülebilir durumda bir değişiklik var — git-fetch/API
tutarsızlığı araştırması bir yanlış alarmın elenmesinden ibaret ve
kullanıcı için aksiyon gerektirmiyor. Bu nedenle bu round için de ayrı
bir push bildirimi gönderilmedi (114-115. turların "yeni bilgi yoksa
bildirme" politikasıyla tutarlı).

## Sonuç
Kod tabanı sağlıklı (1790/1790, regresyon yok), yeni bir live bug yok.
Açık aksiyon kalemleri (onay kuyruğu/doğrudan emir çelişkisi çözümü,
rutin tetikleyicisinin hesap seviyesinde günlük aralığa çekilmesi, canlı
pozisyon verisinin bu sandbox'a bağlanıp bağlanmayacağı) değişmeden
kullanıcı kararını bekliyor; 113. turda zaten iletildi.
