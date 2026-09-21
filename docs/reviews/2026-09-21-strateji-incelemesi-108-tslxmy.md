# 108. Tur Strateji İncelemesi — 2026-09-21

## Kapsam
Planlı ("her gün stratejini gözden geçir, sermayenin %10'u kadar kazanma hedefi
için gereken kararları al") görevin bu turdaki çalıştırması.

## Durum tespiti
- `git fetch origin main` → HEAD zaten `65502af` (PR #202 / 107. tur ile
  senkron), açık PR yok.
- Tam test paketi: **1790 passed, 4 skipped** — 105-107. tur ile birebir aynı
  taban, regresyon yok.
- Bu sandbox'ta hâlâ canlı bot örneği yok: `data/status.json`,
  `data/control.json`, `data/positions.json` mevcut değil (yalnızca 2026-03
  tarihli `.bak`/`shadow_journal` durgun verileri var) → %10 sermaye hedefine
  karşı bu turdan doğrudan ölçülebilir ilerleme yine sağlanamıyor.
- **Zamanlama sıklığı sorunu hâlâ sürüyor**: son 24 saatte 52 commit
  (106. tur bunu ilk tespit edip kullanıcıya bildirmişti; 107. tur
  sürdüğünü doğruladı, bu tur da doğruluyor — düzeltilmemiş).

## Yükseltilen bulgu: Onay kuyruğu / doğrudan emir yolu çelişkisi (104. turdan beri açık, ilk kez kullanıcıya bildiriliyor)

104-107. turlar bunu "kullanıcı kararı bekleyen açık mimari soru" olarak not
düşüp geçti ama hiçbiri kullanıcıya bildirim göndermedi (106'nın bildirimi
yalnızca zamanlama sıklığı hakkındaydı). Bu tur kodu doğrudan okuyarak
doğruladı — bu bir varsayım değil, doğrulanmış bir çelişki:

- `docs/APPROVAL_WORKFLOW_SPEC.md`: *"Her canlı emrin dashboard'dan
  onaylanması zorunludur. Doğrudan emir verme yolu kapatılmıştır."*
- `agents/orchestrator.py` `_cycle()` içinde (~satır 1137, yorum: `── DOĞRUDAN
  EMİR VER (onay kuyruğu bypass) ──`): ArbitrageEngine'in ürettiği her sinyal,
  yalnızca otomatik 11-nokta LiveGate kontrolünden geçtikten sonra
  `is_approved=True` **sabit değeriyle** doğrudan `client.place_order()`'a
  gidiyor. `control_plane.approval_queue.enqueue()` fonksiyonu import
  ediliyor (satır 42) ama **hiçbir yerde çağrılmıyor** — yani PENDING
  kuyruğuna hiçbir AI sinyali hiç girmiyor.
- Ayrı bir fonksiyon (`_execute_approved_orders`, satır 1345) gerçekten
  `data/pending_orders.json`'daki APPROVED kayıtları LiveGate'ten geçirip
  execute ediyor — ama bu yol yalnızca dashboard'dan **manuel** girilen
  emirler için kullanılıyor gibi görünüyor (AI sinyalleri hiç oraya
  düşmüyor).

**Sonuç:** Canlı modda (`live_trading=true`) AI sinyalleri, belgelenmiş
güvenlik kontrolünün (her emrin insan onayından geçmesi) aksine, otomatik
LiveGate kontrolleri dışında hiçbir insan onayı olmadan doğrudan
gerçekleştiriliyor. Bu iki şekilde okunabilir:
1. **Spec güncel değil** — bot kasıtlı olarak tam otonom çalışacak şekilde
   tasarlandı (CLAUDE.md'nin "otonom karar motoru" vizyonuyla tutarlı),
   dokümantasyon güncellenmeli.
2. **Kod güvenlik açığı içeriyor** — kullanıcı gerçekten her canlı emrin
   onaydan geçmesini istiyor, mevcut davranış istenmeyen risk taşıyor.

Bu, sermaye/güvenlik etkisi olan ve yalnızca kullanıcının karar
verebileceği bir mimari tercih olduğu için **bu turda kod değişikliği
yapılmadı** — otomatik bir tur, gerçek parayla ilgili bu tür bir
davranış değişikliğini tek taraflı karara bağlayıp sessizce push
etmemeli. Kullanıcıya bu turda ilk kez doğrudan bildirim gönderildi.

## Diğer devreden açık sorular (değişmedi)
(2) `enhanced_signals.py` confluence/risk-flag'e hiç bağlı değil,
(3) `copytrade.py` ölü kod, (4) `top_trader_signal.py`'de `TOP_TRADERS`
listesi kullanılmıyor, (5) `write_readiness_verdict()` manuel-gate sorusu.
Hiçbiri sermaye güvenliğini bu turdaki bulgu kadar doğrudan etkilemiyor,
bu yüzda düşük öncelikli kaldı.

## Sonuç
Kod tabanı sağlıklı (1790/1790). Asıl aksiyon kullanıcıda: (a) zamanlama
sıklığı hâlâ günlük değil ~saatlik/daha sık, (b) onay kuyruğu/doğrudan emir
çelişkisinin hangi yönde çözüleceği (spec'i gevşet mi, kodu sıkılaştır mı).
