# Günlük Strateji İncelemesi — 2026-09-18 (80. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = bu branch = `9c6ee20` (79. inceleme sonrası).
Açık PR yoktu. Baseline test: `python3 -m pytest tests/ -q` → 844 passed,
2 skipped (`crypto_directional/` hariç — sklearn eksik, canlı yolla ilgisiz).
Not: bu oturumda `loguru`/`httpx`/`rich` sistem python'unda kurulu değildi,
`pip install -r requirements.txt` ile kurulup baseline sayı doğrulandı.

## Bu turda yapılanlar

### 1) Devreden iki açık madde
**a) `agents/whale_tracker.py:48` — `market` query param sorusu.**
`data-api.polymarket.com`'a hem `curl` hem `WebFetch` ile tekrar erişim
denendi; bu oturumda da ağ politikası engelledi (`EGRESS_BLOCKED` /
`403 CONNECT tunnel failed`, 79. turla aynı sonuç). Kod okuma yoluyla
tekrar değerlendirildi:
- `agents/top_trader_signal.py`, aynı `/trades` endpoint'inin **yanıt**
  şemasında market kimliğinin `conditionId` (camelCase) alanında geldiğini
  doğruluyor (önceki bir review'da düzeltilmiş bug'ın yorumunda).
- Repo içindeki diğer tüm `/trades`, `/positions`, `/leaderboard` çağrıları
  (`copytrade.py`, `smart_trader_tracker.py`, `_audit_trades.py`) filtre
  parametresi olarak `user` kullanıyor — hiçbiri `market` ile filtreleme
  yapmıyor, yani karşılaştırma için doğrudan bir emsal yok.
- `agents/whale_tracker.py`, `params={"market": condition_id, "limit": 200}`
  kullanıyor — bu, `data-api.polymarket.com/trades`'in bilinen (yaygın
  topluluk implementasyonlarında görülen) filtre parametre adıyla tutarlı;
  yanıt şemasındaki `conditionId` alanı ile karışıklık yok çünkü biri
  *istek* filtre anahtarı, diğeri *yanıt* alan adı.
- Sonuç: kod okuma temelinde bariz bir hata bulunamadı. Ağ erişimi olmadan
  **kesin** doğrulama yapılamıyor — bu yüzden düzeltme olarak sunulmadı,
  madde yine sıradaki tura devrediliyor (gerçek ağ erişimi olan bir oturum
  doğrulamalı).

**b) `Orchestrator._update_loss_streak()` NEUTRAL tutarsızlığı.**
`agents/orchestrator.py:620` ve `:2045` kontrol edildi — CIRCUIT_BREAKER
bloğu hâlâ yorum satırı (`if _t.time() < self._loss_cooldown_until:` hâlâ
comment-out, `CIRCUIT_BREAKER tamamen kaldırıldı — kullanıcı talebi`).
`_consecutive_losses` hâlâ sadece `logger.info(CIRCUIT_BREAKER_INFO...)`
satırını besliyor, başka hiçbir karar noktasında okunmuyor (grep ile
doğrulandı). Hâlâ inert — talimata göre dokunulmadı.

### 2) Fresh sweep — bulunan ve düzeltilen gerçek bug
**`core/position_manager.py::PositionManager._check_order_filled()`** —
partial-fill sonrası full-fill reconciliation eksikliği.

Kod, `clob_status in ("MATCHED", "FILLED")` kontrolünü `size_matched`
tabanlı `amount` güncellemesinden **önce** yapıyor ve hemen `return True`
ile çıkıyordu (yorum: "Tam dolum — amount doğru zaten"). Bu varsayım
sadece emrin İLK pollamada doğrudan MATCHED gelmesi durumunda doğru.
Ama gerçek akış şu şekilde de olabiliyor (ve GTC/passive emirlerde olağan):

1. Cycle N: CLOB `status=LIVE`, `size_matched=4/20` (%20 dolum) döner.
   Mevcut (zaten önceki bir review'da düzeltilmiş) mantık `pos["amount"]`'u
   doğru şekilde `$2.00`'a küçültüyor ve pozisyonu pollanabilir bırakıyor
   (status MATCHED'e sabitlenmiyor) — bu kısım doğru.
2. Cycle N+1: emir tamamen doldu, CLOB `status=MATCHED`, `size_matched=20/20`
   döner. Eski kod bu noktada `size_matched`'i hiç okumadan
   `pos["status"]="MATCHED"` yapıp çıkıyordu — `pos["amount"]` cycle N'den
   kalma `$2.00`'da donmuş kalıyordu, oysa gerçekte `$10.00` harcanmıştı.

**Canlı etki:** `available_capital()` (`capital - sum(pozisyon amount'ları)`)
gerçekte kilitli olan sermayeyi az gösterip serbest nakti şişiriyor —
sonraki sinyaller bu şişirilmiş sermaye üzerinden boyutlandırılıp gerçek
%20 tek-pozisyon / toplam exposure limitlerini fiilen aşabilir. Pozisyon
kapandığında `_close_position()`'daki `shares = amount/entry_price` de bu
küçük `amount`'u kullandığı için gerçekte sahip olunan share sayısından
daha az share üzerinden payout/pnl hesaplanıyor — gerçekleşen kâr/zarar ve
dolayısıyla `data["capital"]` sessizce ve kalıcı olarak yanlış hale
geliyordu. Bu, CLAUDE.md'nin pozisyon boyutu/sermaye muhasebesi
invaryantlarını doğrudan etkileyen, sermayeyi bozan gerçek bir hata.

**Düzeltme:** `size_matched → amount` reconciliation bloğu artık
`clob_status` ne olursa olsun (MATCHED/FILLED dahil) her pollamada önce
çalışıyor; MATCHED/FILLED short-circuit'i bundan sonra geliyor. Böylece
kısmi dolumdan sonra gelen tam dolum raporu her zaman gerçek harcanan
USDC'ye reconcile oluyor. İlk pollamada doğrudan MATCHED gelen (kısmi dolum
geçmişi olmayan) yaygın durum davranışsal olarak değişmedi.

**Test:** `tests/test_full_fill_after_partial_reconciles_amount.py` (yeni,
2 test) — kısmi dolum sonrası tam dolumda `amount`'un `$10.00`'a reconcile
olduğunu, ve ilk pollamada doğrudan MATCHED gelen sıradan durumun
etkilenmediğini doğruluyor. İlgili mevcut regresyon testleri
(`test_live_partial_fill_freezes_polling.py`,
`test_partial_fill_uses_real_filled_size.py`) da yeşil kalıyor.

### 3) Diğer taze inceleme (bulgu yok)
- `agents/orchestrator.py::run()`/`_cycle()` döngü sıralaması (WATCHDOG,
  `_update_loss_streak()`, `kelly.update_streak()`, walk-forward, 11-nokta
  live gate, `existing_exposure` toplam pozisyon kontrolü) tekrar elle
  izlendi — 74./77. review'ların düzelttiği sıralama hâlâ doğru, yeni sorun
  yok. WATCHDOG bloğu kasıtlı olarak sadece log-only (`WATCHDOG DISABLED`
  yorumu ile açıkça belirtilmiş, gerçek -%15 günlük stop
  `daily_loss_exceeded()` üzerinden ayrı ve aktif çalışıyor) — bu bilinen
  ve kasıtlı bir tasarım, hata değil.
- `core/position_manager.py::update_positions()`/`_close_position()`/
  `_close_position_neutral()` tam olarak yeniden okundu; duplicate-guard,
  `_roll_daily_if_needed()` sıralaması, YES/NO fiyatlama dalları (gerçek
  sıfır kotasyon vs eksik kotasyon ayrımı) doğru. Tek gerçek sorun yukarıda
  düzeltilen `_check_order_filled()` idi.

## Sonuç
Bir gerçek, sermaye muhasebesini bozan hata bulundu ve düzeltildi:
`core/position_manager.py::_check_order_filled()`'ın kısmi dolumdan sonra
gelen tam dolum raporunu `amount`'a reconcile etmemesi. Fix minimal (branch
sırası değişikliği + reconciliation'ın MATCHED/FILLED için de çalışması) ve
cerrahi. Yeni test eklendi, tam test suite **846 passed, 2 skipped**
(844 + 2 yeni test) ile yeşil — hiçbir mevcut test bozulmadı. Çalışma
sırasında oluşan `data/autonomous_state.json` yan etkisi `git checkout --`
ile geri alındı. CLAUDE.md'nin risk kuralları (max %20 pozisyon, günlük
-%15 stop, max 5 açık pozisyon, min $5,000 hacim, min 0.05 edge) kod
tarafında değiştirilmedi; bu fix onları zaten var olan haliyle daha
güvenilir kılıyor (gerçek kilitli sermaye artık yanlış küçük gösterilmiyor).

## Sıradaki tur için notlar
- `agents/whale_tracker.py:48`'deki `market` query param sorusu hâlâ ağ
  erişimiyle kesin doğrulanamadı (bu oturumda da `data-api.polymarket.com`
  egress-blocked). Kod okuma temelinde bariz bir hata görünmüyor
  (yaygın data-api filtre konvansiyonuyla tutarlı), ama gerçek prod ağ
  erişimi olan bir oturum kesin doğrulama yapmalı.
- `Orchestrator._update_loss_streak()`'in NEUTRAL'i streak-bozan sayması
  hâlâ inert (CIRCUIT_BREAKER bloğu hâlâ yorum satırı) — eğer gelecekte o
  blok yeniden aktif edilirse bu tutarsızlık gerçek bir davranış farkına
  dönüşür, o zaman düzeltilmeli.
- Bu turda `_check_order_filled()` düzeltildi ama aynı fonksiyonun `except`
  bloğu (CLOB sorgusu hata verirse `return False`, yani "dolmadı" sayılır)
  incelenmedi derinlemesine — gelecekte bir tur, gerçekten dolmuş ama
  API'nin geçici hata verdiği bir emrin yanlışlıkla NEUTRAL/tekrar-emir
  riskine yol açıp açmadığını değerlendirebilir.
