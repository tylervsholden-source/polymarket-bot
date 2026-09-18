# Günlük Strateji İncelemesi — 2026-09-18 (86. tur)

## Durum
Oturum başında `origin/main` = bu branch = `a2662e8` (#149, 85. inceleme
sonrası — `Orchestrator._analyze_new_closed_trades()`'in sim/paper modda
`_current_closed_trades()` yerine doğrudan `position_manager.data["closed"]`
okuyup TradeAnalyzer'ı fiilen kalıcı olarak devre dışı bıraktığı düzeltilmiş).
Açık PR yoktu. Bu ortamda baseline (gerekli bağımlılıklar — loguru, pytest,
numpy/scikit-learn vb. — kuruldu): `python3 -m pytest -q` →
**1695 passed, 4 skipped**.

## Bu turda yapılanlar
85. turun düzeltmesi `self._sim_results`'ı (EXPIRED sonuçlar dahil) ilk kez
`TradeAnalyzer.analyze_trade()`'e ulaştırdığı için, o yeni veri yolunun
aşağı akışını (`analyze_trade()`'in `result` alanını nasıl yorumladığı)
bağımsız olarak yeniden doğrulamaya odaklanıldı.

### Bulunan ve düzeltilen hata: `TradeAnalyzer.analyze_trade()` EXPIRED (resolve olmamış) sim trade'lerini LOSS olarak etiketliyordu

`agents/orchestrator.py::_check_sim_resolutions()`, bir sim trade'in market'i
45dk+ resolve olmadığında `trade["result"] = "EXPIRED"` yazıyor (CLOB
`tokens.winner` alanı boş, orderbook mid-price 0.15-0.85 "UNCLEAR" bandında
kalmış). Bu trade dict'inde **hiçbir zaman** `"pnl"` anahtarı set edilmiyor
— ne trade oluşturulurken (`_sim_trades.append(sim_entry)`, satır ~1111), ne
de EXPIRED dalında (satır 1997-2003, sadece `result` ve `final_yes_price`
yazılıyor).

`agents/trade_analyzer.py::analyze_trade()` ise `result` alanını sadece
`("WIN", "LOSS", "NEUTRAL")` kümesinde tanıyordu:

```python
result = trade.get("result")
if result in ("WIN", "LOSS", "NEUTRAL"):
    analysis.outcome = result
else:
    analysis.outcome = "WIN" if analysis.pnl > 0 else "LOSS"
```

`"EXPIRED"` bu kümede olmadığından else dalına düşüyor; `trade.get("pnl", 0)`
eksik anahtar yüzünden `0.0` döndüğünden `0 > 0` her zaman `False` — yani
**her EXPIRED kapanış kalıcı olarak LOSS olarak etiketleniyordu**. Bu, 25.
turda NEUTRAL için düzeltilen aynı hata ailesi (`bdaaf2a`), ancak EXPIRED
için hiç düzeltilmemiş bağımsız bir kopyası.

**Neden önemli**: `is_resolved = analysis.outcome in ("WIN", "LOSS")`
(`_evaluate_signal_accuracy`) EXPIRED'i "resolve olmuş bir LOSS" sayıyor ve
whale/regime/smart-money sinyal doğruluk istatistiklerini kirletiyor;
`_determine_root_cause()` LOSS dalına düşüp "NO_THIN_EDGE"/"DIRECTION_ERROR"
gibi anlamsız kök nedenler üretiyor; `_match_pattern()` NO_TRAP /
LOW_EDGE_LOSS gibi `outcome == "LOSS"` gerektiren pattern'leri hiç
gerçekleşmemiş bir trade için eşleştirip `stats.losses`'i artırıyor — bu da
`AutonomousDecisionEngine`'in `get_recommendations()` üzerinden beslendiği
öğrenme döngüsünü kirletiyor. 85. turdan önce bu kod yolu sim/paper modda
(botun fiili varsayılanı) hiç tetiklenmediği için ulaşılamazdı; 85. turun
fix'i bunu ilk kez ulaşılabilir kıldı.

**Fix** (`agents/trade_analyzer.py::analyze_trade`):
```python
if result in ("WIN", "LOSS", "NEUTRAL"):
    analysis.outcome = result
elif result == "EXPIRED":
    analysis.outcome = "NEUTRAL"
else:
    analysis.outcome = "WIN" if analysis.pnl > 0 else "LOSS"
```
EXPIRED, NEUTRAL ile aynı anlama geliyor (yön hakkında bilgi taşımayan bir
kapanış) ve zaten var olan tüm NEUTRAL-aware dallardan (root cause,
signal-accuracy `is_resolved` guard'ı, pattern stats) doğru şekilde geçiyor.
Başka mantık değiştirilmedi.

**Test**: `tests/test_trade_analyzer_expired_not_loss.py` (3 test,
`test_trade_analyzer_neutral_not_loss.py`'nin deseni izlenerek) — EXPIRED
sonuçlu bir trade'in `outcome == "NEUTRAL"` (LOSS değil) olduğunu, root
cause'un LOSS mantığını çalıştırmadığını, ve NO_TRAP/LOW_EDGE_LOSS pattern
istatistiklerini kirletmediğini doğruluyor. Tam suite: **1698 passed, 4
skipped** (baseline 1695/4 + 3 yeni test).

## Sıradaki tur için notlar
Ayrıca, aynı denetimde `orchestrator.py::_check_hot_reload()`'un yeni
`ArbitrageEngine` nesnesini yalnızca `self.arb_engine`'e atadığı,
`self.coordinator.signal_agent.arb_engine`'i (sinyallerin fiilen üretildiği
referans) güncellemediği — bu yüzden hot reload'un sinyal üretimi için
sessizce no-op olduğu ve reload sonrası adaptif min-edge/Dynamic-Kelly
streak güncellemelerinin "öksüz" nesne üzerinde kalıcı olarak donduğu — bir
aday incelendi. Gerçek ama etkisi `reload_engine` bayrağının elle
`data/control.json`'a yazılmasına bağlı (hiçbir otomatik yol tetiklemiyor),
bu yüzden bu turda önceliklendirilmedi; bir sonraki turda ele alınabilir
(fix taslağı: `_check_hot_reload()` içinde yeni engine'i
`self.coordinator.signal_agent.arb_engine`'e de ata ve `latency_arb`'ı
yeniden bağla).
