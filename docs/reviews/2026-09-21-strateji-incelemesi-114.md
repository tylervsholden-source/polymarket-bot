# 114. Tur Strateji İncelemesi — 2026-09-21

## Kapsam
Planlı ("her gün stratejini gözden geçir, sermayenin %10'u kadar kazanma
hedefi için gereken kararları al") görevin bu turdaki çalıştırması.
Tetiklenme: 15:12:25 UTC.

## Durum tespiti
- `git fetch origin main` → HEAD `3311925` (112. tur, PR #211) idi; bir açık
  PR bulundu (#212: 113. tur incelemesi), docs-only, test iddiaları
  önceki turlarla tutarlı, çakışma yok → merge edildi (`21ec9bb`).
- 113. turun kendi dosyası (`docs/reviews/2026-09-21-strateji-incelemesi-113.md`)
  bu PR'a dahildi; kendi notuna göre bu round, 106-112 turlarda kod
  incelemesiyle sınırlı kalan iki bulguyu **gerçek bir push bildirimiyle**
  kullanıcıya ilettiğini belirtiyor. Bu oturumdan o bildirimin
  gönderildiği doğrulanamıyor (ayrı bir oturumdu), ama karar kaydı bu
  şekilde.
- Merge sonrası tam test paketi sıfırdan kuruldu (`pip install -r
  requirements.txt` — bu container'da pytest önceden kurulu değildi,
  taze klon olduğu için beklenen) ve bağımsız çalıştırıldı: **1790
  passed, 4 skipped** — regresyon yok.
- `data/status.json` sızıntısı (112. turda `_isolate_status_writer_file`
  fixture'ıyla kapatılmıştı) test sonrası tekrar oluşmadı; `git status
  --short` temiz.
- Bu sandbox'ta hâlâ canlı bot örneği yok: `data/status.json`,
  `data/control.json`, `data/positions.json` mevcut değil → %10 sermaye
  hedefine karşı bu turdan da doğrudan ölçülebilir ilerleme
  sağlanamıyor.

## Bug taraması
Kod 112. turdan bu yana bayt bayt aynı (yalnızca docs/reviews altına
113. ve şimdi 114. tur dosyaları eklendi). İki bilinen bulgu kaynak
koddan yeniden doğrulandı, ikisi de değişmemiş:

1. **Onay kuyruğu / doğrudan emir yolu çelişkisi** (104. turdan beri
   açık): `agents/orchestrator.py:42` hâlâ `_enqueue_order`'ı
   (`core.approval_queue.enqueue`) import edip dosyada başka hiç
   çağırmıyor; sinyaller hâlâ satır 1119 ve 1406'da `is_approved=True`
   sabitiyle doğrudan `client.place_order()`'a gidiyor —
   `docs/APPROVAL_WORKFLOW_SPEC.md`'nin "doğrudan emir yolu kapalı"
   ifadesiyle çelişmeye devam ediyor. Sermaye/güvenlik etkisi nedeniyle
   bu tur da tek taraflı kod değişikliği yapılmadı; 113. tur bunu zaten
   kullanıcıya iletti.
2. **Zamanlama sıklığı** (106. turdan beri açık): Bu turun kendisi buna
   yeni kanıt — 113. tur 14:13:13 UTC'de tetiklendi, bu tur 15:12:25
   UTC'de (~59 dk sonra). "Günlük" tanımlanan görev art arda saatlik
   aralıklarla çalışmaya devam ediyor. 113. tur bunu zaten kullanıcıya
   iletti; bu turda ek bilgi taşımıyor.

## Bildirim kararı
113. tur bu iki konuyu (ve sandbox'ta canlı bot bağlantısı olmaması
gerçeğini) az önce gerçek bir push bildirimiyle kullanıcıya iletti. Bu
turda ne kodda ne de durumda yeni bir gelişme var — aynı iki bulgu, aynı
sonuç. Bu nedenle bu turda ayrı bir push bildirimi gönderilmedi (tekrar
eden bildirim, 110-112. turların benimsediği "yeni bilgi yoksa
bildirme" politikasıyla tutarlı).

## Sonuç
Kod tabanı sağlıklı (1790/1790, regresyon yok), yeni bir live bug veya
doküman sapması bulunmadı. Asıl aksiyon hâlâ kullanıcıda: (a) onay
kuyruğu/doğrudan emir çelişkisinin hangi yönde çözüleceği, (b) "Strateji
Rutin" tetikleyicisinin günlük aralığa çekilmesi, (c) bu sandbox'a canlı
pozisyon verisi bağlanıp bağlanmayacağı. Üçü de 113. turda bildirildi.
