# Günlük Strateji İncelemesi — 2026-09-14

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum özeti
- Oturum açıldığında `origin/main` (`eb170aa`, 31. çalışmanın sonucu —
  PR #57, aynı zamanda #52-#56'yı da merge etmişti) ile bu branch birebir
  aynıydı; bekleyen fark yoktu.
- Paralel bir oturumdan gelen **PR #58** (32. çalışma) açık ve
  unmerged: `core/polymarket_client.py::get_real_balance()`'ın hata
  yollarında `0.0` döndürmesi (gerçek $0 bakiyeden ayırt edilemiyor).
  Bu PR farklı bir branch'te (`claude/brave-faraday-myvsz9`) olduğu için
  bu oturumun kapsamı dışında bırakıldı — kendi düzeltmemi onunla
  çakışmayacak şekilde seçtim.

## Bugün yapılan işlem: `_sync_real_balance()`, resolve olmamış ama süresi geçmiş pozisyonları "locked" sermayeden sessizce düşürüyordu

### Hata
`agents/orchestrator.py::Orchestrator._sync_real_balance()`, gerçek CLOB
bakiyesine eklenecek `locked` tutarını hesaplarken, market bitiş zamanı
geçmiş her pozisyonu "payout zaten CLOB bakiyesine yansımıştır, tekrar
saymak double-count olur" varsayımıyla `locked`'a **dahil etmiyordu**:

```python
_, end_utc = parse_market_times(question)
if end_utc and now_utc > end_utc:
    continue   # locked'a eklenmiyor
```

Bu varsayım yanlış: `update_positions()` her cycle'da `_sync_real_balance()`'dan
**önce** çalışıyor ve resolve edebildiği her pozisyonu zaten
`data["positions"]`'dan silip `data["closed"]`'a taşıyor (pnl'ini de
`capital`'e ekleyerek — bkz. `agents/orchestrator.py:561` ve
`run()` içindeki çağrı sırası, satır 366/406). Yani `_sync_real_balance()`
çalıştığında `data["positions"]` içinde hâlâ duran bir pozisyon — bitiş
zamanı geçmiş olsa bile — tanım gereği CLOB tarafından **henüz resolve
edilmemiş** demektir (`position_manager.update_positions()`'daki
WAITING_RESOLUTION / STALE_UNRESOLVED / 45dk timeout mantığı tam olarak bu
durumu ele almak için var — 5-15 dakikalık kripto up/down marketlerinde
60-120sn'lik cycle'a karşı rutin bir durum). Onun USDC'si henüz gerçek
CLOB bakiyesine düşmemiştir, hâlâ kilitlidir — tıpkı diğer tüm açık
pozisyonlar gibi (`position_manager.locked_capital()`/`available_capital()`
zaten böyle davranıyor, hiçbir end-time istisnası yok).

Somut senaryo: `capital=$50`, bitiş zamanı geçmiş ama henüz resolve
olmamış tek bir $4'lük açık pozisyon. Gerçek CLOB bakiyesi $46 (o $4 hâlâ
kilitli). Hatalı kod `locked=$0` hesaplıyordu (hariç tutulduğu için) →
`new_capital = 46+0 = $46`, pozisyon nihayet resolve olana kadar her
cycle'da diskteki `capital`'e $4'lük bir kayıp yazılıyordu. Bu, Kelly
boyutlandırmasını, `AutonomousDecisionEngine`'in SURVIVAL-mode eşiğini ve
`PositionManager.daily_loss_exceeded()`'ı (CLAUDE.md'nin dokunulmaz
günlük -%15 stop-loss kuralının paydası `capital - daily.pnl`) sessizce
bozuyordu.

`git blame` ile orijinal `feat` commit'ine (`9b5fd52`) kadar izlendi;
önceki 31 günlük incelemenin hiçbiri (ve açık PR #58'in de) bu satırlara
dokunmamış — `get_real_balance()`'ın kendisi değil, onu çağıran
`_sync_real_balance()`'ın `locked` hesaplaması farklı bir hata.

### Düzeltme
End-time'a dayalı hariç tutma mantığı tamamen kaldırıldı; `locked`, artık
`position_manager.locked_capital()` ile aynı invaryantı kullanıyor —
`data["positions"]` içindeki her pozisyonun `amount`'ı toplanıyor:

```python
locked = sum(
    p.get("amount", 0)
    for p in self.position_manager.data.get("positions", {}).values()
)
```

Başka hiçbir davranış değiştirilmedi (`get_real_balance()`, `balance < 0`
erken-çıkış koruması, `new_capital = balance + locked` hesaplaması ve
sonrası dokunulmadı).

`tests/test_sync_balance_excludes_unresolved_past_due_position.py` eklendi
(2 test):
1. Asıl regresyon: bitiş zamanı geçmiş ama hâlâ açık bir pozisyonun
   `_sync_real_balance()` sonrası hâlâ locked sayıldığını (`capital`
   sabit $50 kaldığını) doğrular.
2. Non-regresyon: bitiş zamanı henüz gelmemiş (parse edilemeyen) bir
   pozisyonun davranışının değişmediğini doğrular.

## Doğrulama
- Fix öncesi (`git stash` ile `agents/orchestrator.py` geri alınmış):
  yeni testlerden biri **fail** — `capital was corrupted to 46.0`
  (log: `CAPITAL_SYNC: $50.0000 → $46.0000 (CLOB=$46.0000 + locked=$0.0000)`),
  tam olarak açıklanan senaryoyu yeniden üretiyor.
- Fix sonrası: `pytest tests/test_sync_balance_excludes_unresolved_past_due_position.py -v`
  → **2/2 pass**.
- Tam suite (fix sonrası): `pytest tests/ -q` → **1506 passed, 4 skipped**,
  sıfır regresyon.
- Test çalıştırmalarının yan etkisi olan `data/autonomous_state.json`
  commit öncesi eski haline döndürüldü.

## Sonuç
33. çalışma, gerçek CLOB bakiyesini `capital`'in tek doğruluk kaynağı
yapan `_sync_real_balance()` içinde, `get_real_balance()`'ın kendisinden
bağımsız ikinci bir hata buldu: süresi geçmiş ama henüz resolve olmamış
pozisyonların `locked` sermayeden sessizce düşürülmesi. Tek satırlık
semantik bir düzeltmeyle (yaklaşık 15 satırlık yanlış istisna mantığı
kaldırılarak) `position_manager.locked_capital()` ile tutarlı hale
getirildi, iki regresyon testiyle kilitlendi. PR #58 (32. çalışma, farklı
branch) hâlâ bağımsız olarak bekliyor — merge edilirken bu fix ile
çakışma olmaz (farklı fonksiyon bloğu).
