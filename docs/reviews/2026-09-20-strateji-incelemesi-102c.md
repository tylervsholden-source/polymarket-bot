# Günlük Strateji İncelemesi — 2026-09-20 (102. tur, c oturumu)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = `a1ba25b` (#179, 101. tur konsolidasyonu
merge edilmiş). Bu branch (`claude/brave-faraday-vox5pw`) `origin/main` ile
eşit başladı, açık farkı yoktu.

Aynı gün içinde, aynı taban commit üzerinden, bu oturumla paralel çalışan iki
ayrı "102. tur" oturumu daha var — henüz main'e merge edilmemiş, ama
üzerlerinde tekrar çalışmamak için not ediliyor:

- **PR #180** (`claude/brave-faraday-abegll`) — `core/candlestick_analyzer.py`
  içinde HAMMER/HANGING_MAN yanlış sınıflandırma hatası düzeltildi.
- **PR #181** (`claude/brave-faraday-7h9bs3`) — `agents/binance_feed.py`'nin
  WS feed'i sadece ilk döngünün kısmi coin kümesine abone oluyordu (tam coin
  evrenine değil) hatası düzeltildi.

Bu oturumda `core/candlestick_analyzer.py`'ye ve `agents/binance_feed.py`'nin
WS-başlatma mantığına dokunulmadı (talimat gereği). Konteynerde çalışan bir
bot instance'ı yok (`data/status.json`/`control.json`/`positions.json`
mevcut değil, ağ erişimi yok) — %10 hedefine karşı gerçek zamanlı sermaye
ilerlemesi bu oturumdan doğrulanamıyor; katkı kod/strateji doğruluğu
seviyesinde.

## Baseline doğrulama
- `pip install -r requirements.txt` çalıştırıldı (zaten kurulu, ek bir şey
  değişmedi).
- `python3 -m pytest tests/ calibration/tests execution_realism/tests signal_bridge/tests crypto_directional/tests -q`
  → **1762 passed, 4 skipped** (bu branch'in başlangıç durumu; görev
  talimatındaki 1762-1766 aralığıyla tutarlı).
- Not: test suite çalıştırıldığında `data/autonomous_state.json` yan etkisi
  oluşuyor (bilinen davranış, önceki turlarda da görülmüş); her çalıştırma
  sonrası `git checkout -- data/autonomous_state.json` ile geri alındı.

## Bu turda incelenen alan

Görev talimatının önerdiği iki öncelikli alandan ilkini derinlemesine
inceledim: **`agents/trade_analyzer.py`'nin EXPIRED dalı ve tüketicileri**,
ardından **`strategies/maker_engine.py`'nin `check_paired_profit` dışındaki
kısımları**.

### `agents/trade_analyzer.py` — EXPIRED dalı zaten temiz
`analyze_trade()`'in outcome belirleme mantığı (`result in ("WIN","LOSS",
"NEUTRAL")` → doğrudan kullan, `result == "EXPIRED"` → `NEUTRAL`'a eşle,
aksi halde pnl işaretine bak) ve `_evaluate_signal_accuracy()`'deki
`is_resolved = outcome in ("WIN","LOSS")` guard'ı — dosyanın kendi içindeki
yorumlara göre daha önceki turlarda (43., ve whale/regime direction
karşılaştırma hataları için ayrıca) düzeltilmiş ve şu an doğru: EXPIRED bir
LOSS olarak yanlış etiketlenmiyor, sinyal doğruluk sayaçlarına da
karıştırılmıyor. `git log --follow -- agents/trade_analyzer.py` bu alanın
zaten yoğun revize edildiğini gösteriyor. Tek tüketicisi
`agents/orchestrator.py::_analyze_new_closed_trades()` — sadece log/rapor
amaçlı, dönen `TradeAnalysis`'i risk kararına geri beslemiyor. Bu dosyada
yeni bir hata bulunamadı.

### `strategies/maker_engine.py` — `check_paired_profit` dışı, zaten büyük
ölçüde taranmış
`get_committed_capital()`/`_expire_resolved_inventory()` (98. tur),
`_cancel_all_standing()`'in fill-detection'ı (47. ve 72. tur) ve
`_quote_market()`'teki `MAX_INVENTORY_PER_SIDE` tavanı (66. tur) — hepsi
dosyanın kendi içinde ayrıntılı yorumlarla belgelenmiş, doğrulanmış
düzeltmeler. `stoikov.py::maker_quotes()`, `_select_markets()`,
`_is_crypto_market()`/`_is_short_window()` satır satır yeniden okundu; yeni
bir muhasebe/mantık hatası bulunamadı. `check_paired_profit()` kendisi hâlâ
hiçbir yerden çağrılmıyor (100./101. turlarda da not edilen ölü kod).

Bu ikinci alanda net bir hata bulamayınca, trade_analyzer.py'nin
EXPIRED sonucunun **başka** bir tüketicisini aramak için
`grep -rn "EXPIRED"` ile tüm canlı/sim karar yoluna tekrar baktım — ve orada
gerçek bir hata buldu.

## Bulunan ve düzeltilen hata: OPT-7 bounce guard, EXPIRED trade'i streak-kırıcı sayıyordu

`agents/orchestrator.py::_update_loss_streak()` içindeki OPT-7
("Consecutive WIN per coin — bounce guard") bloğu, `closed[-30:]`'u geriye
doğru tarayıp her coin için ardışık NO-WIN sayısını (`_consecutive_wins_per_coin`)
hesaplıyor:

```python
if trade.get("result") == "WIN" and _side == "NO":
    self._consecutive_wins_per_coin[_coin] = self._consecutive_wins_per_coin.get(_coin, 0) + 1
elif trade.get("result") == "NEUTRAL":
    continue  # Unfilled/cancelled GTC order — neither win nor loss, doesn't break streak
else:
    _coin_done.add(_coin)  # Bu coin'in streak'i kırıldı
```

`NEUTRAL` (dolmamış/iptal GTC emir) açıkça "streak'i kırma" olarak
işaretlenmiş — ama `EXPIRED` (sim'de market 45dk+ resolve olmadan expire
olduğunda, `_check_sim_resolutions()`'ın ürettiği üçüncü sonuç kategorisi,
bkz. yukarıdaki bölüm) hiç ele alınmamış. Ne `"WIN" and NO` şartına ne de
`"NEUTRAL"` şartına uyduğu için doğrudan `else` dalına düşüyor ve
`_coin_done.add(_coin)` ile **coin'in streak'i sıfırlanıyor** — tıpkı gerçek
bir kayıp veya ters yönlü bir kazanç gibi.

Bu, dosyanın kendi mantığıyla ve projenin geri kalanıyla tutarsız:
- `agents/trade_analyzer.py` (yukarıda) EXPIRED'ı özellikle NEUTRAL ile aynı
  kovaya koyuyor — "yön hakkında hiçbir bilgi taşımayan bir kapanış".
- `agents/autonomous_engine.py::_update_performance()`'ın kendi ardışık
  kayıp/kazanç döngüsü zaten sadece `WIN`/`LOSS`'u özel olarak işliyor,
  geri kalan her şeyi (NEUTRAL, EXPIRED dahil) sessizce atlıyor —
  "matching kelly_criterion.update_streak()" yorumuyla kasıtlı.

**Canlı etki**: `_consecutive_wins_per_coin`, `agents/orchestrator.py::_cycle()`
içindeki CONSEC_WIN_GUARD'ı besliyor (satır ~920-937): aynı coin'de 3+
ardışık NO WIN varsa yeni NO sinyalini SKIP et (bounce riski —
CLAUDE.md'nin belgelediği "2+ ardışık NO-win periyottan sonra %100 bounce
geliyor" pattern'ı), 2'de ise half-Kelly uygula. Sim/paper modda (botun
fiili varsayılan çalışma biçimi, bkz. CLAUDE.md) aynı coin'de 2 ardışık NO
WIN'den sonra bir EXPIRED trade araya girerse (market 45dk+ resolve
olmadıysa — nadir ama gerçek bir durum, zaten kendi kod yolu var), sayaç
sıfırlanıyor; bir sonraki NO WIN "3. ardışık" yerine "1." olarak sayılıyor
ve guard hiç tetiklenmiyor. Sonuç: tam olarak guard'ın korumak için var
olduğu bounce-riskli senaryoda, bot tam boyutlu (indirgemesiz) bir NO
pozisyonu açabiliyor.

### Düzeltme
`agents/orchestrator.py::_update_loss_streak()`'te `NEUTRAL` ile aynı
`continue` dalına `EXPIRED` de eklendi:

```python
elif trade.get("result") in ("NEUTRAL", "EXPIRED"):
    # NEUTRAL: unfilled/cancelled GTC order. EXPIRED: sim market
    # didn't resolve within 45min (_check_sim_resolutions()) —
    # both carry zero directional information ...
    continue
else:
    _coin_done.add(_coin)  # Bu coin'in streak'i kırıldı
```

(`_update_loss_streak()`'in hemen üstündeki genel `_consecutive_losses`
döngüsü de EXPIRED'ı ne `LOSS` ne `("WIN","NEUTRAL")` eşleştirdiği için aynı
şekilde atlıyordu — ama o değişken artık sadece bilgilendirme log'unu
besliyor (circuit breaker kullanıcı talebiyle kaldırılmış), gerçek bir
karara girmiyor; bu yüzden dokunulmadı, kapsam OPT-7'nin gerçekten karara
giren `_consecutive_wins_per_coin`'iyle sınırlı tutuldu.)

### Test
`tests/test_opt7_expired_trade_does_not_break_streak.py` (2 yeni test,
`tests/test_opt7_sim_mode_consec_win_guard.py`'nin aynı harness deseniyle):
- `test_expired_trade_does_not_reset_consecutive_no_win_streak` — BTC için
  NO-WIN, NO-WIN, NO-EXPIRED, NO-WIN sırası düzeltme öncesi
  `_consecutive_wins_per_coin["BTC"] == 1` döndürüyordu (FAIL, beklenen 3),
  düzeltme sonrası `== 3` (PASS).
- `test_expired_trade_alone_does_not_start_or_break_anything` — tek başına
  bir EXPIRED trade'in ne sayaç başlattığını ne de sonraki gerçek 2'li
  streak'i bozmadığını doğrulayan regresyon-karşıtı test (düzeltme öncesi de
  zaten PASS — EXPIRED'ın "sıfırlama" davranışı burada zaten doğru sonucu
  veriyordu, sadece ARADA olduğunda yanlıştı).

**Doğrulama** (`git stash` ile `agents/orchestrator.py`'deki düzeltmeyi geri
alıp sadece yeni test dosyasıyla çalıştırdım):
- Düzeltme öncesi: `test_expired_trade_does_not_reset_consecutive_no_win_streak`
  FAIL (`assert 1 == 3`) — hatayı birebir doğruladı.
- Düzeltme sonrası: `git stash pop`, iki test de PASS.

### Tam suite
- Düzeltme öncesi (baseline): **1762 passed, 4 skipped**.
- Düzeltme sonrası: **1764 passed, 4 skipped** (+2 yeni test, 0 regresyon —
  beklenen aritmetik).

## Sermaye/performans notu
Bu oturumda çalışan bir bot instance'ı yok — %10 hedefine karşı gerçek
ilerleme doğrulanamıyor. Bu turun katkısı, sim/paper modda (botun
varsayılan işletim biçimi) nadir ama gerçek bir kapanış türünün (EXPIRED),
CLAUDE.md'nin belgelediği bounce-riski korumasını (OPT-7/CONSEC_WIN_GUARD)
sessizce devre dışı bırakmasını önlemek — guard artık EXPIRED'dan etkilenmeden
gerçek ardışık NO-WIN sayısını doğru sayıyor.

## Sonuç
`agents/trade_analyzer.py`'nin EXPIRED dalı ve `strategies/maker_engine.py`'nin
`check_paired_profit` dışı kısmı önceki turlarda zaten kapsamlı şekilde
temizlenmiş durumda; bu iki dosyada yeni bir hata yok. Ama aynı EXPIRED
kategorisinin ÜÇÜNCÜ bir tüketicisi (`orchestrator.py`'nin OPT-7 streak
hesaplaması) gözden kaçmıştı ve gerçek, canlı karara etkili bir hataydı —
düzeltildi, regresyon testiyle doğrulandı.

## Sıradaki tur için notlar
- Görev talimatının önerdiği diğer iki alan (`core/web_server.py`/dashboard
  status-service — `/api/control` whitelist'i hariç — ve `scripts/`) bu
  turda zaman bütçesi dahilinde ele alınmadı, hâlâ en az taranmış adaylar.
- `agents/trade_analyzer.py` ve `strategies/maker_engine.py` (check_paired_profit
  hariç) artık iki ayrı turda (98./66./72./47. ve bu tur) derinlemesine
  tarandı — kısa vadede tekrar aynı dosyalara bakmak muhtemelen düşük getiri;
  `check_paired_profit()`'in kendisi hâlâ hiçbir yerden çağrılmayan ölü kod
  (100./101. turlarda da not edildi) — canlı karara sıfır etkisi olduğu için
  yine dokunulmadı.
- PR #180 ve #181 main'e merge edildiğinde, bu PR ile aralarında gerçek bir
  kod çakışması yok (üçü de farklı dosyalarda: candlestick_analyzer.py,
  binance_feed.py, orchestrator.py + yeni test dosyası) — sıralı merge
  sorunsuz olmalı.
