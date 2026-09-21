# Günlük Strateji İncelemesi — 2026-09-20 (105. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = `230c718` (104. turun tüm PR'ları — #187-196
ve 102. turdan kalan #183 — merge edilmişti). Bu branch
(`claude/brave-faraday-4hre3w`) `origin/main` ile tam eşit başladı, açık
fark yoktu.

Konteynerde çalışan bir bot instance'ı yok (`data/status.json`/
`control.json`/`positions.json` bu sandbox'ta yok, canlı Polymarket/
Anthropic API'lerine ağ erişimi yok) — %10 hedefine karşı gerçek zamanlı
sermaye ilerlemesi bu oturumdan doğrulanamıyor; katkı kod/strateji
doğruluğu seviyesinde kalıyor (104 turdur değişmeyen kısıt).

## Baseline doğrulama
`pip install -r requirements.txt` + `python3 -m pytest tests/
calibration/tests execution_realism/tests crypto_directional/tests
signal_bridge/tests -q` → **1784 passed, 4 skipped** — 104. turun bitiş
durumuyla birebir aynı (0 regresyon, main'de bu turdan önce beklenmedik
değişiklik yok).

## Bu turda incelenen alan

104. turun "sıradaki tur için notlar" bölümünün "henüz derinlemesine
taranmamış adaylar" listesinden 14 dosya, iki paralel alt-ajanla satır
satır incelendi (her dosyanın gerçek tüketicileri grep ile izlendi, aday
hatalar repro/test ile doğrulanmaya çalışıldı):

- `strategies/{quality_filter,orderbook_analyzer,sum_monitor,bond_scanner,
  walk_forward,stoikov}.py`
- `shadow_runner/{journal,reporting,types}.py`
- `monitoring/{readiness_checks,regime_review,drift_monitor,metrics,
  alerts}.py`

**Sonuç: yeni bir canlı-karar hatası bulunamadı.** İki aday bulundu, ikisi
de daha önceki turlarda zaten tespit edilip kasıtlı olarak ertelenmiş
kararlar — aşağıda doğrulandı, tekrar "yeni" olarak raporlanmadı:

### 1. `strategies/orderbook_analyzer.py::_estimate_slippage()` — matematik hatası ama ölü alan
`total_cost` (dolar) / `remaining` (dolar) oranı hep ≈1.0 dönüyor (pay ve
payda ikisi de dolar, share sayısı değil) — derinlik/fiyattan bağımsız.
Repro ile doğrulandı. Ancak `result.slippage_5`/`slippage_10` hiçbir yerde
`orderbook_analyzer.py` dışında okunmuyor; `arbitrage_engine.py`'nin
`get_signal_boost()` çağrısı sadece `.tradeable`/`.imbalance`/
`.liquidity_score` kullanıyor (bunlar doğru). Bu hata zaten
2026-09-15-...-48, 2026-09-19-...-87b ve 2026-09-18-...-consolidation-9
turlarında bulunmuş, ölü kod olduğu için düzeltme önceliklendirilmemiş.
Bu turda da aynı gerekçeyle dokunulmadı — canlı karara sıfır etkisi var.

### 2. `strategies/quality_filter.py` — canlı orchestrator'a hiç bağlı değil
`agents/orchestrator.py:733-736`'daki kendi yorumu bu kopukluğu zaten kabul
ediyor ("QualityFilter doğru yazılmış ama sadece scan_markets.py'de
kullanılıyordu, canlı orchestrator'a hiç bağlı değildi") — daha önce
bilinçli olarak orchestrator'a daha dar bir min-volume kontrolü
(`self.min_market_volume`) hardcode edilerek çözülmüş, `QualityFilter`
kasıtlı olarak bağlanmamış. `check()`'in whale-alignment kapısının
yön-agnostik olması (her zaman YES-taraf varsayıyor) da sadece
`scan_markets.py` (deprecated Claude/whale yığını, `docs/architecture.md`
"DEVRE DISI" listesinde) tarafından çağrıldığı için şu an latent/etkisiz.

### 3. `monitoring/drift_monitor.py` + `monitoring/alerts.py` — bilinen ölü kod, yeni değil
İki alt-ajan da bunu "104. tur Regime Decay Guard'la aynı sınıf" bir
canlı-hata adayı olarak flagledi (hesaplanıyor ama hiçbir yere yazılmıyor/
loglanmıyor/dashboard'a yansımıyor). Ancak `docs/reviews/
2026-09-16-strateji-incelemesi-57.md`'nin "Kapsam dışı bırakılanlar" ve
"Sıradaki tur için notlar" bölümleri bunu 2026-09-16'da (89 tur önce) zaten
tespit etmiş ve açıkça bir **mimari karar** olarak kullanıcıya bırakmış:
"ileride DriftMonitor'ı canlıya bağlama kararı verilirse önce bu
modüllerin kendisi tekrar satır satır incelenmeli." 89 tur boyunca bu karar
hiç verilmemiş — bilinçli erteleme, gözden kaçmış bir hata değil. Bu turda
tekrar doğrulandı (hâlâ hiçbir import yok) ama yine "yeni hata" olarak
sayılmadı; mimari karar kullanıcıya açık kalmaya devam ediyor.

### 4. `monitoring.daily_review.py::write_readiness_verdict()` zinciri — yine kontrol edildi, yine kasıtlı
İkinci alt-ajan bunun hiçbir yerden çağrılmadığını (dolayısıyla
`data/readiness_verdict.json`'ın otomatik hiç üretilmediğini, canlı
readiness gate'inin otomatik yolda her zaman fail-closed kaldığını)
bağımsız olarak yeniden keşfetti. Bu, `docs/reviews/
2026-09-20-strateji-incelemesi-104e.md` (madde 3), `-103.md`, `-88.md` ve
`-2026-09-16-...-57.md`'de defalarca doğrulanmış, **kasıtlı bir tasarım**:
`control_plane/types.py`'nin INC-2026-03-15-001 sonrası doktrini ("sinyal
→ emir arasında insan onayı zorunlu") ve `shadow_runner/readiness.py`'nin
"GO verdict yalnızca 3 günlük, tek-varlık, $10 max pozisyon pilot'u
yetkilendirir" tasarım notuyla tam tutarlı — insan operatörün
`generate_daily_review()` + `write_readiness_verdict()`'i elle çalıştırıp
gözden geçirmesi gerekiyor. Düzeltme gerektirmiyor, tekrar dokunulmadı.

### Diğer 9 dosya: temiz
`sum_monitor.py`, `bond_scanner.py`, `walk_forward.py`, `stoikov.py`,
`shadow_runner/{journal,reporting,types}.py`, `monitoring/
{readiness_checks,regime_review,metrics}.py` — hepsi satır satır okundu,
tüketicileri izlendi, formül/sınır/sign kontrolleri doğrulandı. Hiçbir yeni
hata bulunamadı (detaylar alt-ajan raporlarında; kısaca: bond_scanner NO-taraf
padding testle uyumlu, walk_forward train/test split çakışmasız, stoikov
inventory-skew formülleri iç tutarlı, shadow_runner JSONL round-trip
simetrik, readiness_checks hiçbir eksik veriyi GREEN'e maplemiyor).

## Doğrulama
İki alt-ajan da salt-okunur inceleme yaptı (dosya değişikliği yok). Bu
turda kod değişikliği olmadığından ek bir pytest çalıştırmasına gerek
kalmadı — yukarıdaki baseline (1784 passed, 4 skipped) bu turun başı ve
sonu için geçerli.

## Sonuç
105. tur, 104. turun bıraktığı 14 taranmamış aday dosyayı tükeninceye kadar
inceledi ve **yeni bir canlı-karar hatası bulmadı**. İki alt-ajanın
bulduğu adaylar (orderbook_analyzer slippage matematiği, quality_filter
kopukluğu) zaten bilinen ölü kod; DriftMonitor/AlertEngine ve
write_readiness_verdict zinciri zaten kullanıcıya açık bırakılmış mimari
kararlar. Kod değişikliği yapılmadı; bu PR sadece bu turun bulgularını
belgeliyor (105/104 turlarının kurduğu konvansiyonla aynı).

## Sıradaki tur için notlar
- **Taranmış, temiz — tekrar taranmasına gerek yok**: `strategies/
  {quality_filter,orderbook_analyzer,sum_monitor,bond_scanner,
  walk_forward,stoikov}.py`, `shadow_runner/{journal,reporting,types}.py`,
  `monitoring/{readiness_checks,regime_review,drift_monitor,metrics,
  alerts}.py` (104. turun listesinden devralınan, artık tamamı işlendi).
- **Kullanıcı kararı bekleyen açık mimari sorular** (104. turdan devralınan,
  hâlâ çözülmedi): (1) onay kuyruğu/doğrudan emir yolu çelişkisi
  (`agents/orchestrator.py` ~1126 vs `docs/APPROVAL_WORKFLOW_SPEC.md`),
  (2) `agents/enhanced_signals.py`'nin confluence/risk-flag'e hiç bağlı
  olmaması, (3) `agents/copytrade.py`'nin tamamen ölü kod olması (silinsin
  mi, bağlansın mı), (4) `agents/top_trader_signal.py`'nin `TOP_TRADERS`
  listesinin hiç kullanılmaması, (5) — **yeni eklenen** —
  `monitoring/drift_monitor.py`/`alerts.py`'nin canlıya bağlanıp
  bağlanmayacağı (2026-09-16'dan beri, 89 tur boyunca açık).
- **Henüz derinlemesine taranmamış adaylar**: `agents/{latency_arb,
  market_index_watcher,context_fetcher}.py` (104. turun listesinden kalan,
  bu turda kapsam dışı bırakıldı).
- Dosya adı çakışmasını önlemek için bu tur, 104. turun önerdiği gibi
  dosya adına branch kimliğinin bir parçasını (`4hre3w`) baştan ekledi —
  gelecekteki paralel oturumlar da aynı konvansiyonu izleyebilir.
