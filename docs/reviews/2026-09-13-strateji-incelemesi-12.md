# Günlük Strateji İncelemesi — 2026-09-13 (12. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum özeti
- Bu çalışma başladığında **bir açık PR** vardı: #29 (11. çalışma) —
  `AutonomousDecisionEngine.get_adaptive_params()`'ın `min_edge_yes/no` ve
  `max_bet_multiplier` alanlarını gerçek canlı yola (`arb_engine`, `bet_size`)
  bağlayan gerçek bir düzeltme.
- Doğrulama: izole `git worktree`'de PR branch'i checkout edilip bağımlılıklar
  kuruldu, `python3 -c "import agents.orchestrator"` hatasız, `pytest tests/`
  → **599 passed, 2 skipped** — PR body'sinin iddiasıyla birebir eşleşti. Diff
  minimal (orchestrator.py'de üç küçük ekleme + regresyon testi) ve
  CLAUDE.md'nin edge tabanlarını (0.12/0.18) asla gevşetmeme kuralına uyuyor.
  **PR #29 squash-merge edildi** (main artık `79df1f9`).
- Bu checkout'ta yine `data/control.json`/`data/positions.json`/`.env` yoktu
  → bugün kapatılacak/açılacak gerçek bir pozisyon yoktu.

## Bugünkü derin inceleme: önceki 11 raporun bulmadığı yeni bir P&L-etkili hata arandı, bulunamadı

Sırasıyla incelenen, daha önce hiç veya yüzeysel bakılmış dosyalar:

1. **`agents/trade_analyzer.py` (`get_recommendations`)** — Pattern'lara
   dayalı öneriler (`NO_TRAP`, `HIGH_EDGE_WIN`, `REGIME_OVEREXTEND_LOSS`,
   `WHALE_ALIGNED_WIN`) sadece log'a yazılıyor, otomatik uygulanmıyor — ama
   bunlar CLAUDE.md'nin kendisinin tanımladığı gibi doğal dilde, insan/Claude
   incelemesi için tasarlanmış öneriler (yapılandırılmış sayısal alanlar
   değil), otomatik uygulanacak şekilde tasarlanmamışlar. `LOW_EDGE_LOSS`
   pattern'i (`data/trade_patterns.json`: 1008 kayıp, $-2548 toplam) için
   öneri yok, ama bu dosya tek bir geçmiş commit'ten (`9b5fd52`,
   2026-04-23, "full bot update") kalma statik örnek veri — canlı sistemde
   güncellenmiyor (git log tek kayıt) ve edge<0.08 eşiği zaten statik
   `min_edge_yes/no` (0.12/0.18) tabanının çok altında kaldığı için bugünkü
   canlı gate'le örtüşmüyor. Aksiyon gerektirmiyor.
2. **`agents/subagents/coordinator.py`** — Tam pipeline (research+signal
   paralel → merge → reviewer sıralı → REDUCE boyut küçültme) satır satır
   okundu; 10. çalışmanın düzelttiği REDUCE double-apply hatası hâlâ
   düzeltilmiş durumda (coordinator'da tek uygulama), başka bir double-apply
   veya atlanan alan bulunamadı.
3. **`agents/autonomous_engine.py` (`evaluate`, `_assess_risk`,
   `_update_performance`)** — Ardışık kayıp/kazanç hesaplama döngüsü
   (satır 368-381) elle birkaç senaryo ile (W-W-L, L-L-W, L-W-L-L) doğrulandı,
   doğru. `_assess_risk`'in risk skoru bileşenleri (edge, yön, risk flag
   sayısı, confluence, pozisyon yoğunluğu, sermaye) CLAUDE.md'nin "Otonom
   Karar Akışı" bölümüyle birebir eşleşiyor. `LOW_RISK_EDGE`/`MED_RISK_EDGE`/
   `HIGH_RISK_FLAGS` sabitleri tanımlı ama `_assess_risk` bunları referans
   almıyor (hardcoded 0.05/0.08/0.12 literal'leri kullanıyor, ki bunlar
   sabitlerle sayısal olarak zaten örtüşüyor) — kozmetik, davranışı
   etkilemiyor, bugün dokunulmadı (CLAUDE.md "Sadelik": gerekmeyen değişiklik
   yapma).
4. **`strategies/kelly_criterion.py`** — Formül (`f*=(bp-q)/b`),
   adaptive/dynamic/regime-aware/capital-preservation katmanları
   strategy.md'deki tanımla ve `data/bot_log.txt`'teki canlı
   `DYNAMIC_KELLY: W2/L0 → multiplier=1.10` log satırıyla doğrulandı — hem
   doğru hesaplanıyor hem de `update_streak()` çağrısı canlı yolda
   (`orchestrator.py:516`) gerçekten çalışıyor.
5. **`core/position_manager.py` (`daily_loss_exceeded`)** — Günün başındaki
   sermaye = `capital - daily.pnl` formülü doğru, üç çağrı noktası
   (`orchestrator.py:856,1048,1054`) canlı trade kapısını gerçekten
   engelliyor.

## Bugün bulunan ve düzeltilen küçük hata: kayıp serisi, DEFENSIVE/SURVIVAL "aggression" etiketini yanlış şekilde "AGGRESSIVE"ye çeviriyordu

### Kod incelemesi
- `agents/autonomous_engine.py:438-441` (düzeltme öncesi) — `consecutive_losses
  >= 3` bloğu, önceki bloklarda `win_rate < 0.40` (DEFENSIVE) veya
  `capital < $10` (SURVIVAL) tarafından zaten set edilmiş `params["aggression"]`
  etiketini **koşulsuz** `"AGGRESSIVE"` ile eziyordu — `min_edge_yes/no` ve
  `max_bet_multiplier` değerleri (gerçek risk sıkılaştırması) değişmeden
  kalıyordu, sadece etiket yanlış oluyordu.
- Grep ile doğrulandı: `aggression` alanı repo genelinde **sadece**
  `orchestrator.py`'de iki `logger.info()` çağrısında okunuyor
  (`ADAPTIVE_INTERVAL`, `ADAPTIVE_RISK`) — hiçbir yerde davranışa dallanmıyor.
  Yani bu hata sermayeyi/emirleri etkilemiyor, **sadece günlükleri
  yanıltıyor**.

### Etki
Düşük win-rate + 3+ ardışık kayıp aynı anda gerçekleştiğinde (tam olarak
CLAUDE.md'nin "Kritik Keşifler" bölümünün betimlediği bounce/losing-streak
senaryosu), loglar `aggression=AGGRESSIVE` gösteriyordu, gerçekte bot
DEFENSIVE/SURVIVAL modda (daha sıkı edge eşiği, küçültülmüş bet boyutu)
çalışıyor olmasına rağmen. Bu tam olarak bu günlük inceleme sürecinin
loglara bakarak bot durumunu teşhis ettiği senaryo — yanlış etiket, ileride
bir incelemeyi "bot performans kötüleşince gevşiyor" diye yanlış bir
sonuca götürebilirdi.

### Düzeltme
- `get_adaptive_params()`: `consecutive_losses >= 3` bloğu artık
  `params["aggression"]`'ı sadece hâlâ `"NORMAL"` ise `"AGGRESSIVE"`ye
  çeviriyor — DEFENSIVE/SURVIVAL etiketini asla ezmiyor.
  `cycle_interval_seconds=60` ataması (zaten varsayılan değer, davranışı
  değiştirmiyor) korundu.
- `tests/test_adaptive_params_wiring.py`'ye iki test eklendi: (1) win_rate
  düşükken + 3+ kayıp serisiyle etiketin `"DEFENSIVE"` kalması, (2) başka
  hiçbir faktör yokken 3+ kayıp serisinin etiketi hâlâ `"AGGRESSIVE"`ye
  çevirmesi (regresyon: davranış tamamen kaldırılmadı, sadece koşullu
  hale getirildi).

## Doğrulama
- `python3 -c "import agents.orchestrator"` → hatasız.
- `pytest tests/` → **601 passed, 2 skipped** (599'dan 601'e: 2 yeni test
  eklendi, mevcut testlerden hiçbiri bozulmadı).

## Sonuç
12. çalışma önce açık PR'ı (#29, gerçek adaptive min_edge/bet-size wiring
düzeltmesi) doğrulayıp merge etti, sonra beş ayrı dosyada (`trade_analyzer`,
`coordinator`, `autonomous_engine`, `kelly_criterion`, `position_manager`)
derinlemesine, önceki 11 raporun bulmadığı yeni bir sermaye-etkili hata aradı
ve **bulamadı** — mevcut kod tabanı sağlam. Bunun yerine, aynı gün içinde
`get_adaptive_params()`'da küçük ama gerçek bir günlük-doğruluğu hatası
buldu ve düzeltti: kayıp serisi, DEFENSIVE/SURVIVAL risk etiketini
"AGGRESSIVE" ile eziyordu (parayı etkilemiyor, ama gelecekteki günlük
incelemeleri yanıltabilirdi — tam da bu sürecin kendisinin güvendiği
sinyal). Değişiklik minimal (tek koşul eklendi) ve regresyon testleriyle
kilitlendi.
