# Günlük Strateji İncelemesi — 2026-09-13 (21. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum özeti
- Bu oturum açıldığında `claude/brave-faraday-sx8igr`, `origin/main`
  (`b7cb554`, 20. çalışmanın sonucu — cycle-içi `capital`/`cycle_spent`
  gerçek emir maliyeti düzeltmesi) ile birebir güncel ve temizdi. Bekleyen PR
  yoktu (#41 zaten merge edilmişti) — bu çalışma doğrudan yeni bir alanı
  incelemeye ayrıldı.
- `data/control.json`/`positions.json`/`.env` bu ortamda yok → gerçek API
  kimlik bilgisi veya canlı pozisyon yok, tamamen statik kod incelemesi + testle
  doğrulama.
- Başlangıç testi: `pytest tests/` → **626 passed, 2 skipped**.

## İncelenen alanlar
1. `core/candlestick_analyzer.py` ve `strategies/arbitrage_engine.py`'deki
   entegrasyonu (satır ~1139-1420: `_bullish_patterns`/`_bearish_patterns`,
   `_pattern_score`, `bullish_exhaustion`/`bullish_exhaustion_magnitude`,
   `no_side_health`, `consecutive_bullish`/`consecutive_bearish`,
   `bounce_signal`, `_bounce_active`/`_bounce_faded`, `tech_score` gate).
   Her değişkenin üretildiği (`agents/binance_feed.py::_process_klines`) ve
   tüketildiği yerler satır satır izlendi — hepsi doğru bağlı. Tek dikkat
   çeken nokta: `BinanceFeed.get_candle_analysis()` (multi-timeframe
   `CandlestickAnalyzer.multi_tf_score()` sarmalayıcısı) hiçbir yerden
   çağrılmıyor (`grep -rn "get_candle_analysis"` → sadece tanım) — ama bu,
   14./15. çalışmaların Monte Carlo `viable` ve `latency_arb` emsaliyle aynı
   kategori: bağımsız, opsiyonel bir özellik olarak yazılmış ve hiç
   etkinleştirilmemiş, davranışı bozmuyor, bağlamak ayrı bir tasarım kararı
   gerektirir — bu incelemenin kapsamı dışında bırakıldı, "bug" olarak
   raporlanmadı.
2. `agents/subagents/base_agent.py` — timeout/error handling temel sınıfı
   satır satır okundu, `asyncio.wait_for` + `except Exception` yapısı ve
   `AgentResult` alanları tutarlı; hata bulunamadı.
3. `agents/trade_analyzer.py` — post-trade root-cause + pattern matching +
   "adaptive parameter suggestions" akışı incelendi.
   `agents/orchestrator.py::_analyze_new_closed_trades()` çağrısı doğrulandı;
   üretilen öneriler (`suggested_adjustments`) gerçekten
   `data/trade_analysis.json`'a yazılıp loglanıyor — "hesaplanıp hiç
   kullanılmıyor" türü bir kopukluk yok (log/analiz amaçlı, otomatik
   parametre değişikliği zaten tasarım gereği yok — CLAUDE.md'nin manuel
   inceleme beklentisiyle tutarlı).
4. `agents/btc_arb_agent.py`, `agents/kalshi_arb.py`, `agents/copytrade.py`
   — `grep -rn` ile `agents/orchestrator.py`'den referansları kontrol edildi.
   `kalshi_arb` `KALSHI_ARB_ENABLED`/`BOND_ENABLED` ile devre dışı (16./19.
   çalışmalarda zaten not edilmiş), `copytrade.py` hiç import edilmiyor (ölü
   kod), `btc_arb_agent.py` da orchestrator'a hiç bağlı değil. Üçü de
   varsayılan olarak devre dışı/ölü — CLAUDE.md "Sadelik" kuralı gereği
   dokunulmadı.
5. `agents/smart_trader_tracker.py` ve `agents/top_trader_signal.py` — daha
   önce hiç tek tek incelenmemişti. Burada gerçek bir hata bulundu (aşağıda).

## Bulunan ve düzeltilen hata: `SmartTraderTracker` kapatılmış pozisyonları asla cache'den silmiyor

### Kod incelemesi
`agents/smart_trader_tracker.py` docstring'i açıkça "Her 5 dakikada bir tüm
trader'ların **açık** pozisyonlarını çeker ve cache'ler" diyor. Gerçek akış:

```python
async def _fetch_positions(self, trader: dict) -> bool:
    ...
    positions = resp.json()
    ...
    for pos in positions:
        cid = pos.get("conditionId") or pos.get("market") or pos.get("condition_id")
        ...
        size = float(pos.get("size", 0) or 0)
        if size < 0.001:
            continue
        outcome = str(pos.get("outcome", "") or "").upper()
        net = size if outcome == "YES" else -size
        if cid not in self._positions:
            self._positions[cid] = {}
        self._positions[cid][trader["name"]] = net
        added += 1
```

Bu döngü, API'nin **bu refresh'te döndürdüğü** pozisyonları
`self._positions[cid][trader_name]` içine yazıyor/güncelliyor — ama bir
trader pozisyonunu kapattığında (API artık o pozisyonu döndürmediğinde),
`self._positions` içindeki eski `net` değerini **silen hiçbir kod yok**.
`self._positions` dict'i `__init__`'te `{}` olarak başlatılıyor ve sadece bu
döngüde yazılıyor — hiçbir yerde temizlenmiyor/expire edilmiyor.

Sonuç: `get_signal(condition_id)` (`agents/smart_trader_tracker.py:76-113`),
bir trader gerçekten pozisyonunu kapattıktan **sonra bile**, o trader'ı
sonsuza dek "buyers"/"sellers" listesinde tutmaya ve eski yönde `signal`
üretmeye devam ediyor.

### Etki (canlı yola bağlı, live-wired)
`strategies/arbitrage_engine.py:621-634`:
```python
if self.smart_trader is not None:
    sm = self.smart_trader.get_signal(market.get("condition_id", ""))
    if sm and sm.get("total_traders", 0) > 0 and volume_ratio >= 1.0:
        boost = sm.get("signal", 0) * 0.02
        bayesian_prob = max(0.05, min(0.95, bayesian_prob + boost))
```
Bu blok **her cycle'da, her açık market için** çalışıyor (feature flag yok).
`docs/architecture.md`'nin "SmartTraderTracker -> +/-0.05 boost" olarak
tanımladığı bu sinyal, bir top trader marketten çoktan çıkmış olsa bile
`bayesian_prob`'u yanlış yönde itmeye devam ediyor — bot, artık var olmayan
bir "akıllı para" pozisyonuna göre yön kararı alıyor. `tests/test_smart_
money_boost_survives_reset.py` (16. çalışma) bu boost'un gerçekten
`bayesian_prob`'a ulaştığını zaten doğrulamıştı; bu inceleme, ulaşan sinyalin
kendisinin süresiz olarak bayatlayabildiğini gösteriyor.

`agents/top_trader_signal.py::TopTraderTracker` (aynı kategori, ayrı sınıf)
karşılaştırma için incelendi: o her refresh'te **son 200 global trade**'den
market başına sıfırdan (`market_trades = defaultdict(...)`) bir sinyal
hesaplayıp `self._cache[cid] = signal` ile üzerine yazıyor — birikimli bir
pozisyon durumu tutmuyor, dolayısıyla bu spesifik "asla silinmeyen state"
hatasından etkilenmiyor (ayrı, daha küçük bir "market son 5dk'da trade
görmüyorsa cache eskir" davranışı var ama bu, veri akışının doğası gereği
kabul edilebilir bir sınırlama — SmartTraderTracker'daki gibi "asla
güncellenmeyen state" değil).

### Doğrulama (A/B)
`tests/test_smart_trader_stale_position_purge.py` (yeni, 2 test):
1. `test_closed_position_is_purged_from_cache` — bir trader $100 YES
   pozisyonu açar (`get_signal` → `signal=1.0`, `buyers=[trader]`), sonra API
   aynı market için hiç pozisyon döndürmez (kapatılmış). Fix öncesi:
   `get_signal` hâlâ `signal=1.0` döndürüyor (**FAIL**, gerçekten çalıştırılıp
   doğrulandı: `assert 1.0 == 0.0`). Fix sonrası: `signal=0.0`,
   `total_traders=0` (**PASS**).
2. `test_position_flip_replaces_not_accumulates` — aynı trader YES'ten
   NO'ya geçtiğinde eski YES kaydının doğru şekilde değiştiğini (bu senaryo
   zaten fix öncesi de doğru çalışıyordu, çünkü aynı dict anahtarının üzerine
   yazılıyor) doğrulayan tamamlayıcı test.

### Fix
`agents/smart_trader_tracker.py::_fetch_positions()`: API yanıtı başarıyla
ayrıştırıldıktan hemen sonra, bu trader'ın **tüm** marketlerdeki önceki
cache girdilerini temizleyip (`market_positions.pop(name, None)` for tüm
`self._positions.values()`), ardından döngüde sadece bu refresh'te dönen
gerçekten açık pozisyonları yeniden ekliyoruz. Böylece kapanmış bir pozisyon
API'den düşünce cache'den de düşüyor; başka trader'ların aynı marketteki
girdilerine dokunulmuyor (farklı dict anahtarı). Temizleme, ağ çağrısı
başarıyla tamamlandıktan (ve `positions` bir liste olduğu doğrulandıktan)
**sonra** yapılıyor — geçici bir API hatası mevcut cache'i sıfırlamıyor.

## Doğrulama
- `pytest tests/test_smart_trader_stale_position_purge.py` → fix öncesi 1
  fail + 1 pass, fix sonrası **2 passed**.
- `pytest tests/` → **628 passed, 2 skipped** (626 → 628: 2 yeni test,
  mevcut testlerden hiçbiri bozulmadı).
- `git diff agents/smart_trader_tracker.py` → tek, minimal değişiklik (8
  satır ekleme), başka hiçbir davranış değiştirilmedi.
- Test çalıştırmalarının yan etkisi olan `data/autonomous_state.json`
  commit öncesi eski haline döndürüldü.

## Sonuç
21. çalışma, önceki 20 incelemenin hiç tek tek bakmadığı
`agents/smart_trader_tracker.py`'de gerçek ve canlı yola bağlı bir hata
buldu: kapatılmış top-trader pozisyonları cache'den asla silinmiyor, bu da
`bayesian_prob`'un süresiz olarak bayat "akıllı para" sinyaliyle
beslenmesine yol açıyor. Minimal bir düzeltmeyle (trader başına stale
girdileri her başarılı refresh'te temizle) kapatıldı ve iki regresyon
testiyle kilitlendi.
