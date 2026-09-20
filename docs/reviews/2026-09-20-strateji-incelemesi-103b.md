# Günlük Strateji İncelemesi — 2026-09-20 (103. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = `330ffaa` (#182 merge edilmiş, 102. turun üç
paralel oturumu — candlestick HAMMER/HANGING_MAN, binance_feed WS abonelik,
OPT-7 EXPIRED streak — hepsi main'e girmiş). Bu branch (`claude/brave-faraday-
1tvm3c`) `origin/main` ile eşit başladı, açık farkı yoktu.

Konteynerde çalışan bir bot instance'ı yok (`data/status.json`,
`data/control.json`, `data/positions.json` mevcut değil; `.env` yok; ağ
erişimi canlı Polymarket/Anthropic API'lerine değil) — %10 hedefine karşı
gerçek zamanlı sermaye ilerlemesi bu oturumdan doğrulanamıyor. Katkı,
102c'nin notlarında bırakıldığı yerden kod/strateji doğruluğu seviyesinde
devam etti.

## Baseline doğrulama
- `pip install -r requirements.txt` çalıştırıldı.
- `python3 -m pytest tests/ calibration/tests execution_realism/tests signal_bridge/tests crypto_directional/tests -q`
  → **1769 passed, 4 skipped** (bu branch'in başlangıç durumu).
- `data/autonomous_state.json` yan etkisi (bilinen davranış) her çalıştırma
  sonrası `git checkout -- data/autonomous_state.json` ile geri alındı.

## Bu turda incelenen alanlar
102c'nin "sıradaki tur için notlar" bölümünün işaret ettiği iki en az
taranmış aday ele alındı: **`core/web_server.py`** (dashboard status-servisi,
`/api/control` whitelist hariç — o zaten önceki turda kontrol edilmiş) ve
**`scripts/`** (`migrate_positions.py`, `verify_binance_trades.py`).

### Önce kontrol edilen, hatasız çıkan alanlar
- **`agents/orchestrator.py::_execute_approved_orders()` edge/onay kuyruğu
  yolu** — 87. turda düzeltilen "edge hiç yazılmıyordu" hatası hâlâ düzelmiş
  durumda (`edge=edge` satırı yerinde). Onay kuyruğu (`control_plane/
  approval_queue.enqueue()`) canlı `_cycle()` yolundan hiç çağrılmıyor —
  doğrudan emir yolu (`DOĞRUDAN EMİR VER (onay kuyruğu bypass)`) `docs/
  APPROVAL_WORKFLOW_SPEC.md`'nin "Dogrudan emir verme yolu kapatilmistir"
  ifadesiyle çelişiyor, ama bu satır konteynerin gördüğü tüm git geçmişinde
  (root commit'e kadar) mevcut, mum-içi 30-60sn'lik edge penceresi
  (`_fresh_price_ok` yorumu) ile açıkça kasıtlı bir tasarım tercihi gibi
  duruyor. Onay kuyruğunu zorunlu kılmak botun temel yürütme davranışını
  değiştirecek büyük bir mimari karar — kullanıcı onayı olmadan bu turda
  dokunulmadı, sadece not ediliyor.
- **`strategies/kelly_criterion.py`** — `update_streak()` gerçekten
  `agents/orchestrator.py:687`'den çağrılıyor (`self.arb_engine.kelly.
  update_streak(closed_trades)`), `_current_closed_trades()` üzerinden
  (82-85. turlarda düzeltilen aynı ortak kaynak). Dynamic Kelly streak
  multiplier'ı canlı/sim modda aktif ve doğru besleniyor — hata yok.
- **`agents/smart_trader_tracker.py`** — stale pozisyon temizleme, ağırlıklı
  sinyal hesabı, UP/DOWN↔YES/NO eşleme mantığı doğru; eşzamanlı
  `asyncio.gather` çağrıları `await` noktaları arasında `self._positions`'ı
  güvenli şekilde mutasyona uğratıyor (race yok). Hata bulunamadı.
- **`scripts/migrate_positions.py`**, **`scripts/verify_binance_trades.py`**
  — satır satır okundu, `fix_pnl()`, dedup, crypto-filtre, CLOB doğrulama
  mantığı tutarlı. `verify_binance_trades.py`'deki `clob_won` ifadesi
  fazladan bir OR dalı içeriyor (`(outcome == clob_resolution) or (outcome
  == "YES" and clob_resolution == "YES") or ...` — ilk terim zaten ikinci ve
  üçüncüyü kapsıyor) ama bu sadece gereksiz kod, davranışı değiştirmiyor;
  dokunulmadı.

## Bulunan ve düzeltilen hata: `/api/status`'ta position_meta.json merge'i positions.json override'ı tarafından eziliyordu

`core/web_server.py::_Handler._send_status()` üç kaynağı sırayla okuyordu:

1. `status.json` → `status` sözlüğüne yükle.
2. `data/position_meta.json` → `status["positions"][market_id]` içine
   `target_price`/`asset`/`gamma_id` gibi ek alanları merge et.
3. `data/positions.json` → **bot çalışırken her zaman daha güncel** olduğu
   için (yorum: "status.json bot durduğunda stale kalır") `pm_data.get(
   "positions")` doluysa `status["positions"]`'ı **komple değiştir**.

Adım 3, adım 2'nin yazdığı merge'i sözlüğü baştan atayarak siliyordu —
`pm_data["positions"]` (positions.json'daki ham pozisyon kaydı) hiçbir zaman
`target_price`/`asset`/`gamma_id` alanlarını taşımıyor, çünkü bunlar
`position_meta.json`'a özel. Sonuç: bot normal şekilde çalışırken (yani
`data/positions.json` dolu pozisyonlar içerdiğinde — sıradan durum) `/api/
status` endpoint'i meta verisini **hiçbir zaman** göstermiyordu; sadece bot
durmuşken/`positions.json` boşken (adım 3'ün `elif` dalına hiç girmediği,
status.json'daki eski positions dict korunduğu durum) merge görünür
kalıyordu — ki bu da tam tersine tam olarak "stale" durumdu.

**Canlı etki notu**: `web/index.html::renderPositions()` şu anda
`target_price`/`asset`/`gamma_id` alanlarını hiç okumuyor (sadece
`question`/`outcome`/`entry_price`/`amount`/`status`/`unrealized_pnl`
render ediliyor) ve kod tabanında `position_meta.json`'ı yazan hiçbir yer
yok (repodaki dosya sadece eski bir fixture) — yani bu hata şu an hiçbir
görünür dashboard davranışını bozmuyor, ölü/etkisiz bir mantık hatası. Yine
de gerçek ve gelecekte (meta dosyası doldurulduğunda veya front-end bu
alanları okumaya başladığında) sessizce yanlış veri döndürecek bir
tutarsızlık olduğu için düzeltildi — küçük, güvenli, ilgili bölgede daha
önce hiç test yoktu.

### Düzeltme
Merge sırası ters çevrildi: `position_meta.json` merge'i artık
`positions.json` override'ından **sonra**, override'ın kazandığı hangi
`status["positions"]` sözlüğü olursa olsun ona uygulanıyor.

### Test
`tests/test_web_server_status_meta_merge.py` (2 yeni test):
- `test_position_meta_merge_survives_positions_json_override` — `positions.json`
  dolu pozisyon içerdiğinde meta alanlarının hâlâ merge edildiğini doğrular.
  Düzeltme öncesi `git stash` ile doğrulandı: `KeyError: 'target_price'`
  (bug'ı birebir yakalıyor). Düzeltme sonrası PASS.
- `test_position_meta_merge_still_works_without_positions_json_override` —
  regresyon-karşıtı: `positions.json` yokken (eski davranışın zaten doğru
  çalıştığı yol) merge'in bozulmadığını doğrular; düzeltme öncesi de sonrası
  da PASS.

### Tam suite
- Düzeltme öncesi (baseline): **1769 passed, 4 skipped**.
- Düzeltme sonrası: **1771 passed, 4 skipped** (+2 yeni test, 0 regresyon).

## Sermaye/performans notu
Bu oturumda çalışan bir bot instance'ı yok — %10 hedefine karşı gerçek
ilerleme doğrulanamıyor. Önceki turlarda düzeltilen kritik canlı-karar
hataları (edge wiring, OPT-7 EXPIRED streak, NO-direction Kelly penaltısı,
WS coin evreni) hâlâ yerinde ve testlerle korunuyor; bu turda aynı sınıftan
yeni bir canlı-karar hatası bulunamadı — kapsamlı arama `core/web_server.py`
ve `scripts/`'de tek, düşük etkili (şu an ölü kod yolu) bir mantık
hatasıyla sonuçlandı.

## Sonuç
`core/web_server.py::_send_status()`'taki merge-sırası hatası düzeltildi ve
regresyon testiyle doğrulandı. Onay kuyruğu bypass'ı (`docs/
APPROVAL_WORKFLOW_SPEC.md` ile çelişen doğrudan emir yolu) tespit edildi ama
kapsamı büyük bir mimari karar olduğu için kullanıcı onayı olmadan
değiştirilmedi — sıradaki tur veya kullanıcı için not olarak bırakılıyor.

## Sıradaki tur için notlar
- **Onay kuyruğu / doğrudan emir yolu çelişkisi** (`agents/orchestrator.py`
  ~1126: "DOĞRUDAN EMİR VER (onay kuyruğu bypass)") `docs/
  APPROVAL_WORKFLOW_SPEC.md`'nin "Dogrudan emir verme yolu kapatilmistir"
  ifadesiyle doğrudan çelişiyor. Muhtemelen kasıtlı (edge 30-60sn'de eriyor,
  dashboard onayı bunu karşılayamaz) ama spec güncel değilse ya da gerçekten
  kapatılması gerekiyorsa bu kullanıcı kararı gerektirir — otonom olarak
  değiştirilmedi.
- `core/web_server.py`, `scripts/` artık bu turda derinlemesine tarandı —
  kısa vadede tekrar bakmak düşük getiri. `agents/smart_trader_tracker.py`
  ve `strategies/kelly_criterion.py` de bu turda doğrulandı, hata yok.
- Sonraki adaylar: `operator_layer/` (chamber API — `pnl.py` hariç
  satır satır okunmadı), `agents/subagents/reviewer_agent.py` (Claude API
  reviewer, ENABLE_REVIEWER_AGENT), `agents/enhanced_signals.py`,
  `agents/hit_rate_tracker.py`, `agents/copytrade.py` — hiçbiri
  git geçmişinde bir düzeltme commit'inde görünmüyor.
