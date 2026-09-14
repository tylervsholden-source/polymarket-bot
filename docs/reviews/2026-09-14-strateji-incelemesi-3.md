# Günlük Strateji İncelemesi — 2026-09-14 (27. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum özeti
- Bu oturum açıldığında `origin/main` (`d42a69a`, 24. çalışmanın sonucu)
  üzerinde **iki** bekleyen PR vardı — aynı anda çalışan başka iki paralel
  inceleme oturumundan:
  - **PR #46** (25. çalışma): `TradeAnalyzer.analyze_trade()`'in NEUTRAL
    (dolmamış/iade edilmiş) kapanışları da `pnl<=0` üzerinden LOSS sayması —
    23. çalışmada `autonomous_engine.py`'de düzeltilen aynı hata sınıfının
    `trade_analyzer.py`'de gözden kaçmış kopyası.
  - **PR #47** (26. çalışma): `Orchestrator._cycle()`'ın doğrudan emir
    döngüsünde `directional_count`'un döngü başında bir kez okunup asla
    artırılmaması — `MAX_DIRECTIONAL=2` sınırının bir cycle içinde
    sessizce aşılabilmesine yol açıyordu.
- Her iki PR da bağımsız olarak doğrulandı: izole worktree'lerde kod satır
  satır okunup iddialar canlı koda (`core/position_manager.py`'nin
  `result` alanı, `add_position()`'ın `strategy="directional"` varsayılanı)
  karşı çapraz kontrol edildi, `pytest tests/` her ikisinde de yeşildi.
  **PR #46'nın hiç regresyon testi yoktu** — bu inceleme sırasında
  `tests/test_trade_analyzer_neutral_not_loss.py` (4 test) yazılıp PR'ın
  branch'ine eklendi (fix öncesi 3/4 fail, fix sonrası 4/4 pass ile
  doğrulandı), sonra **PR #46 ve PR #47 squash-merge edildi**
  (`c802bed`, `cd2604e`). Yerel branch `origin/main`'e (`cd2604e`)
  sıfırlandı; test çalıştırmalarının yan etkisi olan
  `data/autonomous_state.json` her adımda eski haline döndürüldü.
- Bu iki PR `agents/trade_analyzer.py` ve `agents/orchestrator.py`'yi zaten
  kapsadığından, bu çalışma bilinçli olarak farklı bir alana yöneldi:
  `agents/subagents/coordinator.py`, `agents/subagents/signal_agent_v2.py`,
  `strategies/kelly_criterion.py`, `core/polymarket_client.py`,
  `core/position_manager.py`'nin kalan kısımları, `main.py`,
  `backtesting/engine.py` tek tek okundu — hiçbirinde yeni bir "hesaplanıp
  kullanılmayan değer" tipi hata bulunamadı (detaylar aşağıda, "İncelenip
  hata bulunamayan alanlar"). Asıl bulgu `agents/subagents/reviewer_agent.py`
  — Claude API tabanlı trade reviewer'ın JSON cevap ayrıştırması — içinde.

## ⚠️ ÖNEMLİ — CANLI PARAYI DOĞRUDAN ETKİLEYEBİLECEK BULGU
Aşağıdaki hata, mimarideki tek "trade onayla/veto et" güvenlik katmanının
(ReviewerAgent) yanlış trade'e yanlış karar uygulayabilmesine — yani
**onaylanmaması gereken bir trade'in gerçek sermaye ile açılmasına** —
yol açabiliyordu. CLAUDE.md'nin risk yönetimi ruhunu (min edge 0.05,
pozisyon boyutlandırma vb. hepsi ReviewerAgent'ın onayından SONRA devreye
giriyor) doğrudan zedeleyen bir sınıf hatası olduğu için en üste
işaretlendi.

## Bugün yapılan işlem: ReviewerAgent, Claude'un JSON cevabını trade kimliği yerine ham array pozisyonuna göre eşliyordu

### Hata
`agents/subagents/reviewer_agent.py::_parse_claude_response()`:
```python
for i, (item, sig) in enumerate(zip(parsed, signals)):
    ...
    decisions.append(ReviewDecision(condition_id=sig.condition_id, ...))
```
`REVIEWER_SYSTEM_PROMPT`, kullanıcı prompt'unda trade'leri
`"--- Trade #1 ---"`, `"--- Trade #2 ---"` diye numaralandırıyor ama
Claude'dan JSON cevabında bu numarayı **hiç geri istemiyordu** — sadece
"array döndür" diyordu. Kod da cevabın sırasının ve uzunluğunun
`signals` listesiyle bire bir aynı olacağını varsayıp `zip()` ile
pozisyonel eşliyordu.

Claude'un cevabı bir trade'i atlarsa, sırasını değiştirirse, ya da array
uzunluğu `signals`'tan farklı olursa (hepsi gözlemlenen LLM davranışları)
— `zip()` bir trade için yazılmış kararı (reasoning'i dahil) **sessizce
başka bir trade'in `condition_id`'sine** uyguluyordu.

**Somut, doğrulanmış senaryo** (aşağıdaki "Doğrulama" bölümündeki script
ile bire bir üretildi): Claude 3 trade'den 2.'sini (zayıf-edge'li bir NO
trade — kurala göre neredeyse kesin VETO olması gereken) atlayıp sadece
1. ve 3. trade'i inceleyip ikisini de APPROVE ederse:
- `COND_1` (trade 1) → doğru şekilde APPROVE.
- `COND_2` (hiç incelenmeyen, zayıf-edge NO trade) → `zip()` onu 2.
  array elemanıyla (aslında trade 3'ün APPROVE'u) eşliyor → **hiç
  incelenmemiş riskli trade, başka bir trade'in onay gerekçesiyle
  sessizce APPROVE ediliyor ve gerçek sermaye ile açılabiliyor.**
- `COND_3` (Claude'un gerçekten APPROVE ettiği iyi trade) → array'de
  kendine karşılık gelen slot tükendiği için "cevapta yok" listesine
  düşüyor ve gereksiz yere VETO ediliyor.

Bu, `agents/subagents/coordinator.py`'nin PHASE 3'te doğrudan kullandığı
`review_result.get_approved_signals(signal_result.signals)` çıktısını
besliyor — yani hata, hiçbir ara katman tarafından yakalanmadan doğrudan
`approved_signals`'a, oradan da emir verme yoluna ulaşıyor.

Dosyanın git geçmişi (`9b5fd52` orijinal feat commit, `762f1a9` 13. çalışma
— sadece rule-based fallback'teki ayrı bir hatayı düzeltti) bu spesifik
`_parse_claude_response()` eşleme mantığının hiç gözden geçirilmediğini
doğruluyor; kasıtlı bir tasarım kararı değil, gözden kaçmış bir varsayım.

### Düzeltme
- `REVIEWER_SYSTEM_PROMPT`: JSON şemasına zorunlu `"trade_number"` alanı
  eklendi (prompttaki `"--- Trade #N ---"` numarasıyla birebir), Claude'a
  trade'leri atlamaması/yeniden numaralandırmaması açıkça söylendi.
- `_parse_claude_response()`: `zip(parsed, signals)` yerine, her cevap
  öğesi önce kendi `trade_number`'ına göre (`signals[trade_number-1]`,
  geçerli aralıkta ve henüz kullanılmamışsa) eşleniyor; `trade_number`
  eksik/geçersizse (ör. eski tarz/bozuk bir cevap) yalnızca o öğe için
  pozisyonel sıraya geri düşülüyor — böylece iyi biçimli, sıralı, 1:1
  cevaplarda davranış aynı kalıyor, sadece atlama/yeniden sıralama
  durumunda artık yanlış trade'e karar sızmıyor. Eşlenmeyen sinyaller
  zaten var olan "cevapta yok → VETO" güvenli varsayımına düşmeye devam
  ediyor.
- Rule-based fallback (`_rule_based_review`) ve coordinator'daki REDUCE
  boyut uygulaması dahil başka hiçbir davranış değiştirilmedi.
- `tests/test_reviewer_response_matched_by_trade_number.py` eklendi (4
  test): (1) ortadaki trade'in atlanması durumunda yanlış sinyale karar
  sızmadığını — yukarıdaki somut senaryonun birebir regresyon testi —
  (2) cevabın sırası karışsa bile (3,1,2) her kararın doğru
  `condition_id`'ye ulaştığını, (3) normal sıralı/1:1 cevabın davranışının
  değişmediğini, (4) `trade_number` tamamen eksikse pozisyonel fallback'in
  eskisi gibi çalıştığını doğruluyor.

## Doğrulama
- Bağımsız reprodüksiyon scripti (fix öncesi `main`, `git stash` ile
  geçici geri alınmış dosya üzerinde çalıştırıldı): yukarıdaki 3-trade
  senaryosu → `COND_2` (hiç incelenmemiş, zayıf NO) **APPROVE** dönüyor
  (`reasoning="trade3 strong YES edge..."` — başka trade'in gerekçesi),
  `COND_3` (gerçekten APPROVE edilen) **VETO** dönüyor — hata bağımsız
  olarak yeniden üretildi.
- `pytest tests/test_reviewer_response_matched_by_trade_number.py -v`:
  fix öncesi dosya (`git stash`) → **2/4 fail** (dropped-trade ve
  reordered-response testleri, tam yukarıdaki gibi yanlış eşleme
  gösteriyor); fix sonrası (`git stash pop`) → **4/4 pass**.
- Tam suite (fix sonrası): `pytest tests/ -q` → **645 passed, 2 skipped**
  (641'den 645'e: PR #46'ya eklenen 4 test + bu çalışmanın 4 testi zaten
  641'e dahildi — bkz. not*). Sıfır regresyon, sıfır yeni hata.
  *Not: 641 rakamı PR #46+#47 merge sonrası, bu çalışmanın kendi 4 testi
  eklenmeden önceki taban; 645 bu çalışmanın testleriyle birlikte.
- `git diff agents/subagents/reviewer_agent.py` → tek fonksiyon +
  sistem prompt'unda minimal, amaca yönelik değişiklik; `_rule_based_review`,
  `ReviewBatchResult`, coordinator entegrasyonu dokunulmadı.
- Test çalıştırmalarının yan etkisi olan `data/autonomous_state.json`
  commit öncesi eski haline döndürüldü.

## İncelenip hata bulunamayan alanlar (bu turda)
- `agents/subagents/coordinator.py` — PHASE 1/2/3 akışı, REDUCE boyut
  uygulaması (`sig.size *= suggested_size_pct`, önceki tur #28'in
  düzelttiği çift-uygulama hatası tekrarlanmıyor), OrderFlow merge —
  hepsi doğru bağlı.
- `agents/subagents/signal_agent_v2.py` — confluence/risk-flag hesabı,
  `research=` kwarg'ının coordinator tarafından bilerek geçilmemesi
  (paralel çalıştığı için research henüz hazır değil — `coordinator.
  _re_enrich_signals()` zaten sonradan taze veriyle zenginleştiriyor,
  tasarım gereği, hata değil).
- `strategies/kelly_criterion.py::update_streak()` — zaten `result`
  alanına göre çalışıyor, NEUTRAL'i doğru atlıyor (23. çalışmanın
  bulgusuyla tutarlı, ayrı bir hata yok).
- `core/polymarket_client.py` — `_order_dedup` dict'inin yazılıp hiç
  okunmaması fark edildi ama kod içi yorum ("ORDER_DEDUP_BLOCK kaldırıldı
  — sinyal neyse o") bunun kasıtlı olarak devre dışı bırakıldığını
  belgeliyor — bug olarak raporlanmadı.
- `main.py`, `backtesting/engine.py` — küçük/basit, canlı karar yoluna
  bağlı yeni bir kopukluk bulunamadı (backtest zaten mimariye göre
  "güvenilmez, devre dışı" kategorisinde).

## Sonuç
27. çalışma önce iki paralel oturumdan gelen bekleyen PR'ları (#46, #47)
bağımsız doğrulayıp merge etti — birine (#46) eksik olan regresyon testini
ekleyerek. Ardından daha önce hiç incelenmemiş bir alanda —
`ReviewerAgent`'ın Claude JSON cevabını trade'lerle eşleme mantığı —
gerçek ve ciddi bir hata buldu: pozisyonel `zip()` eşleşmesi, Claude'un
cevabı beklenen sırayı/uzunluğu tutturamadığında bir trade'in kararını
sessizce başka bir trade'e uygulayabiliyordu — hiç incelenmemiş riskli
bir trade'in yanlışlıkla APPROVE edilip gerçek sermaye ile açılmasına yol
açabilecek bir senaryo dahil. Minimal bir düzeltmeyle (Claude'dan açık
`trade_number` istenip ona göre eşleme yapılarak, eksik durumda eski
pozisyonel davranışa geri dönülerek) kapatıldı ve dört regresyon testiyle
kilitlendi.
