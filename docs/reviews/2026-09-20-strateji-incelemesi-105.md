# Günlük Strateji İncelemesi — 2026-09-20 (105. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum branch `claude/brave-faraday-23nn45` üzerinde başladı, HEAD =
`230c718` (#196 dahil, 104. turun konsolidasyon zincirinin tamamı içeride).
`origin/main` bu branch'in 97 commit gerisinde — beklenen, çünkü bu branch
önceki turların tamamını taşıyor.

Konteynerde çalışan bir bot instance'ı yok: `data/control.json`,
`data/status.json`, `data/positions.json` bu sandbox'ta **yok**
(`data/` altında `positions.json.bak`, `positions.json.bak2`,
`autonomous_state.json`, `bot_log.txt` gibi eski/yardımcı dosyalar var ama
canlı üçlü — control/status/positions.json — yok), gerçek Polymarket/
Anthropic API'lerine ağ erişimi de yok. Yani %10 hedefine karşı gerçek
zamanlı sermaye ilerlemesi bu oturumdan **doğrulanamıyor** — 100'den fazla
önceki tur bunu tutarlı şekilde tespit etti, bu tur da aynı sonucu
doğruluyor (yeni bir bulgu değil).

## Baseline doğrulama
`pip3 install -r requirements.txt` + `python3 -m pytest tests/
calibration/tests execution_realism/tests crypto_directional/tests
signal_bridge/tests -q` → **1784 passed, 4 skipped** (104-consolidation'ın
bıraktığı sayıyla birebir aynı — beklenen, bu turda kaynak kod
değişmediği için baseline ve final aynı).

## Bu turda incelenen dosyalar
104h'nin işaret ettiği, review zincirinde en uzun süredir "derinlemesine"
taranmamış üç aday satır satır okundu ve canlı karar yoluna gerçekten
bağlı olup olmadıkları `agents/orchestrator.py`,
`agents/subagents/coordinator.py`, `agents/subagents/research_agent.py` ve
`strategies/arbitrage_engine.py` üzerinden izlendi:

### `agents/latency_arb.py` (548 satır)
`grep -rl maker_engine/latency_arb docs/reviews/` ve `git log` kontrolü:
dosyanın kendisi 16., 47., 72. ve 98. turlarda gerçek düzeltmeler almış
(en sonuncusu — `get_spike_boost()`'un BTC'yi hiç eşleştirmemesi —
98. turda düzeltilmiş, kod içinde ayrıntılı yorumla belgeli). Bu turda
tüm dosya yeniden satır satır okundu, düzeltmeler hâlâ yerinde.

Canlı bağlantı doğrulandı: `orchestrator.py` `LatencyArbEngine`'i
her zaman (env flag'siz) instantiate edip `run()` içinde
`await self.latency_arb.start()` ile başlatıyor; `self.arb_engine.
latency_arb = self.latency_arb` ile `arbitrage_engine.py`'ye sinyal
kaynağı olarak bağlanıyor (satır 302-314, 469-476).

**Yeni gözlem (düzeltme gerektirmiyor — tasarım gereği zaten kapalı)**:
`_find_market_for_spike()` ve `_can_trade()` metodları (dosyanın kendi
docstring'inin tarif ettiği "4. Aktif market bul, 5. Spike yönüne göre
YES/NO al" adımları — bağımsız/anlık emir verme yolu) dosyanın **hiçbir
yerinden çağrılmıyor** — tamamen ulaşılamaz kod. Gerçek canlı yol sadece
`get_spike_boost()` üzerinden `arbitrage_engine.py`'ye bir Bayesian boost
sağlamak (satır 803-805) — ve bu boost da `arbitrage_engine.py`'nin kendi
"TÜM EXTERNAL BOOST'LAR DEVRE DIŞI" bloğunda (16. turda bilinçli olarak
kapatılmış, iyi belgelenmiş) `bayesian_prob`'a hiç uygulanmıyor, sadece
log basılıyor (`"(DISABLED)"`). Yani `latency_arb.py`'nin şu anki canlı
karara net etkisi **sıfır** — bu, tasarımın kendisi (isim: "signal
source → Bayesian boost", satır 314) ve önceki turların kayıtlı
kararlarıyla tutarlı, hata değil. `_find_market_for_spike`/`_can_trade`
ölü kod olarak not edildi, CLAUDE.md'nin sadelik kuralı gereği
dokunulmadı (silme de bu turun kapsamı dışında).

### `strategies/maker_engine.py` (489 satır)
Görev talimatı bu dosyayı "hiç incelenmemiş" olarak nitelendirmişti;
`grep -rl maker_engine docs/reviews/` bunun **doğru olmadığını** gösterdi
— dosya 25 review dosyasında geçiyor ve `git log -- strategies/
maker_engine.py` iki gerçek düzeltme commit'i taşıyor: 72. tur
(`_cancel_all_standing()`'in kısmi fill'i cancel_order()==True dalında
kaçırması) ve 98. tur (`get_committed_capital()`'ın çözülmüş market
inventory'sini hiç süresi doldurmaması → `maker_capital` sonsuza kadar
$0'a kilitlenmesi). Her iki düzeltme de kod içinde ayrıntılı yorumla
belgeli ve bu turda satır satır yeniden doğrulandı — hâlâ yerinde,
regresyon yok. `_quote_market()`'taki `MAX_INVENTORY_PER_SIDE` kapasite
kontrolü (`yes_room`/`no_room`) de zaten düzeltilmiş halde.

Canlı bağlantı: `orchestrator.py` satır 341-344'te `MAKER_ENABLED` env
var'ı ile (varsayılan `false`, kod içinde "KAPALI" yorumu) koşullu
instantiate ediliyor; açık olduğunda `_cycle()` içinde
(satır 1263-1294) günlük stop-loss, process-lock ve pozisyon-limiti
guard'larının **hepsinden sonra** çalışıyor — 43. turun bond-scanner için
kurduğu guard-sırası örüntüsüyle tutarlı, `MAKER_ENABLED=true` olsa bile
kayıp günü/acil-durdurma'yı atlamıyor. Yeni bir hata bulunamadı.

### `agents/market_index_watcher.py` (177 satır)
En son 88. turda değinilmiş (~17 tur önce), o zamandan beri dosyanın
kendisi hiç değişmemiş (`git log` — tek commit, ilk ekleme). Satır satır
okundu: `_refresh()`'in yfinance multi-ticker `Close` sütunu seçimi,
`get_context()`'in keyword eşleştirmesi, gram altın türevi hesabı — hepsi
doğru görünüyor.

Canlı bağlantı izlendi — iki farklı tüketici var:
1. `orchestrator.py`: `market_watcher.run()` arka planda başlatılıyor,
   `_data` dashboard'a (`_sw.update_indices`) yazılıyor — bu yol sağlıklı.
2. **`agents/subagents/research_agent.py::_extract_global_indices()`**
   (satır 369-379) — `ResearchResult.global_indices` alanını doldurmak
   için çağrılıyor, `ResearchAgent.execute()`'in her cycle'da çalıştırdığı
   ana yol üzerinde.

**Yeni gözlem — gerçek ama sıfır canlı etkili bug**:
`_extract_global_indices()` `market_watcher.get("sp500", 0.0)` gibi
çağrılar yapıyor, ama `market_watcher` bir `dict` değil
`MarketIndexWatcher` **singleton instance**'ı — sınıfın hiçbir yerinde
`.get()` metodu tanımlı değil (veri `self._data` attribute'unda,
"S&P500"/"NASDAQ" gibi büyük harfli anahtarlarla tutuluyor; "btc_dom" ise
`INDICES` sözlüğünde hiç tanımlı bile değil — BTC dominance hiç
izlenmiyor). Bu yüzden her çağrı `AttributeError` fırlatıyor, fonksiyonun
kendi `try/except Exception: return {}` bloğu bunu sessizce yutuyor, ve
`_extract_global_indices()` **her zaman boş dict döner**. `grep -rn
"global_indices" agents/` bu alanın `ResearchResult` dataclass'ına
yazıldıktan sonra (`result.global_indices = ...`, satır 211) **hiçbir
yerde okunmadığını** gösterdi — `coordinator.py`, `signal_agent_v2.py`,
`reviewer_agent.py` içinde `global_indices` adı hiç geçmiyor. Yani bu,
104. turda `enhanced_signals.py`'nin `max_pain`/`fear_greed`
alanları için tespit edilenle birebir aynı sınıfta: gerçek bir kod hatası
(yanlış API kullanımı + yanlış anahtarlar) var, ama sonucu hiçbir yerde
tüketilmediği için canlı trading kararı üzerinde **sıfır** etkisi var.
CLAUDE.md'nin sadelik kuralı ve 104. turun aynı sınıftaki bulgular için
kurduğu emsal gereği (rapor et, dokunma — düzeltmek gerçek tasarım
kararları: hangi anahtarlar, BTC dominance'ın nereden geleceği vs.
gerektiriyor ve hiçbir davranışı değiştirmiyor) bu turda düzeltilmedi.

## Sonuç — bu turda kod değişikliği YOK
Üç hedef dosya (`latency_arb.py`, `maker_engine.py`,
`market_index_watcher.py`) ve bunların canlı yoluna bağlandığı
`orchestrator.py`/`coordinator.py`/`research_agent.py`/
`arbitrage_engine.py` satır satır incelendi. Pozisyon boyutlandırma, yön
(YES/NO), risk-gate'leme veya sermaye muhasebesini bozan **gerçek,
düzeltmesi gereken** bir canlı-karar hatası bulunamadı — her üç dosya da
önceki turlarda (16, 47, 72, 88, 98) zaten kapsamlı şekilde incelenip
düzeltilmiş, düzeltmeler hâlâ yerinde ve regresyon yok. Tek yeni somut
bulgu (`research_agent.py::_extract_global_indices()`'in her zaman boş
dönmesi) gerçek bir kod hatası ama sıfır canlı etkili — 104. turun
`enhanced_signals.py` bulgusuyla aynı "belgele, dokunma" sınıfında.

## Tam suite
Baseline (oturum başı) ve bu turun sonu aynı: **1784 passed, 4 skipped**
(0 regresyon, 0 yeni test — beklenen, çünkü hiçbir kaynak dosya
değişmedi).

## Sermaye/performans notu
Bu oturumda çalışan bir bot instance'ı yok (`data/control.json`,
`data/status.json`, `data/positions.json` sandbox'ta yok) — %10 hedefine
karşı gerçek ilerleme bu oturumdan doğrulanamıyor; 100'den fazla önceki
turda tutarlı şekilde tespit edilen aynı durum. Önceki turlarda
düzeltilen kritik canlı-karar hataları (Regime Decay Guard, SpreadModel
z-score self-inclusion, candlestick containment, latency_arb BTC
eşleştirmesi, maker_engine committed-capital/partial-fill düzeltmeleri)
hâlâ yerinde ve testlerle korunuyor.

## Sıradaki tur için notlar
- `research_agent.py::_extract_global_indices()`'in gerçekten
  bağlanması (doğru `market_watcher._data` erişimi + gerçek anahtarlar +
  `global_indices`'in confluence/risk-flag hesabına dahil edilmesi) yeni
  bir sinyal-tasarım kararı — kullanıcı onayı olmadan bu turda
  otomatik yapılmadı, `enhanced_signals.py` ile aynı kategoride bekliyor.
- `agents/latency_arb.py::_find_market_for_spike()`/`_can_trade()` —
  dosyanın erken "anlık emir" tasarımından kalma, artık hiç çağrılmayan
  ölü kod; silinip silinmeyeceği bir temizlik kararı.
- 104-consolidation'ın listelediği, henüz derinlemesine taranmamış
  adaylar hâlâ geçerli: `shadow_runner/{journal,reporting,types}.py`,
  `monitoring/{readiness_checks,regime_review,drift_monitor,metrics,
  alerts}.py`, `strategies/{quality_filter,orderbook_analyzer,
  sum_monitor,bond_scanner,walk_forward,stoikov}.py`,
  `agents/context_fetcher.py`.
