# 110. Tur Strateji İncelemesi — 2026-09-21

## Kapsam
Planlı ("her gün stratejini gözden geçir, sermayenin %10'u kadar kazanma
hedefi için gereken kararları al") görevin bu turdaki çalıştırması.

## Durum tespiti
- `git fetch origin main` → HEAD zaten `7578afc` (PR #205 / 109. tur ile
  senkron), açık PR yok, merge conflict yok.
- Tam test paketi (`pytest.ini`'deki 5 testpath: `tests`,
  `calibration/tests`, `execution_realism/tests`, `crypto_directional/tests`,
  `signal_bridge/tests`): **1790 passed, 4 skipped** — 105-109. tur ile
  birebir aynı taban, regresyon yok.
- `pytest` sonrası `git status --short` boş — 108. turda eklenen
  `_isolate_autonomous_engine_state` fixture'ı hâlâ `data/autonomous_state.json`'ı
  koruyor.
- Bu sandbox'ta hâlâ canlı bot örneği yok: `data/status.json`,
  `data/control.json`, `data/positions.json` mevcut değil (yalnızca 2026-03
  tarihli `.bak`/`shadow_journal` durgun verileri var) → %10 sermaye hedefine
  karşı bu turdan da doğrudan ölçülebilir ilerleme sağlanamıyor.

## İki açık bulgu — değişiklik yok
Her ikisi de 106-109. turlarda kullanıcıya bildirildi; bu tur herhangi bir
yanıt veya kod değişikliği gözlemlemedi, dolayısıyla tekrar bildirim
gönderilmedi (aynı, henüz çözülmemiş durum).

1. **Onay kuyruğu / doğrudan emir yolu çelişkisi** (104. turdan beri açık):
   `agents/orchestrator.py` satır 1119/1138 (sinyal yolu) ve 1406/1422
   (approved-queue yolu) hâlâ aynı — AI sinyalleri `is_approved=True` sabit
   değeriyle `approval_queue.enqueue()`'a hiç uğramadan doğrudan
   `client.place_order()`'a gidiyor; `docs/APPROVAL_WORKFLOW_SPEC.md` hâlâ
   "doğrudan emir yolu kapatılmıştır" diyor. Sermaye/güvenlik etkisi olan bir
   mimari tercih olduğu için bu tur da tek taraflı kod değişikliği
   yapılmadı.
2. **Zamanlama sıklığı** (106. turdan beri açık): 109. tur 07:23:17 UTC'de
   merge oldu, bu tur da aynı gün içinde tetiklendi — "her gün" tanımına
   karşı hâlâ daha sık çalışıyor.

## Sonuç
Kod tabanı sağlıklı (1790/1790, regresyon yok), yeni bir canlı bug
bulunmadı. Asıl aksiyon hâlâ kullanıcıda: (a) onay kuyruğu/doğrudan emir
çelişkisinin hangi yönde çözüleceği, (b) zamanlama sıklığının günlüğe
çekilmesi. Bu ikisi zaten bildirildi; bu turda yeni bilgi taşımadığı için
ayrı bir push-notification gönderilmedi.
