# Günlük Strateji İncelemesi — 2026-09-13 (10. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum özeti
- `origin/main` HEAD `effed4c` (8. çalışmanın sonucu) ile bu branch'in
  başlangıcı birebir aynıydı, çalışma ağacı temizdi.
- Açık PR #27 (9. çalışma) var ama farklı bir branch üzerinde ve kod
  değişikliği içermiyor (sadece OPT-1/decay-pause'un tekrar doğrulanması,
  "no change" sonucu) — bu çalışmayla çakışmıyor.
- Bu checkout'ta yine `data/control.json`/`data/positions.json`/`.env` yoktu
  → bugün kapatılacak/açılacak gerçek bir pozisyon yoktu, acil bir işlem
  kararı gerekmedi.
- `pytest tests/` bağımlılıkları bu ortamda kurulu değildi
  (`No module named pytest`); `pip install -r requirements.txt` ile kuruldu,
  sonra **592 passed, 2 skipped** — 8. çalışmanın bıraktığı durumla eşleşti.
- Önceki 9 çalışmanın raporları okundu; kapatılmış maddeler (daily stop-loss,
  MIN_MARKET_VOLUME, OPT-1..OPT-6, dashboard min_bet, market filtresi,
  pozisyon muhasebesi, Kelly formülü) yeniden incelenmedi — bugüne kadar hiç
  bakılmamış bir alana odaklanıldı: `agents/autonomous_engine.py`,
  `agents/subagents/coordinator.py` ve bunların orchestrator ile canlı
  bağlantısı (CLAUDE.md'nin "Otonom Karar Akışı" bölümünde tarif edilen ama
  önceki 9 incelemenin hiçbirinde adı geçmeyen dosyalar).

## Bugün bulunan hata: REDUCE verdict'in suggested_size_pct'i live bet_size'a iki kez uygulanıyordu

### Kod incelemesi
- `agents/subagents/coordinator.py:310-317` — `AgentCoordinator.run_cycle()`,
  reviewer `REDUCE` verdict verdiğinde `sig.size = round(sig.size *
  dec.suggested_size_pct, 2)` ile sinyalin boyutunu **kalıcı olarak**
  küçültüyor, sonra bu (signal, decision) çiftini `approved_signals` içinde
  orchestrator'a döndürüyor.
- `agents/orchestrator.py:664-671` — orchestrator'daki per-signal döngü, aynı
  (zaten küçültülmüş) `signal`'i `self.autonomous_engine.evaluate(signal,
  review_decision, ...)`'a veriyor.
- `agents/autonomous_engine.py` (düzeltme öncesi, satır 221-225) —
  `evaluate()` içinde REDUCE verdict için **aynı** `suggested_size_pct`
  ikinci kez `size_mult = min(size_mult, suggested)` ile uygulanıyordu.
- `agents/orchestrator.py:722-724` — `compute_bet_size(signal_size=signal.size,
  ...)` çağrısı, coordinator'ın zaten küçülttüğü `signal.size`'ı Kelly
  girdisi olarak kullanıyor → `bet_size` bu tek-küçültülmüş değerden türüyor.
- `agents/orchestrator.py:735-737` — `bet_size = max(_effective_min, bet_size
  * _auto_size_mult)` — `_auto_size_mult` (autonomous engine'in çıktısı,
  REDUCE için `suggested`'a clamp'lenmiş) bu zaten-küçültülmüş `bet_size`'a
  **tekrar** çarpılıyor.
- `agents/orchestrator.py:848-851` — canlı emirde gerçekten gönderilen tutar
  `amount=bet_size` — yani double-application gerçek sermayeyi etkiliyordu
  (SIM/shadow yolunda ise sadece `signal.size` loglanıyor, `bet_size`
  kullanılmıyor — o yol etkilenmiyordu).

### Etki
`suggested_size_pct=0.6` diyen bir REDUCE verdict, başka hiçbir risk faktörü
daha sıkı olmadığında, gerçek emri reviewer'ın istediği ×0.6 yerine ×0.36
(0.6×0.6) boyutuna küçültüyordu — `FIX_CONFLICT_REPORT.md`'nin daha önce
çözülmüş 15dk double-dampening hatasıyla birebir aynı desen, ama farklı bir
katmanda (reviewer REDUCE ⇄ autonomous engine). Bu, onaylanmış ve
"REDUCE" ile kısmen güvenilir bulunmuş sinyalleri gereğinden fazla
küçülterek sermaye kullanımını gereksiz yere kısıyor — %10 hedefine
ulaşmayı zorlaştıran, riski artırmayan ama getiriyi gereksiz frenleyen bir
hata.

`agents/autonomous_engine.py` ve `agents/subagents/coordinator.py`'nin ikisi
de daha önce **hiç test edilmemişti** (`tests/` içinde bu iki dosyaya
referans yoktu) — bu da hatanın 9 önceki incelemede de yakalanmamış
olmasını açıklıyor.

### Düzeltme
- `agents/autonomous_engine.py`: REDUCE branch'inde `size_mult = min(size_mult,
  suggested)` satırı kaldırıldı — `suggested_size_pct` artık sadece
  coordinator tarafında, tek sefer uygulanıyor. `action = EXECUTE_REDUCED`
  ataması ve reasoning log'u korundu (sınıflandırma/log için hâlâ gerekli),
  sadece boyut çarpanına ikinci kez binmiyor. Diğer bağımsız risk
  faktörleri (drawdown, loss-streak, regime_strength, vb.) etkilenmeden
  `min()` zinciriyle uygulanmaya devam ediyor.
- `tests/test_reduce_verdict_size_not_double_applied.py` eklendi: (1) hiçbir
  başka risk faktörü yokken REDUCE verdict'in `size_multiplier`'ı 1.0'da
  bırakması (coordinator'ın tek ×0.6'sı dışında ek küçültme olmaması), (2)
  REDUCE ile birlikte bağımsız bir risk faktörünün (regime_strength>0.80 →
  ×0.5 cap) hâlâ doğru şekilde uygulanmaya devam etmesi.

### Bugün dokunulmayan, ilgili gözlem
- `autonomous_engine.py`'deki VETO dalı (`size_mult = min(size_mult, 0.25)`)
  canlı yolda **ölü kod**: `ReviewBatchResult.get_approved_signals()`
  (`agents/subagents/reviewer_agent.py:70-80`) sadece `dec.approved`
  (APPROVE/APPROVE_WITH_WARNING/REDUCE) olan sinyalleri döndürüyor — VETO
  sinyaller `approved_signals`'a hiç girmiyor, dolayısıyla
  `autonomous_engine.evaluate()`'e asla VETO verdict'i ile ulaşmıyorlar.
  Davranışı bozmuyor (zararsız ölü kod, tek çağrı noktası
  `agents/orchestrator.py:664` doğrulandı) ve REDUCE hatası kadar acil
  olmadığından bugün ayrıca dokunulmadı — ayrı bir temizlik konusu olarak
  bırakıldı.

## Doğrulama
- `pip install -r requirements.txt` (ortamda pytest kurulu değildi).
- `pytest tests/` → **594 passed, 2 skipped** (592'den 594'e: 2 yeni test
  eklendi, mevcut testlerden hiçbiri bozulmadı).
- `python -c "import agents.orchestrator"` → hatasız.

## Sonuç
10. çalışma, önceki 9 incelemenin hiç bakmadığı `autonomous_engine.py` +
`coordinator.py` REDUCE-verdict boyutlandırma yolunda, canlı sermayeyi
etkileyen gerçek bir double-application hatası buldu ve düzeltti: reviewer'ın
"REDUCE" kararı gerçek emirde iki kez uygulanıp trade'leri istenenden çok
daha fazla küçültüyordu. Düzeltme minimal (tek satır kaldırma + reasoning
güncellemesi) ve regresyon testiyle kilitlendi; ilişkili ama zararsız bir
ölü-kod gözlemi (VETO dalı) ayrı not edildi, değiştirilmedi.
