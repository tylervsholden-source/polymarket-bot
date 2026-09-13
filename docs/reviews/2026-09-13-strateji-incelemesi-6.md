# Günlük Strateji İncelemesi — 2026-09-13 (6. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum özeti
- Açık PR yok, local branch `origin/main` ile birebir aynıydı (`32483a2`),
  çalışma ağacı temizdi.
- `pytest tests/` → **586 passed, 2 skipped** (5. çalışmanın bıraktığı durumla
  eşleşiyor).
- Bu checkout'ta yine `data/control.json`/`.env` yok — gerçek canlı
  sermaye/pozisyon durumu gözlenemiyor, inceleme kod-seviyesinde kaldı.

## Bulunan ve düzeltilen hata: OPT-2 (max 1 coin/period) canlı yolda 5'e gevşetilmişti

Önceki çalışma (4.) v9 optimizasyon tablosunu tek tek izlerken OPT-2'yi
"bu oturumda ayrıca doğrulanmadı, önceki günlerde incelenmiş" diyerek
atlamıştı. Bugün gerçekten doğruladım:

- CLAUDE.md: **"OPT-2: Max 1 Coin/Period — COIN_LIMIT 2→1. Korelasyon %99,
  2 coin = 2x risk 1x bilgi."**
- Kod (`agents/orchestrator.py:602`, değişiklikten önce):
  `self._limit_coins_per_period(all_signals, max_per_period=5)` — yorum
  satırı bile "COIN_LIMIT: max 5 coin/slot" diyordu.
- Fonksiyonun kendi varsayılan parametresi de (`max_per_period: int = 2`)
  hâlâ v9-öncesi (v8) değerindeydi, v9 hedefi olan 1 değil.

`git log -S "max_per_period=5"` bunun da tam olarak diğer 4 çalışmanın
düzelttiği `9b5fd52` squash-commit'inden (aynı commit stop-loss'u,
MAX_OPEN_POSITIONS'ı, OPT-3'ü ve %20 pozisyon tavanını da sessizce
gevşetmişti) geldiğini doğruladı. OPT-1/OPT-4'ün aksine burada "kasıtlı
tasarım" işareti (örn. `# PIVOT: ...` gibi bir not) yoktu — sadece sessiz
bir gevşetme.

**Etki:** Canlı yolda aynı 5 dakikalık zaman diliminde (örn. BTC+ETH+SOL+XRP+DOGE)
5 farklı coin'e kadar aynı anda pozisyon açılabiliyordu; CLAUDE.md'nin kendi
gerekçesiyle ("korelasyon %99 — bu 5x risk, 1x bilgi anlamına geliyor")
doğrudan çelişen bir durumdu.

### Düzeltme
- `agents/orchestrator.py:602` → `max_per_period=1` (canlı çağrı noktası).
- `agents/orchestrator.py:1306` → fonksiyon varsayılanı da `1` yapıldı
  (dokümantasyonla tutarlılık; tek üretim çağrı noktası zaten değeri açıkça
  geçiyor, davranış değişikliği yok).
- `tests/test_opt2_coin_limit.py` eklendi: varsayılanın 1 olduğunu, aynı
  slotta en yüksek edge'li tek sinyalin kaldığını, ve o slotta zaten açık
  bir pozisyon varken yeni sinyalin tamamen düşürüldüğünü kilitliyor.
- Mevcut `tests/test_loss_slot_cooldown.py` etkilenmedi (kendi
  `max_per_period=5` değerini açıkça geçiyor, OPT-6'yı OPT-2'den izole
  test etmek için).

## Doğrulama
- `pytest tests/` → **589 passed, 2 skipped** (586'dan 589'a: 3 yeni OPT-2
  regresyon testi eklendi, mevcut testlerden hiçbiri bozulmadı).
- `python -c "import agents.orchestrator"` → hatasız.

## Bugünün geri kalan v9 tablosu taraması (yeni sapma yok)
| OPT | Durum |
|---|---|
| OPT-1 | Regime-addon'a evrilmiş (OPT-5 içinde), kasıtlı — dokunulmadı. |
| OPT-2 | **Bugün düzeltildi** (yukarıda). |
| OPT-3 | 4. çalışmada onarıldı, bugün değişmedi. |
| OPT-4 | Eşik gevşetilmiş ama belgeli/kasıtlı, aktif — dokunulmadı. |
| OPT-5 | 3. çalışmada onarıldı, bugün değişmedi. |
| OPT-6 | 12 Eylül'de onarılmış, bugün değişmedi. |

## Dokunulmayan gözlemler
- `review_bundle/`, `incident_bundle/`, `incident_bundle_v2/` altındaki eski
  repo kopyaları hâlâ commit'li (önceki incelemelerin de notu) — canlı koda
  etkisi yok.
- CLAUDE.md'nin "Kritik Keşifler" bölümündeki **Sim-Live gap** ("Sim'de NO
  %60-75 WR, canlıda %0 WR. Execution farkı araştırılmalı.") hiçbir günlük
  incelemede henüz araştırılmadı. Bu ortamda gerçek canlı trade log'u/
  `data/positions.json` olmadığından (container her oturumda temiz açılıyor)
  kod-seviyesinde tek somut aday `core/polymarket_client.py:392-424`'teki
  canlı emir "price bump" (+0.01-0.02, fiyat > 0.90'da azaltılıyor) — ama bu
  PnL'i aşındırır, WR'yi %0'a düşürmez; WR'yi düşürecek bir yön/token-index
  hatası bulunamadı (`_verify_outcome_order` cross-check'i hiç
  `OUTCOME_ORDER_FLIPPED` loglamamış, statik kod incelemesiyle doğrulanamıyor).
  Gerçek canlı trade geçmişi olmadan kanıtlanabilir bir kök neden
  bulunamadığından bugün kod değişikliği yapılmadı; bu iz düşülüyor ki
  gelecekte gerçek canlı log erişimi olduğunda takip edilsin.

## Bugün yapılan (özet)
- Kod tabanı, testler (589 passed / 2 skipped), git senkronu doğrulandı.
- OPT-2 (max 1 coin/period) canlı yolda 5'ten 1'e düzeltildi, regresyon
  testi eklendi.
- Bu doküman commit edilip pushlandı.
