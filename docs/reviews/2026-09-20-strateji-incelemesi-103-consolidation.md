# Günlük Strateji İncelemesi — 2026-09-20 (103. tur konsolidasyonu)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = `330ffaa` (102. turun konsolidasyonu, #180-182
merge edilmiş) idi. Üç paralel otomatik inceleme oturumu aynı taban commit
üzerinden bağımsız olarak "103. tur" etiketiyle üç ayrı PR açmıştı:

- **PR #184** — kod değişikliği yok, sadece inceleme belgesi
  (`docs/reviews/2026-09-20-strateji-incelemesi-103.md`): `core/web_server.py`
  ve `scripts/` satır satır tarandı, canlı karara giren bir hata bulunamadı.
- **PR #185** — `core/web_server.py::_send_status()`: `position_meta.json`
  merge'i `positions.json` override'ı tarafından sessizce eziliyordu (bot
  normal çalışırken merge hiç görünmüyordu). Şu an ölü/etkisiz bir yol
  (front-end bu alanları okumuyor, hiçbir yer `position_meta.json` yazmıyor)
  ama gerçek bir mantık hatası olduğu için düzeltildi.
- **PR #186** — `agents/orchestrator.py`: sim/paper modda `_cycle()`'ın
  "TOPLAM RİSK LİMİTİ" bloğu, Kelly/sinyal boyutlandırmasında kullanılan
  SIM_CAPITAL floor'unu risk-tavanı hesabına uygulamıyordu; gerçek CLOB
  bakiyesi küçükken (örn. $0.75) tavan ~133x daha sıkı hesaplanıyor ve bir
  sim trade'den sonra tam $0'a çöküp o pozisyon çözülene kadar (5-45dk) tüm
  yönlü sim trade'leri engelliyordu.

Konteynerde çalışan bir bot instance'ı yok (`data/status.json`/
`control.json`/`positions.json` bu sandbox'ta yok, dışarıya CLOB/Polymarket
API erişimi de yok) — %10 hedefine karşı gerçek zamanlı sermaye ilerlemesi
bu oturumdan doğrulanamıyor. Bu turun katkısı kod/strateji doğruluğu
seviyesinde: üç bağımsız oturumun bulduğu, örtüşmeyen iki gerçek canlı-karar
hatasının doğrulanıp main'e alınması.

## Yapılan doğrulama

1. Üç PR'ın diff'i tek tek okundu.
2. **Çakışma tespiti**: #184 ve #185, aynı dosya yoluna
   (`docs/reviews/2026-09-20-strateji-incelemesi-103.md`) farklı içerikle
   ekleme yapıyordu — gerçek bir git çakışması. Bu repodaki yerleşik
   `102`/`102b`/`102c` adlandırma deseni izlenerek #185'in branch'ine
   (`claude/brave-faraday-1tvm3c`) küçük bir düzeltme commit'i push edildi:
   dosya `2026-09-20-strateji-incelemesi-103b.md` olarak yeniden adlandırıldı.
3. `origin/main` üzerinde geçici bir worktree açılıp üç branch de
   (`-ibv7fq`, `-1tvm3c` [yeniden adlandırma sonrası], `-en3474`) octopus
   merge ile birleştirildi — **hiç çakışma yok**.
4. Birleşik ağaçta tam suite çalıştırıldı:
   `pytest tests/ calibration/tests execution_realism/tests
   signal_bridge/tests crypto_directional/tests -q`
   → **1773 passed, 4 skipped** (baseline 1769 + 2 (#185) + 2 (#186) yeni
   test — beklenen aritmetik, 0 regresyon).
5. Üç PR de GitHub üzerinden normal `merge` yöntemiyle main'e alındı:
   #184 → `73c3486`, #185 (yeniden adlandırma dahil) → `1c05576`,
   #186 → `f47c5cc`.
6. Merge sonrası `origin/main` yeniden fetch edilip tam suite tekrar
   çalıştırıldı: **1773 passed, 4 skipped** — merge commit'lerinin kendisi
   hiçbir regresyon getirmedi. İki review dosyası da (`103.md`, `103b.md`)
   çakışmadan main'de duruyor.

## Sonuç
101./102. tur konsolidasyonlarında kurulan desenle aynı: aynı gün paralel
çalışan bağımsız oturumların ürettiği, doğrulanmış düzeltmeler tek tek
main'e alındı. Bu turda ek olarak gerçek bir dosya-yolu çakışması vardı
(iki oturum aynı review dosyasını farklı adla oluşturdu) — repo'nun kendi
`102b`/`102c` konvansiyonu uygulanarak çözüldü.

## Sıradaki tur için notlar (PR #185'ten taşınıyor)
- **Onay kuyruğu / doğrudan emir yolu çelişkisi**: `agents/orchestrator.py`
  ~1126 satırındaki "DOĞRUDAN EMİR VER (onay kuyruğu bypass)" yolu, `docs/
  APPROVAL_WORKFLOW_SPEC.md`'nin "Doğrudan emir verme yolu kapatılmıştır"
  ifadesiyle doğrudan çelişiyor. Muhtemelen kasıtlı bir tasarım tercihi
  (edge 30-60sn'de eriyor, dashboard onayı bunu karşılayamaz) ama kullanıcı
  onayı olmadan otonom olarak değiştirilmedi — bu bir mimari karar,
  kullanıcıya sorulmalı.
- `core/web_server.py` ve `scripts/` artık tamamen taranmış ve temiz.
- Taranmamış adaylar: `shadow_runner/{runner,replay,reporting,validation}.py`,
  `calibration/{calibrator,probability_mapper,edge_estimator}.py`,
  `agents/subagents/{research_agent,reviewer_agent}.py`.
- `control_plane/approval_queue.py`'nin `enqueue()`'ı hâlâ hiçbir yerden
  çağrılmıyor (canlı kodda inert) ve `strategies/quality_filter.py` hâlâ
  hiç import edilmiyor — ikisi de düzeltme gerektirmiyor, tekrar "yeni
  keşif" olarak raporlanmamalı.
