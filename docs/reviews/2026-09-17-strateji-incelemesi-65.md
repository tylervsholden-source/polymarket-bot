# Günlük Strateji İncelemesi — 2026-09-17 (65. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum açıldığında `main` üzerinde açık/birleştirilmemiş **PR #110**
(`claude/brave-faraday-89gxp8`, "HANGING_MAN always co-emitted with HAMMER,
zeroing the bearish signal", bugünün 64. çalışması) vardı — o PR sadece
`core/candlestick_analyzer.py`'a dokunuyor ve bu turun kapsamı ona
çakışmıyor.

64. çalışmanın kendi review dokümanı, `agents/` (52), `strategies/` (41),
`core/` (34), `control_plane/` (20), `dashboard/` (14) alanlarına kıyasla
`execution_realism/` (3), `operator_layer/` (3), `monitoring/` (4) ve
`agents/subagents/*.py`, `agents/latency_arb.py`, `agents/kalshi_arb.py`,
`agents/top_trader_signal.py`, `strategies/bond_scanner.py`,
`strategies/maker_engine.py`, `strategies/walk_forward.py`'ın az
incelendiğini işaret ediyordu. Bu tur `monitoring/` alanından başladı.

`docs/reviews/*.md` grep'lendi: `agents/latency_arb.py` (`_find_market_for_spike`/
`_can_trade`) ve `agents/kalshi_arb.py`/`agents/top_trader_signal.py`'nin
tespit edilebilir hataları önceki turlarda ya düzeltilmiş (top_trader_signal
— `conditionId` alan eşleme hatası, 14. çalışma) ya da bilinçli olarak ölü
kod bırakılmış (latency_arb spike→emir yolu hiç çağrılmıyor, 15./48.
çalışmalar) — yeniden dokunmadım. `operator_layer/aggregator.py` ve
`operator_layer/ledgers.py` satır satır okundu; ikisi de yalnızca
dashboard/"Architect Chamber" görselleştirmesi için — canlı emir yoluna
bağlı değiller (`build_chamber_summary()`'yi sadece `operator_layer/api.py`
çağırıyor). `monitoring/daily_review.py::write_readiness_verdict()`'in
docstring'i şunu belirtiyordu: *"The orchestrator reads this file in
`_readiness_clears_live()` before enabling live order flow."* Bu ipucu
`agents/orchestrator.py::_readiness_clears_live()`'a (canlı emir yolunun
asıl kapısı, `_is_live_trading()` → `action="ORDER"` vs `"SIM_BUY"`)
takip edildi — PR #110'un `candlestick_analyzer.py` → `arbitrage_engine.py`
izleme yöntemiyle aynı mantık.

## Bulgu (65.) — Orchestrator'ın kendi readiness-yaş kontrolü, 53. çalışmanın düzelttiği INC-2026-03-15-001 hatasını hâlâ taşıyor

### Kapsam
`agents/orchestrator.py::Orchestrator._readiness_clears_live()` (satır
~2084-2129, düzeltme öncesi). Canlı emir yolunun tek gerçek kapısı olan
`_is_live_trading()` tarafından çağrılıyor.

### Kök neden
`control_plane/live_gate.py::_check_readiness()` içinde **aynı** yaş
kontrolünün bir kopyası var, ve o kopya 53. çalışmada (`6b6806e`,
"readiness gate skipped staleness check entirely when generated_utc was
missing") düzeltildi: `generated_utc` alanı eksik/boşsa artık fail-closed
(`return False`) davranıyor.

Ama `agents/orchestrator.py`'nin **kendi, bağımsız** kopyası
`_readiness_clears_live()` o düzeltmeyi hiç almadı — düzeltme öncesi kod
birebir aynı hatayı taşıyordu:

```python
generated_utc_str = data.get("generated_utc", "")
if generated_utc_str:                      # boşsa/yoksa if bloğunun TAMAMI atlanıyor
    ...
    if age > timedelta(hours=max_age_h):
        return False
return True                                 # generated_utc yoksa direkt buraya düşüyor
```

53. çalışmanın dokümante ettiği tam senaryo (INC-2026-03-15-001: operatör
`readiness_verdict.json`'ı manuel düzenledi/override etti, dosyada
`generated_utc` yoktu, bot 26 saat boyunca dosyayı güvenilir kabul etti)
`control_plane/live_gate.py` üzerinden artık engelleniyor — ama
`agents/orchestrator.py`, `check_live_gate()`'i her zaman çağırmıyor;
kendi `_is_live_trading()` → `_readiness_clears_live()` yolu ayrı bir kod
kopyası ve bu kopya hâlâ "yaşı bilinmiyor" durumunu "sonsuz taze" ile
eşdeğer davranıyor. `tests/test_live_gate.py`'deki 53. çalışma testleri
sadece `control_plane` fonksiyonunu kapsıyordu, orchestrator'ın kendi
kopyasını hiç test etmiyordu (`grep -rn "_readiness_clears_live"
tests/` → sıfır sonuç, düzeltme öncesi).

### Neden önemli
`_readiness_clears_live()`, `LIVE_TRADING_ENABLED=true` ve
`control.json: live_trading=true` olduğunda bottun gerçek para ile emir
verip vermeyeceğini belirleyen tek kontrol. `generated_utc` eksik/boş bir
`readiness_verdict.json` (manuel override, eski format, ya da bir yazım
hatası) bu kontrolü sonsuza kadar geçirir — `READINESS_MAX_AGE_HOURS=26`
tazelik zorunluluğu sessizce devre dışı kalır, ve bot günler/haftalar
eski, artık geçerli olmayan bir "TINY_PILOT_CANDIDATE" verdiktine dayanarak
gerçek emirler vermeye devam edebilir.

### Somut senaryo (doğrulandı, `Orchestrator._readiness_clears_live()` gerçek çalıştırılarak)
`data/readiness_verdict.json` = `{"verdict": "TINY_PILOT_CANDIDATE"}`
(generated_utc alanı yok — tam INC-2026-03-15-001 senaryosu):
- **Düzeltme öncesi**: `_readiness_clears_live()` → `True` (yaş hiç
  kontrol edilmedi, canlı emir yolu açık kalır).
- **Düzeltme sonrası**: `_readiness_clears_live()` → `False` (fail-closed,
  "generated_utc eksik" logu ile canlı işlem engellenir).

Aynı sonuç `generated_utc: ""` (boş string) için de doğrulandı.

### Düzeltme
`agents/orchestrator.py::_readiness_clears_live()`'da, `generated_utc`
alanı boş/yoksa artık erken `return False` ile fail-closed davranıyor —
`control_plane/live_gate.py::_check_readiness()`'in 53. çalışmada aldığı
düzeltmeyle birebir aynı mantık. Var olan davranışlar değişmedi: alan
mevcut ve taze ise `True`, mevcut ama parse edilemiyorsa (`except`) hâlâ
`False`, dosya bulunamazsa hâlâ `False`.

### Test
`tests/test_orchestrator_readiness_missing_generated_utc.py` (yeni, 3
test): `Orchestrator.__new__(Orchestrator)` ile hafif bir instance
oluşturup `_readiness_clears_live()`'ı gerçek `data/readiness_verdict.json`
dosyası üzerinden (yedekleyip/geri yükleyerek) çağırıyor.
- `generated_utc` alanı yok → `False` olmalı
- `generated_utc = ""` → `False` olmalı
- `generated_utc` taze ISO zaman damgası → `True` olmalı (sanity check)

Düzeltme öncesi: **2 failed, 1 passed** (ilk iki senaryo `True` döndürdü,
beklenen `False` yerine). Düzeltme sonrası: **3 passed**.

### Doğrulama
Düzeltme öncesi (yeni test dosyası varken): `python3 -m pytest tests/` →
**2 failed, 788 passed, 2 skipped**. Düzeltme sonrası: **790 passed, 2
skipped** (788 taban dahil, iki başarısız test artık geçiyor, sıfır
regresyon).
Ek olarak `calibration/tests` (524 passed), `execution_realism/tests`
(121 passed), `signal_bridge/tests` (77 passed) — hepsi yeşil, bu turun
değişikliğinden etkilenmiyorlar ama sıfır regresyon teyit edildi.

## Sonuç
Canlı emir yolunun readiness-tazelik kontrolü artık her iki kod
kopyasında da (`control_plane/live_gate.py` ve `agents/orchestrator.py`)
"yaşı bilinmiyor" durumunu "güvenilmez" olarak ele alıyor. 53. çalışmanın
kapattığını düşündüğü INC-2026-03-15-001 sınıfı hata, aslında sadece
kontrol yolunun yarısında kapatılmıştı — bu tur diğer yarısını kapattı.
CLAUDE.md'nin risk kuralları veya Kelly/Bayesian iç matematiği
değiştirilmedi; sadece bu tek fail-open dalı düzeltildi.

Bugünkü ikinci, bağımsız günlük inceleme (64.) PR #110'da farklı bir
modülde (`core/candlestick_analyzer.py`) açık; bu PR ona dokunmuyor.

## Sıradaki tur için notlar (devralınan + yeni)
- `data/trade_memory.json`'daki `CAPITAL_LOW` uyarısı ve sim-live WR farkı
  hâlâ araştırılmayı bekliyor (devralınan, 58. çalışmadan).
- `MC_GATE_SHADOW` loglarını izlemeye devam et; `MC_GATE_ENFORCE=true`'ya
  geçiş kararı hâlâ bekliyor (devralınan).
- **Yeni**: `agents/orchestrator.py` ve `control_plane/live_gate.py`
  arasında en az bir daha örtüşen kontrol mantığı olup olmadığı sistematik
  olarak taranmadı (bu turda sadece readiness-yaş kontrolü karşılaştırıldı)
  — canlı emir yolunda iki bağımsız kod kopyasının senkron kalması yapısal
  bir risk, ileride tekrar oluşabilir.
- `operator_layer/aggregator.py`/`ledgers.py`/`readiness_view.py` bu turda
  satır satır okundu, ek bulgu çıkmadı (sadece dashboard görselleştirmesi,
  canlı emir yoluna bağlı değil).
