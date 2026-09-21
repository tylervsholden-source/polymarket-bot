# 110. Tur Strateji İncelemesi — 2026-09-21

## Kapsam
Planlı ("her gün stratejini gözden geçir, sermayenin %10'u kadar kazanma
hedefi için gereken kararları al") görevin bu turdaki çalıştırması.

## Durum tespiti
- `git fetch origin main` → HEAD zaten `7578afc` (PR #205 / 109. tur ile
  senkron), açık PR yok, kod tabanında 109. turdan bu yana **hiçbir
  değişiklik yok**.
- Tam test paketi (`pytest.ini`'deki 5 testpath: `tests`,
  `calibration/tests`, `execution_realism/tests`, `crypto_directional/tests`,
  `signal_bridge/tests`): **1790 passed, 4 skipped** — 105-109. tur ile
  birebir aynı taban, regresyon yok.
- Test sonrası `git status --short` boş — state izolasyonu (108. turun
  `_isolate_autonomous_engine_state` fixture'ı) hâlâ çalışıyor.
- Bu sandbox'ta hâlâ canlı bot örneği yok: `data/status.json`,
  `data/control.json`, `data/positions.json` mevcut değil (yalnızca 2026-03
  tarihli `.bak`/`shadow_journal` durgun verileri var) → %10 sermaye
  hedefine karşı bu turdan doğrudan ölçülebilir ilerleme yine sağlanamıyor.

## Bug taraması
Kod 109. turdan bu yana değişmediği için tam yeniden tarama yerine önceden
bilinen sapmaların hâlâ geçerli/aynı olduğu doğrulandı:
- `agents/orchestrator.py:262` `MIN_MARKET_VOLUME=10_000` vs CLAUDE.md
  "$5.000" — değişmedi (kod daha katı, güvenlik yönünde, düşük öncelik).
- `strategies/arbitrage_engine.py:468` `VOLUME_GATE_MIN=0.8` vs CLAUDE.md
  OPT-4 "1.2x" — değişmedi (kasıtlı gevşetme, düşük öncelik).

Yeni bir davranış değişikliği veya taze bug bulunmadı.

## Devam eden, kullanıcı kararı bekleyen iki bulgu (değişmedi)

### 1. Onay kuyruğu / doğrudan emir yolu çelişkisi (104. turdan beri açık)
`docs/APPROVAL_WORKFLOW_SPEC.md` hâlâ "her canlı emrin dashboard'dan
onaylanması zorunlu, doğrudan emir yolu kapalı" diyor; `agents/
orchestrator.py` hâlâ AI sinyallerini `is_approved=True` sabit değeriyle
(satır 1119, 1406) `approval_queue.enqueue()`'a hiç uğratmadan doğrudan
`client.place_order()`'a (satır 1138, 1422) gönderiyor. 108. turda
kullanıcıya ilk kez bildirildi, 109 ve 110. turlarda yanıt/kod değişikliği
gözlenmedi. Sermaye/güvenlik etkisi olduğu için bu tur da tek taraflı kod
değişikliği yapılmadı.

### 2. Zamanlama sıklığı (106. turdan beri açık)
Görev "her gün" tanımlı ama tetiklenme aralığı yine saatlerle ölçülüyor:
109. tur 07:17 UTC'de merge oldu, bu tur 10:05 UTC'de başladı (~2s48d
sonra). 106-109. turlar bildirdi; henüz düzeltilmemiş. Yeni bilgi
taşımadığı için bu tur ayrı bir bildirim gönderilmedi.

## Sonuç
Kod tabanı sağlıklı ve 109. turdan bu yana değişmemiş (1790/1790,
regresyon yok). Bu turda yeni bir canlı bug veya doküman sapması
bulunmadı. Asıl aksiyon hâlâ kullanıcıda: (a) onay kuyruğu/doğrudan emir
çelişkisinin hangi yönde çözüleceği, (b) zamanlama sıklığının günlüğe
çekilmesi. İkisi de önceden bildirildi; bu turda yeni bir push-notification
gerektiren gelişme yok.
