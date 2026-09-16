# Günlük Strateji İncelemesi — 2026-09-16 (54. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum açıldığında `main` üzerinde birleştirilmemiş **1 PR** vardı: #87
(53. çalışma, `readiness gate skipped staleness check when generated_utc
was missing`). Diff okundu, branch checkout edilip `pytest tests/` yerelde
doğrulandı (**750 passed, 2 skipped**, iddia edilenle birebir), sonra GitHub
üzerinden `main`'e merge edildi. Çalışma dalı bu güncel `main`'den yeniden
oluşturuldu.

53. çalışmanın notunda `execution_realism/*`'in hiçbir zaman (54 tur
boyunca sıfır) tek tek incelenmediği fark edildi — `docs/reviews/*.md`
üzerinde `execution_realism/` için grep sıfır sonuç veriyordu, ama modül
`agents/orchestrator.py`'nin canlı döngüsüne
(`from execution_realism.core import compute_executable_ev`) doğrudan
bağlı. Bu turun kapsamı olarak seçildi: `execution_realism/core.py`,
`fill_simulator.py`, `liquidity_model.py`, `slippage_model.py`,
`staleness_penalty.py`, `types.py` ve canlı çağrı noktası
(`agents/orchestrator.py::_record_shadow_decisions`).

## Bulgu (54.) — Shadow EV hesaplaması, NO yönlü sinyaller için YES tarafının fiyat/olasılığını kullanıyordu

### Kapsam
`agents/orchestrator.py::_record_shadow_decisions()` (~satır 2448-2467),
`compute_executable_ev()`'e (`execution_realism/core.py`) giden çağrı.

### Kök neden
Fonksiyon, aynı kayıt için birkaç satır önce (satır ~2387-2399) NO tarafının
doğru fiyatını (`_ask_no` — gerçek orderbook varsa `REAL_BOOK`, yoksa
`1 - bid_yes` sentetik) zaten hesaplıyor ve bunu `pricing_snap`/`signal_snap`
alanlarına doğru şekilde yazıyor. Ama hemen ardından gelen
`compute_executable_ev()` çağrısı bu değeri hiç kullanmıyordu — sinyalin
yönüne bakmaksızın koşulsuz olarak:

```python
er = compute_executable_ev(
    side=sig_match.direction,
    calibrated_event_probability=yes_prob,   # ← her zaman YES olasılığı
    ask_price=ask_yes,                        # ← her zaman YES ask'ı
    ...
)
```

`compute_executable_ev()`'in `theoretical = calibrated_event_probability -
ask_price` formülü (execution_realism/core.py:53), NO yönlü bir sinyal için
YES piyasasının mispricing'ini hesaplıyor — değerlendirilen gerçek NO
trade'iyle hiç ilgisi olmayan bir sayı. Doğrusu NO için
`(1 - yes_prob)` vs. `_ask_no` olmalıydı; zaten hesaplanmış `_ask_no`
kullanılmadan atlanıyordu.

### Neden önemli
`execution_adjusted_ev` (bu hesabın çıktısı) shadow journal'a yazılıyor →
`shadow_runner/summary_metrics.py`'de `mean_ev_haircut_pct`'ı besliyor →
`monitoring/readiness_checks.py::check_ev_haircut_pct()` bunu
`shadow_runner/readiness.py::assess_readiness()` içinde
TINY_PILOT_CANDIDATE / NO_GO / CONDITIONAL_REVIEW kararına giren bir kontrol
olarak kullanıyor → bu karar `readiness_verdict.json`'a yazılıyor → tam da
53. çalışmada `generated_utc` tazelik kontrolü düzeltilen dosya, yani
`control_plane/live_gate.py::_check_readiness()`'in gerçek para emirlerini
kapı gibi kullandığı dosya. `docs/architecture.md`'ye göre NO, botun
baskın işlem yönü ("Sim'de NO %60-75 WR") — yani bu hata, canlı shadow
korpusunun çoğunluğu için readiness sinyalini bozuyordu.

### Somut senaryo (doğrulandı, `execution_realism.core.compute_executable_ev`
ile doğrudan çalıştırılarak)
`yes_prob=0.30`, `ask_yes=0.28`, gerçek NO ask=`0.55` — gerçekte iyi bir NO
trade (gerçek edge = 0.70 - 0.55 = **0.15**):

- **Düzeltme öncesi** (YES fiyat/olasılığı kullanılıyor):
  `theoretical_hold_ev=0.02`, `executable_ev=-0.008`,
  `passes_gate=False` → sağlıklı bir trade kaybeden gibi kayda geçiyor.
- **Düzeltme sonrası** (doğru NO fiyat/olasılığı):
  `theoretical_hold_ev=0.15`, `executable_ev=0.122`,
  `passes_gate=True` → gerçek durumu yansıtıyor.

### Düzeltme
`agents/orchestrator.py::_record_shadow_decisions()`: `compute_executable_ev()`
çağrısından hemen önce `sig_match.direction == "NO"` ise
`calibrated_event_probability=round(1.0 - yes_prob, 6)` ve
`ask_price=_ask_no` (zaten hesaplanmış, pricing_snap'te de kullanılan
değer) kullanılıyor; YES sinyalleri değişmedi (`yes_prob`/`ask_yes` aynen
kullanılmaya devam ediyor).

### Test
`tests/test_shadow_ev_no_side_price_mismatch.py` (yeni, 3 test):
1. `test_no_signal_passes_no_side_probability_and_ask` — gerçek
   `Orchestrator._record_shadow_decisions()`'ı (ağır `__init__` çalıştırmadan,
   `Orchestrator.__new__` + minimal stub'larla) çalıştırıp
   `compute_executable_ev()`'e giden `calibrated_event_probability`/`ask_price`
   kwarg'larının NO tarafına ait olduğunu doğruluyor.
2. `test_no_signal_execution_adjusted_ev_reflects_no_side_edge` — yazılan
   shadow kaydındaki `execution_adjusted_ev`'in, gerçek 0.15 edge'li bir NO
   trade için pozitif olması gerektiğini doğruluyor.
3. `test_yes_signal_still_uses_yes_side_pricing` — YES sinyallerinin
   etkilenmediğini doğrulayan sağlamlık testi.

Düzeltme öncesi (`git stash -- agents/orchestrator.py` ile doğrulandı):
**2 failed, 1 passed** (YES sanity testi zaten geçiyordu, NO testleri
başarısızdı — biri `executable_ev=-0.005 > 0` beklerken başarısız oldu).
Düzeltme sonrası: **3 passed**.

### Doğrulama
`python3 -m pytest tests/` → **753 passed, 2 skipped** (750 taban + 3 yeni
test), sıfır regresyon.

## Kapsam dışı bırakılanlar (bilinçli, blast-radius nedeniyle)
`execution_realism`'i incelerken iki ek gözlem yapıldı ama bu turda
dokunulmadı — aynı çağrı noktasındaki değişiklikleri tek bir PR'da
yığmamak ve 49. çalışmanın Monte Carlo kapısı kararında izlenen ihtiyatlı
yaklaşımı tekrarlamak için:

1. **`er.passes_gate` hiçbir yerde tüketilmiyor.** `compute_executable_ev()`
   `policy_mode="live"` ile çağrılıyor (fill/EV kapısının "canlı sıkı" modu)
   ama dönen `passes_gate` sadece shadow journal'a bile yazılmıyor —
   `DecisionSummary.passes_final_gate` alanı bunun yerine `is_execute`
   (sadece "sinyal üretildi mi") ile dolduruluyor
   (`agents/orchestrator.py:2481`, değişmedi). Gerçek emir döngüsü
   (satır 715+) bu kapıyı hiç sorgulamıyor — yalnızca ham Bayesian
   `edge >= 0.05` kullanılıyor. Bu kapıyı canlı emir döngüsüne bağlamak
   gerçek parayı etkileyen büyük bir davranış değişikliği olur ve NO-tarafı
   fiyat hatası bu turda düzeltilmeden yapılsaydı NO trade'lerin çoğunu
   (baskın yön) yanlış şekilde engelleyebilir ya da hatalı onaylayabilirdi
   — tam da 49. çalışmanın "kör bağlama tehlikeli" tespitiyle aynı sınıf.
   NO-taraf fiyat hatası artık düzeltildiği için gelecekte bu kapının
   MC_GATE_SHADOW/MC_GATE_ENFORCE ile aynı desende (önce shadow'da
   gözlemle, sonra ENFORCE'a geçir) canlıya bağlanması değerlendirilebilir.
2. **`snapshot_age_seconds=0.0` sabit geçiliyor** (satır ~2445, ~2459) —
   `compute_staleness_penalty()` bu yüzden bu çağrı noktasından hiçbir
   zaman FRESH dışında bir bölge görmüyor. Şu an zararsız (kapı zaten
   tüketilmiyor) ama gerçek piyasa verisi yaşı mevcut değilse doğru
   şekilde ele alınmalı.

## Sonuç
Shadow journal'ın `execution_adjusted_ev`/`mean_ev_haircut_pct` verisi
artık NO yönlü sinyaller için de doğru piyasanın fiyat/olasılığını
kullanıyor — bu veri üzerinden üretilen readiness kararı (canlı emir
kapısının okuduğu `readiness_verdict.json`) artık botun baskın işlem yönü
için gerçek dışı biçimde kötü (ya da yanlışlıkla iyi) görünmüyor.
CLAUDE.md'nin risk kuralları veya `execution_realism/` paketinin iç
matematiği değiştirilmedi — sadece hangi tarafın verisinin geçildiği
düzeltildi.

## Sıradaki tur için notlar (devralınan + yeni)
- `data/trade_memory.json`'daki `CAPITAL_LOW` uyarısı ve sim-live WR farkı
  hâlâ araştırılmayı bekliyor (önceki turlardan devralınan).
- `MC_GATE_SHADOW` loglarını izlemeye devam et; `MC_GATE_ENFORCE=true`'ya
  geçiş kararı hâlâ bekliyor (devralınan).
- **Yeni**: `er.passes_gate`/`fill_decision` canlı emir döngüsüne hiç
  bağlanmıyor (yukarıdaki "Kapsam dışı" #1). NO-taraf fiyat hatası artık
  düzeltildiği için, bir sonraki tur MC_GATE ile aynı shadow→enforce
  deseniyle bu kapının canlıya alınıp alınmayacağını değerlendirebilir —
  ama önce birkaç günlük shadow verisiyle `passes_gate=False` oranının
  makul olduğu gözlemlenmeli (aksi halde kör bağlama tüm trafiği durdurabilir).
- **Yeni**: `_record_shadow_decisions()`'daki `snapshot_age_seconds=0.0`
  sabiti (yukarıdaki "Kapsam dışı" #2) — kapı canlıya bağlanmadan önce
  gerçek piyasa verisi yaşıyla değiştirilmeli, yoksa staleness koruması
  kapı enforce edildiğinde de hiç tetiklenmeyecek.
- `control_plane/entry_window_guard.py::parse_market_start_time()`'ın
  `reference_year=2026` hardcoded varsayılanı (53. çalışmadan devralınan,
  düşük öncelik) — 2026 içinde risksiz, yıl dönümünde izlenmeli.
