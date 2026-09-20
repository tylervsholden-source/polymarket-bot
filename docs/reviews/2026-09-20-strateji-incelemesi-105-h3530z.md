# Günlük Strateji İncelemesi — 2026-09-20 (105. tur, 3. paralel oturum)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum branch `claude/brave-faraday-h3530z` üzerinde başladı, HEAD = `230c718`
(104. turun tüm PR'ları merge edilmişti). Aynı taban üzerinde iki paralel
105. tur oturumu daha PR açmış durumda: #197 (`monitoring/regime_review.py`
tek-pencere `is_stable` fix'i + `strategies/{quality_filter,
orderbook_analyzer,stoikov}.py`, `monitoring/{drift_monitor,metrics,
alerts}.py` taraması) ve #198 (`agents/{latency_arb,market_index_watcher,
context_fetcher}.py`, `strategies/maker_engine.py`,
`shadow_runner/{journal,reporting,types}.py` taraması, kod değişikliği yok).

Konteynerde çalışan bir bot instance'ı yok (`data/control.json`/
`status.json`/`positions.json` sandbox'ta yok, canlı Polymarket/Anthropic
API erişimi yok) — %10 hedefine karşı gerçek zamanlı sermaye ilerlemesi bu
oturumdan doğrulanamıyor (100'den fazla önceki turda tutarlı tespit).

## Kapsam
104-consolidation'ın taranmamış aday listesinden, #197/#198'in bu turda
henüz dokunmadığı üç dosya satır satır incelendi ve canlı karar yoluna
bağlantıları doğrulandı:
- `strategies/sum_monitor.py` → `strategies/arbitrage_engine.py`
- `strategies/bond_scanner.py` → `agents/orchestrator.py::_bond_cycle()`
- `strategies/walk_forward.py` → `agents/orchestrator.py::_cycle()`

## Bulunan ve düzeltilen gerçek hata

**`strategies/arbitrage_engine.py` — OVERPRICED MARKET BLOCK yorumla
çelişen bir eşik kullanıyordu (1.10 yerine 1.50)**

Bloğun kendi yorumu şunu söylüyor: `"YES+NO > 1.10 = market maker spread
too wide, edge is fake"`. Ama kod `_sum_total > 1.50` kontrolü yapıyordu —
yorumla kod arasında 40 sentlik bir tutarsızlık. Bu, tek bir yerel yazım
hatası değil; **üç bağımsız kaynak** aynı 1.10 değerini doğruluyor:

1. Bloğun kendi inline yorumu (yukarıda).
2. `docs/PRICING_SANITY_SPEC.md` — LIVE profili için `max_ask_sum = 1.10`
   (farklı bir modül, `calibration/decision_policy.py`, için yazılmış olsa
   da aynı YES+NO toplamı kavramını tanımlıyor ve aynı sayıyı veriyor).
3. `tests/test_no_side_execution_path.py`'nin kendi yorumu:
   `"YES+NO > 1.10 → OVERPRICED_BLOCK returns None"` — test yazarı da
   eşiği 1.10 olarak biliyordu ve test market'lerini bilinçli olarak bunun
   altında tuttu.

**Canlı etki:** Bu blok, `_evaluate_market()`'in erken bir aşamasında,
gerçek orderbook'tan gelen `no_best_ask` mevcutsa çalışıyor ve YES+NO
toplamının aşırı saptığı (piyasa yapıcısının spread'inin çok geniş olduğu,
dolayısıyla hesaplanan "edge"in sahte olduğu) marketleri trade'den önce
eleyecek şekilde tasarlanmış. 1.50 eşiğiyle, toplamı 1.10–1.50 arasında
olan (yani %10–%50 aşırı fiyatlanmış / likidite sorunlu) marketler bu
korumayı hiç tetiklemeden gerçek emir akışına ulaşabiliyordu — tam da
yorumun engellemek istediği senaryo.

**Düzeltme:** Eşik `1.50` → `1.10` olarak düzeltildi (kod artık kendi
yorumu, `PRICING_SANITY_SPEC.md` ve mevcut test yorumlarıyla tutarlı).

**Test doğrulaması (regresyon yok):** Tüm mevcut testler tarandı —
`tests/test_no_side_execution_path.py` ve `tests/test_no_side_diagnostics.py`
içinde YES+NO toplamı 1.10'un üzerinde olan iki senaryo (`0.55+0.99=1.54`,
`0.55+0.91=1.46`) zaten gevşek assertion kullanıyor
(`signal is not None or signal is None` / yorum satırı "either outcome
acceptable") — bu testler yeni davranışla da geçiyor. `test_no_side_
execution_path.py`'deki `best_ask=0.55, no_best_ask=0.55` (toplam=1.10,
tam sınırda) testi `>` (dahil değil) operatörü nedeniyle etkilenmedi.
Yeni `tests/test_overpriced_block_threshold.py` (3 test) eklendi: eşiğin
üzerinde blok, tam sınırda geçiş, ve önceden kaçan 1.10–1.50 aralığının
artık bloklandığını doğruluyor.

## İncelenen ama düzeltme gerektirmeyen bulgular

- `strategies/sum_monitor.py`: mantık doğru; `get_edge_adjustment()`'ın
  `arbitrage_engine.py`'ye uygulanması önceki bir turda bilinçli olarak
  devre dışı bırakılmış (`# FIX: SUM_MONITOR boost DISABLED`) — davranış
  hatası değil, kayıtlı bir tasarım kararı.
- `strategies/bond_scanner.py`: `_evaluate_market()`'in YES/NO bant
  hesapları (PROB_THRESHOLD/MAX_PRICE/MIN_YIELD) ve NO-tarafı sentetik
  fiyat konservatif payı (+0.02, önceki turda düzeltilmiş) tutarlı.
  `agents/orchestrator.py::_bond_cycle()`'daki guard sırası (günlük
  stop-loss → process-lock → pozisyon limiti → bond-özel limitler) diğer
  tüm emir yollarıyla aynı desende. Yeni hata bulunamadı.
- `strategies/walk_forward.py`: NEUTRAL kapanışların WR paydasından
  dışlanması (44./46. review'da autonomous_engine.py/trade_analyzer.py
  için yapılan düzeltmenin burada da uygulandığı, kod içi yorumla
  belgeli) ve STOP/HALF_SIZE eşiklerinin "death spiral" düzeltmesi
  (kod içi yorum) hâlâ yerinde, tutarlı. `agents/orchestrator.py`'deki
  bağlantı noktası (`closed_trades` kaynağı, `_walk_forward.validate()`
  çağrısı) 82-86. review'ların düzelttiği "sim modda hep boş liste" bug
  sınıfından etkilenmiyor (ortak `_current_closed_trades()` kullanıyor).

## Doğrulama
```
python3 -m pytest tests/ calibration/tests execution_realism/tests \
  crypto_directional/tests signal_bridge/tests -q
# 1787 passed, 4 skipped (baseline 1784 + 3 yeni test — 0 regresyon)
```

## Sermaye/performans notu
Bu oturumda çalışan bir bot instance'ı yok — %10 hedefine karşı gerçek
ilerleme doğrulanamıyor. Düzeltilen hata doğrudan risk azaltıcı: gerçek
sermaye canlıya alındığında, aşırı fiyatlanmış/likiditesi bozuk
marketlerin (sahte edge) artık tasarlandığı gibi 1.10 eşiğinde
engellenmesini sağlıyor — önceden 1.10-1.50 aralığındaki böyle marketler
korumasız geçebiliyordu.

## Sıradaki tur için notlar
104/105'ten devreden açık mimari sorular değişmedi (onay kuyruğu/doğrudan
emir yolu çelişkisi, `enhanced_signals.py`/`copytrade.py`/
`top_trader_signal.py` bağlantısızlığı, `latency_arb.py` ölü kod,
`research_agent.py::_extract_global_indices()`'in her zaman boş dönmesi).
Bu turda taranan `sum_monitor.py`/`bond_scanner.py`/`walk_forward.py`
listeden çıkarılabilir. 104-consolidation'ın orijinal listesinden geri
kalan: yok — üç paralel 105. tur oturumu (#197, #198, bu oturum) listeyi
tamamen kapattı.
