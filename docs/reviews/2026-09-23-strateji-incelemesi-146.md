# 146. Tur Strateji İncelemesi — 2026-09-23

## Kapsam
Planlı ("her gün stratejimizi gözden geçirsin. Amacımız mevcut kapitalimizin
yüzde 10'u kadar kazanmak. Bunun için gereken tüm kararları alabilir ve
uygulayabilirsin") görevin bu turdaki çalıştırması. Tetiklenme: ~07:09 UTC
(2026-09-23). Bu turun talimatı önceki turlardan farklı: kullanıcı bu sefer
açıkça "gereken tüm kararları alabilir ve uygulayabilirsin" diyerek karar
alma yetkisini genişletti.

## Durum tespiti
- Bu tur başladığında `main` üzerinde açık iki PR vardı: #245 (round-144,
  zaten kod tabanında merge edilmiş görünüyordu) ve **#246 (round-145,
  `claude/brave-faraday-7fney7` dalından, eşzamanlı başka bir oturum
  tarafından ~06:12 UTC'de açılmış)**. Saatlik/düzensiz tetikleme hatası
  (106. turdan beri bilinen) bu turda da somut sonucunu gösterdi: iki
  oturum aynı "round" için paralel çalıştı. #246'nın içeriği doğrudan
  okundu (`get_diff`): tek değişiklik round-145 review dosyasıydı, kod
  değişikliği yoktu, ve o turun kendi tespiti de aynı onay-kuyruğu
  bulgusunu (42. kez) doğrulayıp yine bildirim göndermeden bekletmişti.
  #246'nın dalı bu dala merge edildi (bu commit'ten önceki merge commit).

## Bu turda alınan karar: onay kuyruğu güvenlik açığı düzeltildi
104. turdan beri (şimdi 42+ turdur) her round bağımsız olarak aynı bulguyu
doğruluyordu ama hiçbiri düzeltmiyordu — çünkü standart günlük görev
talimatı yalnızca "gözden geçir ve bildir" yetkisi veriyordu, canlı emir
mantığını değiştirme yetkisi vermiyordu (round-145'in kendi sonucu: "karar
kullanıcıda"). **Bu turun talimatı farklı** — kullanıcı açıkça "gereken tüm
kararları alabilir ve uygulayabilirsin" dedi. Bu nedenle bu turda düzeltme
uygulandı, sadece rapor edilmedi.

### Bulgunun doğrulanması (kaynaktan, tekrar)
- `agents/orchestrator.py`: `check_live_gate(...)` çağrısına sabit
  `is_approved=True` geçiliyordu (LiveGate'in kendi 10. kontrolü —
  "Emir onay kuyruğunda onaylanmış" — hep geçiyordu).
- Hemen ardından `self.client.place_order(...)` **doğrudan** çağrılıyordu;
  `core.approval_queue.enqueue()` (zaten import edilmiş, `_enqueue_order`
  adıyla) hiçbir yerde çağrılmıyordu.
- `control_plane/approval_queue.py` başlığı: *"INC-2026-03-15-001 dersi:
  Sinyal → emir arasında insan onayı ZORUNLU."*
- `incident_bundle_v2/agents/orchestrator.py` (orijinal incident-sonrası
  düzeltmenin saklı kopyası) aynı noktada `_enqueue_order({...})`
  çağırıyor ve doğrudan `place_order()` çağırmıyor — yani mevcut kod bu
  incident'ten sonra **regresyona uğramış**, muhtemelen "v3 Otonom
  Multi-Agent Orchestration" refactor'ında (CLAUDE.md, Mart 2026).
- Onay altyapısı zaten tam ve test edilmiş durumda: `control_plane/
  approval_queue.py` (state machine), `core/web_server.py`
  (`/api/pending/approve`, `/api/pending/reject` endpoint'leri, zaten
  bağlı), ve `Orchestrator._execute_approved_orders()` (onaylanan emirleri
  gerçekten yürüten, kendi LiveGate re-check'ini zaten doğru yapan metod)
  — hiçbiri değiştirilmedi, sadece sinyal üretiminin bu altyapıya
  bağlanması eksikti.

### Uygulanan düzeltme
`agents/orchestrator.py`, doğrudan emir bloğu (`# DOĞRUDAN EMİR VER (onay
kuyruğu bypass)`) kaldırıldı, yerine `_enqueue_order({...})` çağrısı
kondu — `incident_bundle_v2`'deki orijinal düzeltmeyle aynı desen.
Farklar/ek önlemler:
- Enqueue öncesi LiveGate ön-kontrolü (capital/rate-limit/expiry/entry-
  window/...) korundu — `is_approved=True` burada sadece o 10 kontrolü
  ön-elemek için, gerçek onay değil (yorumla belgelendi). Gerçek
  `is_approved` kontrolü zaten `_execute_approved_orders()`'da yapılıyor.
- Aynı cycle'da art arda enqueue nedeniyle `max_open_positions`/
  `MAX_DIRECTIONAL`/`cycle_budget` limitlerinin aşılmaması için
  `open_count`/`directional_count`/`cycle_spent` sayaçları enqueue
  sonrası yine artırılıyor (gerçek `capital` artırılmıyor — gerçek
  harcama yalnızca onaydan sonra gerçekleşir).
- `position_manager.add_position()` ve `reentry_guard.mark_traded()`
  artık bu noktada çağrılmıyor (henüz gerçek pozisyon yok) — bunlar zaten
  `_execute_approved_orders()` içinde onaydan sonra doğru şekilde
  çağrılıyor.

### Test güncellemesi
`tests/test_capital_decrement_uses_real_order_cost.py`:
`test_direct_order_path_decrements_by_real_order_amount` eski (hatalı)
davranışı doğruluyordu (`real_cost = order.get(...)` doğrudan yolda
olmalı diye assert ediyordu) — bu artık geçerli değil çünkü doğrudan yol
artık gerçek emir vermiyor. Test `test_direct_order_path_no_longer_
places_real_orders` olarak yeniden yazıldı: canlı dalda `_enqueue_order(`
çağrısı VE `self.client.place_order(` çağrısının YOKLUĞU doğrulanıyor.
`test_approval_queue_path_decrements_by_real_order_amount` (asıl
`_execute_approved_orders()` yolunun gerçek-maliyet düşümü) değişmeden
kaldı ve hâlâ geçiyor.

### Doğrulama
`pip install -r requirements.txt -q` + `python3 -m pytest -q`:
**1790 passed, 4 skipped, 1 warning** — regresyon yok (önceki turlarla
aynı sayı, artı düzeltilen test).

### Davranış değişikliği — kullanıcının bilmesi gereken
Bu, canlı botun emir verme davranışını **kökten değiştiriyor**: sinyaller
artık doğrudan emre dönüşmüyor, dashboard'dan operatör onayı bekleyen bir
kuyruğa giriyor (varsayılan zaman aşımı: 300 saniye,
`control_plane/approval_queue.py:DEFAULT_TIMEOUT_SEC`). Biri düzenli
olarak dashboard'u (`core/web_server.py` — `/api/pending/approve`)
kullanmazsa, hiçbir emir 5 dakika içinde onaylanmadığı için EXPIRED olur
ve bot fiilen sinyal üretmeye devam etse de **hiç canlı emir vermez**. Bu,
CLAUDE.md'nin "Otonom Karar Akışı" (insan müdahalesi olmadan sürekli
çalışma) hedefiyle gerilim içinde ama INC-2026-03-15-001'in "insan onayı
ZORUNLU" dersiyle ve 42 turdur tekrarlanan güvenlik bulgusuyla birebir
uyumlu. Kullanıcının ya (a) dashboard'u aktif izlemesi ya da (b) timeout'u
uzatması/otomatik-onay politikası eklemesi gerekebilir — bu kararı bu
oturum almadı, çünkü trading stratejisinin kendisinden çok operasyonel bir
tercih ve kullanıcının gerçek ortamındaki (Windows/Antigravity IDE)
kullanım şekline bağlı.

## Diğer bilinen bulgu
**Zamanlama sıklığı** (106. turdan beri açık, bu turda da somut kanıtla
—iki eşzamanlı PR— **26. kez** doğrulandı): hesap seviyesinde bir ayar,
`CronList` bu oturumda da "No scheduled jobs" döndürdü, bu oturumdan
değiştirilemiyor.

## Canlı sermaye / pozisyon durumu
`data/positions.json`, `data/control.json`, `data/status.json` bu bulut
oturumunda yok. `data/3day_eval.txt` (bu sandbox'ta bulunan, muhtemelen
kullanıcının ortamından senkronize edilmiş en son gerçek sonuç dosyası):
son 3 gün / 44 trade, gerçek PnL **+$1.01** (52.3% WR) — "%10 kazanma"
hedefinden uzak, pratikte breakeven. `data/trade_patterns.json`:
`LOW_EDGE_LOSS` deseni 1008 tekrar, -$2548 toplam PnL (muhtemelen eski
sim verisi, tarih damgası yok) — canlı performansla karıştırılmamalı ama
strateji kalitesi hakkında ayrı bir endişe kaynağı.

## Sonuç ve bildirim kararı
Bu tur, 42 turdur bekleyen kritik bir canlı-güvenlik bulgusunu **düzeltti
ve pushladı** (önceki turların hepsi yalnızca rapor etmişti). Bu hem
büyüklüğü (canlı emir verme davranışını değiştiriyor) hem de kullanıcının
aktif dashboard kullanımı gerektirme olasılığı nedeniyle kullanıcıya
bildirilmesi gereken bir sonuç — push bildirimi gönderildi.
