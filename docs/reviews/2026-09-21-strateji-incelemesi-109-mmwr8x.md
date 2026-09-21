# 109. Tur Strateji İncelemesi — 2026-09-21

## Kapsam
Planlı ("her gün stratejini gözden geçir, sermayenin %10'u kadar kazanma
hedefi için gereken kararları al") görevin bu turdaki çalıştırması.

## Durum tespiti
- `git fetch origin main` → HEAD zaten `dd501a8` (PR #203 / 108. tur ile
  senkron), açık PR yok.
- Tam test paketi (`pytest`, `pytest.ini`'deki 5 testpaths: `tests`,
  `calibration/tests`, `execution_realism/tests`, `crypto_directional/tests`,
  `signal_bridge/tests`): **1790 passed, 4 skipped** — 105-108. tur ile
  birebir aynı taban, regresyon yok.
  (Not: yalnızca `pytest tests/` çalıştırmak 952 test görür; diğer 4
  testpath'i atlar — bu tur bunu fark edip tam komutla düzeltti.)
- `pytest` sonrası `git status --short` boş — 108. turda eklenen
  `_isolate_autonomous_engine_state` fixture'ı `data/autonomous_state.json`'ı
  koruyor, tekrar kirlenme yok.
- Bu sandbox'ta hâlâ canlı bot örneği yok: `data/status.json`,
  `data/control.json`, `data/positions.json` mevcut değil (yalnızca 2026-03
  tarihli `.bak`/`shadow_journal` durgun verileri var) → %10 sermaye hedefine
  karşı bu turdan doğrudan ölçülebilir ilerleme yine sağlanamıyor.

## Bug taraması (arka plan ajanı)
Dokümante edilen sayısal eşikler (max pozisyon %20, günlük stop -%15, max 5
açık pozisyon, min hacim $5.000, edge eşiği, OPT-1..OPT-6 gate'leri) kod ile
tek tek karşılaştırıldı — round 105'in bulduğu OVERPRICED_BLOCK tarzı taze
bir bug bulunmadı. Hard-enforcement gate'lerin (max pozisyon sayısı, günlük
stop-loss, Kelly %20 cap, OVERPRICED_BLOCK) hepsi tutarlı ve doğru sınırda
karşılaştırıyor.

İki **önceden bilinen** (round 2 ve round 4'te ilk kez görülüp o zaman
kasıtlı kabul edilmiş ama üst seviye dokümanlar hiç güncellenmemiş) doc/kod
sapması yeniden tespit edildi — yeni bir davranış değişikliği değil, sadece
CLAUDE.md/strategy.md'nin güncel olmayan rakamlar taşıması:
1. `agents/orchestrator.py:262` `MIN_MARKET_VOLUME` varsayılanı $10.000,
   CLAUDE.md/strategy.md "$5.000" yazıyor — kod dokümandan 2x daha katı
   (güvenlik yönünde sapma, risk değil).
2. `strategies/arbitrage_engine.py:468` `VOLUME_GATE_MIN` varsayılanı 0.8
   (`# was 1.2, relaxed for live` yorumuyla kasıtlı gevşetilmiş), CLAUDE.md
   OPT-4'ü hâlâ "1.2x" ve "Tümü Aktif" olarak anlatıyor — kod dokümandan
   daha gevşek (0.8-1.2 arası vol_ratio'lu sinyaller dokümana göre
   bloklanmalıyken canlıda geçiyor).

Her ikisi de düşük öncelikli, salt dokümantasyon-güncelliği sorunu; kod
tarafında düzeltme gerektirmiyor (davranış kasıtlı), CLAUDE.md/strategy.md
güncellenebilir ama bu sermaye güvenliğini etkilemiyor.

## Devam eden, kullanıcı kararı bekleyen iki bulgu (değişmedi)

### 1. Onay kuyruğu / doğrudan emir yolu çelişkisi (104. turdan beri açık)
`docs/APPROVAL_WORKFLOW_SPEC.md` hâlâ "her canlı emrin dashboard'dan
onaylanması zorunlu, doğrudan emir yolu kapalı" diyor; `agents/
orchestrator.py` hâlâ AI sinyallerini `is_approved=True` sabit değeriyle
`approval_queue.enqueue()`'a hiç uğratmadan doğrudan `client.place_order()`'a
gönderiyor (satır 1119, 1137, 1406 — 108. turdan bu yana değişmemiş). 108.
turda kullanıcıya ilk kez bildirildi, bu tur bir yanıt/kod değişikliği
gözlenmedi. Sermaye/güvenlik etkisi olduğu için bu tur da tek taraflı kod
değişikliği yapılmadı.

### 2. Zamanlama sıklığı (106. turdan beri açık)
Bu görev "her gün" olarak tanımlanmış ama tetiklenme aralığı saatlik/daha
sık (108. tur 05:17 UTC'de merge oldu, bu tur 07:17 UTC'de — 2 saat sonra).
106-108. turlar bunu bildirdi; henüz düzeltilmemiş. Bu tur yeni bir bilgi
taşımadığı için ayrı bir bildirim gönderilmedi (önceki bildirim hâlâ
geçerli).

## Sonuç
Kod tabanı sağlıklı (1790/1790, regresyon yok). Bu turda yeni bir canlı bug
bulunmadı; iki eski doküman-kod sapması yeniden doğrulandı (düşük öncelik).
Asıl aksiyon hâlâ kullanıcıda: (a) onay kuyruğu/doğrudan emir çelişkisinin
hangi yönde çözüleceği, (b) zamanlama sıklığının günlüğe çekilmesi. Bu ikisi
zaten bildirildi; bu turda yeni bir push-notification gerektiren gelişme
yok.
