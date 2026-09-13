# Günlük Strateji İncelemesi — 2026-09-13 (14. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum özeti
- Bu çalışma başladığında **bir açık PR** vardı: #31 (13. çalışma) —
  rule-based reviewer'ın counter-regime VETO'sunun hiç tetiklenememesini
  düzeltiyordu (`f == "COUNTER_REGIME"` → `f.startswith("COUNTER_REGIME")`).
- Doğrulama: `origin/claude/brave-faraday-fqvzyv` izole bir `git worktree`'de
  checkout edilip `pytest tests/` çalıştırıldı → **604 passed, 2 skipped**,
  PR body'sinin iddiasıyla birebir eşleşti. **PR #31 squash-merge edildi**
  (main artık `762f1a9`).
- `data/status.json`/`positions.json`/`control.json`/`.env` bu ortamda yok →
  gerçek API kimlik bilgisi veya canlı pozisyon yok, bugün kapatılacak/açılacak
  gerçek bir pozisyon yoktu.

## Bugünkü derin inceleme: `strategies/arbitrage_engine.py`'nin import ettiği
## 6-model motorunun (Bayesian+Edge+Spread+Stoikov+Kelly+**MC**) hiç
## dokunulmamış MC (Monte Carlo) tarafı

`docs/reviews/` geçmişi tarandı: hiçbir önceki inceleme "Monte Carlo",
"monte_carlo" veya "mc.simulate" ifadelerini içermiyor — bu tamamen yeni bir
alan.

### Bulgu 1 (gerçek ama YANLIŞTAN DÖNÜLDÜ — bilinçli olarak wire edilmedi)

`ArbitrageEngine._maybe_run_monte_carlo()` (`strategies/arbitrage_engine.py:283`)
`MonteCarloResult.viable` (gerçekçi fill/slippage/edge-noise koşulları altında
stratejinin hayatta kalıp kalmadığı) hesaplayıp `bool` olarak return ediyor,
ama tek çağrı noktası (`analyze()`, satır 393) dönüş değerini **hiç
kullanmıyor** — sadece log için çağrılıyor. `docs/architecture.md` ve
CLAUDE.md'nin "6-model ArbitrageEngine" tanımındaki MC modeli, gerçekte hiçbir
sinyali hiç engelleyememiş.

Bunu `analyze()`'da `if not mc_viable: return []` şeklinde bağlamayı
denedim ve `tests/test_monte_carlo_viability_gate.py` (2 yeni test) ile
kilitledim — ama tam paketi çalıştırınca **`test_execution_path.py
::test_full_sim_execution_chain` kırıldı** (capital=$5 ile "güçlü bullish
sinyal üretilmeli" testi artık `[]` dönüyordu).

Kök nedeni araştırdım: `MonteCarloSimulator.simulate()` bahis büyüklüğünü
saf `capital × position_size_pct` olarak modelliyor ve `bet < 0.5` ise o
trade'i tamamen atlıyor — ama canlı kod böyle çalışmıyor: `arbitrage_engine.py`
satır 1687-1699'daki `_KELLY_MIN_BET = 3.0` "compounding floor" mantığı,
Kelly boyutu $3'ün altına düştüğünde (edge yeterliyse) $3'e **yükseltiyor**,
atlamıyor. $5 sermayede MC'nin modeli bahis=$0.43 hesaplayıp atlarken, canlı
kod aynı sinyali $3 (sermayenin %60'ı!) ile giriyor. Bu floor'u MC'ye
eklediğimde (`bet = min(max(bet, 3.0), w)`), bu sefer MC gerçekten $3/$5=%60
bahis boyutunun ruin dinamiklerini yakaladı (`E[r]=-81.6%%, DD=97.8%%`) ve
yine viable=False üretti — ama bu da yanlış: canlı yolda `agents/orchestrator.py`
satır 67-90'daki `compute_bet_size()` son adımda bahsi
`capital × max_position_pct (0.20)` ile clamp'liyor (CLAUDE.md'nin "%20"
kuralı, PR #21'de doğrulanmış), yani $5 sermayede gerçek bahis $3 değil
`min($3, $5×0.20)=$1` olurdu. MC'nin tek-percentage modeli bu iki aşamalı
floor+cap boru hattını yansıtmıyor.

Daha da önemlisi: bu düzensizlik `tests/test_execution_path.py`'nin **kasıtlı
ve halihazırda test edilen** davranışıyla doğrudan çelişiyor —
`AutonomousDecisionEngine` düşük sermayede botu tamamen durdurmak yerine
"survival" modunda (×0.30 boyut çarpanı) çalışmaya devam edecek şekilde
tasarlanmış (CLAUDE.md). MC'yi olduğu gibi sert bir "tüm sinyalleri engelle"
kapısı olarak bağlamak, motorun kendi kalibre edilmemiş dahili sizing
modelindeki bir tutarsızlık yüzünden, botun canlı kalması gereken tam da o
düşük-sermaye senaryosunda kalıcı olarak durmasına yol açardı.

**Karar: CLAUDE.md'nin "Karmaşık Görevler ... bir şeyler ters giderse dur ve
yeniden planla" talimatı gereği, bu değişikliği commit etmeden geri aldım**
(`git checkout -- strategies/arbitrage_engine.py strategies/monte_carlo.py`,
yeni test dosyası silindi). Gerekçe: MC'nin `viable` çıktısını canlıya
güvenle bağlamak için önce `MonteCarloSimulator.simulate()`'ın canlı iki
aşamalı sizing'i (KELLY_FLOOR $3 + orchestrator %20 clamp) doğru şekilde
modellemesi, ayrıca sıfır-trade'lik dejenere simülasyonların %0 win-rate
olarak sayılmaması gerekiyor — bu, "mevcut bir gate'i bağla" kapsamının
ötesinde, ayrı ve dikkatli bir tasarım/kalibrasyon çalışması. Bir sonraki
çalışma için not bırakıldı, kod değişikliği yapılmadı (KellyCriterion
.should_enter() için 9. çalışmada uygulanan aynı emsal).

### Bulgu 2 (gerçek, düşük riskli, UYGULANDI)

Aynı incelemede, MC çağrısının `os.getenv("MAX_POSITION_PCT", 0.10)`
kullandığını, ama aynı env var'ı okuyan `strategies/kelly_criterion.py:22` ve
`core/position_manager.py:96`'nın her ikisinin de `0.20` varsayılanı
kullandığını buldum — üç yerin aynı env var için iki farklı fallback'i var.
`MAX_POSITION_PCT` ortam değişkeni tanımlı değilken (varsayılan durum), MC
gerçek Kelly/PositionManager boyutlandırmasının **yarısı** kadar bahis
büyüklüğü simüle ediyordu — win-rate ve drawdown tahminlerini olduğundan
iyimser gösteriyordu. `viable` şu an hiçbir yerde tüketilmediği için bunun
canlı sermaye etkisi yok (Bulgu 1), ama loglanan Monte Carlo metrikleri
(`E[r]/WR/DD/Sharpe`) yanlış varsayılan sizing ile hesaplanıyordu — 12.
çalışmanın (#30) aggression-etiket düzeltmesiyle aynı sınıf: log/tanı
doğruluğu, gelecekte bu gate gerçekten bağlanınca da doğru davranması için
önemli.

**Düzeltme**: `strategies/arbitrage_engine.py:290`'daki fallback `0.10` →
`0.20` (kelly_criterion.py/position_manager.py ile aynı).
`tests/test_mc_position_pct_matches_kelly.py` eklendi: MC çağrısına giden
`position_size_pct`'in, `MAX_POSITION_PCT` tanımsızken, hem
`KellyCriterion().max_position_pct` hem `PositionManager().max_position_pct`
ile (0.20) eşleştiğini doğruluyor.

## Doğrulama
- `python3 -c "import strategies.arbitrage_engine; import agents.orchestrator"` → hatasız.
- `pytest tests/` → **605 passed, 2 skipped** (604 → 605: 1 yeni test eklendi,
  mevcut testlerden hiçbiri bozulmadı; MC hard-block denemesi geri alındığı
  için o testler pakette değil).
- `git status` → yalnızca amaçlanan iki değişiklik (`arbitrage_engine.py`
  tek satır + yeni test dosyası); `data/autonomous_state.json` gibi test
  çalıştırma yan etkileri commit'lenmeden geri alındı.

## Sonuç
14. çalışma önce açık PR'ı (#31, counter-regime VETO düzeltmesi) doğrulayıp
merge etti, sonra CLAUDE.md'nin "6-model ArbitrageEngine" tanımının hiç
incelenmemiş MC (Monte Carlo) tarafını derinlemesine araştırdı. İki bulgu
çıktı: (1) MC'nin `viable` gate'i gerçekten hiç bağlı değil — ama bunu
düzeltme girişimi, simülatörün canlı iki-aşamalı (floor+cap) sizing'i doğru
modellemediğini ve bunun botun kasıtlı düşük-sermaye "survival mode"
davranışıyla çeliştiğini ortaya çıkardı; bu yüzden **bilinçli olarak geri
alındı ve bağlanmadı**, ileride ayrı bir kalibrasyon çalışması gerektiği not
edildi. (2) Aynı MC çağrısındaki `MAX_POSITION_PCT` varsayılan tutarsızlığı
(0.10 vs 0.20) — düşük riskli, gerçek ve doğrulanmış bir düzeltme — uygulandı
ve regresyon testiyle kilitlendi.
