# Günlük Strateji İncelemesi — 2026-09-13 (7. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum özeti
- Bu çalışma başladığında `main`'de açık bir PR vardı: **#24** ("wire OPT-2
  coin-per-period cap, was silently loosened to 5") — 6. çalışmanın bulup
  açtığı, henüz merge edilmemiş bir düzeltme.
- `pytest tests/` her iki ortamda da (`main` ve PR #24 branch'i, izole
  worktree'de) çalıştırıldı ve doğrulandı.

## Bugün yapılan işlem 1: PR #24 doğrulandı ve merge edildi
6. çalışmanın bulduğu gerçek bir hata: canlı `_cycle()` çağrı noktası
`_limit_coins_per_period(all_signals, max_per_period=5)` kullanıyordu,
CLAUDE.md OPT-2'nin belgelediği `1` yerine — aynı `9b5fd52` squash-commit'in
neden olduğu, daha önceki 4 çalışmanın bulduğu diğer sessiz gevşetmelerle
(stop-loss, MAX_OPEN_POSITIONS, OPT-3, %20 tavan) aynı kökten.

Doğrulama adımları:
- `git diff origin/main origin/claude/brave-faraday-7mmypi --stat` → sadece
  `agents/orchestrator.py` (2 satır: çağrı noktası + fonksiyon varsayılanı),
  yeni test dosyası, ve inceleme dokümanı.
- İzole `git worktree` içinde PR branch'i checkout edilip `pytest tests/`
  çalıştırıldı → **589 passed, 2 skipped** (iddia edilenle birebir eşleşti).
- Diff, CLAUDE.md'nin OPT-2 tanımıyla ("Max 1 Coin/Period") tam uyumlu.

Sonuç: **PR #24 squash-merge edildi** (`49b79694`). `main` artık aynı zaman
diliminde tek coin'e izin veriyor; 5x korelasyonlu risk açığı kapatıldı.

## Bugün yapılan işlem 2: "Sim-Live gap" araştırıldı (CLAUDE.md'de 9 gündür açık kalan bulgu)
CLAUDE.md'nin "Kritik Keşifler" bölümünde belgelenen ve bugüne kadarki 9
inceleme dokümanının hiçbirinde araştırılmamış olan madde ele alındı:

> "Sim-Live gap — Sim'de NO %60-75 WR, canlıda %0 WR. Execution farkı
> araştırılmalı."

Derin kod incelemesi (agents/orchestrator.py, core/polymarket_client.py,
core/position_manager.py, backtesting/engine.py, git geçmişi) sonucu:

- **Yeni, düzeltilmemiş bir canlı-execution hatası bulunamadı.** Geçmişte
  bulunan iki düzeltme (`8313e69`: FRESH_PRICE_ABORT'un NO tarafını hiç
  korumaması — Gamma'nın `no_best_ask` alanını hiç doldurmaması yüzünden
  guard NO emirlerinde sessizce no-op oluyordu; `7159b98`: YES/NO token-index
  varsayımı için log cross-check) tam olarak bu tarz bir sim-canlı
  farkını açıklayacak nitelikte ve ikisi de hâlâ kod tabanında aktif/sağlam
  (`_fresh_price_ok`, `orchestrator.py:960` üzerinden doğrulandı).
- `core/position_manager.py:507-527`'deki NO PnL/exit formülü her iki yön
  için de tutarlı; YES/NO karışıklığına dair bir formül hatası yok.
- `data/last_5_losses.json`'daki gerçek görünümlü canlı trade örnekleri
  (hex order_id, büyük CLOB token_id) çok küçük bir örneklem (5 trade, 4'ü
  NO, hepsi kayıp) — "canlıda %0 WR" iddiasının muhtemelen küçük-örneklem
  artefaktı olduğunu, sistemik/aktif bir hatadan çok yukarıdaki iki
  düzeltmeden **önceki** dönemden kalma olduğunu düşündürüyor.
- Backtest'in endpoint-bias'ı (`backtesting/engine.py:76-90`, kapanmış
  marketlerde `bestAsk`'i giriş fiyatı olarak kullanması) zaten belgeli ve
  ayrı bir katkı faktörü — yeni değil.

**Karar:** Bu ortamda düzeltme sonrası gerçek canlı trade log'u olmadığından
iddia kesin kapatılamıyor, ama mevcut kanıtlarla kod değişikliği
gerektirecek yeni bir hata yok. CLAUDE.md'deki not aynen bırakıldı; gelecekte
gerçek canlı log erişimi olduğunda (özellikle `8313e69` sonrası NO
trade'lerinin WR'si) tekrar bakılmalı.

## Doğrulama
- `pytest tests/` (main, merge sonrası) → **589 passed, 2 skipped**.
- `git log origin/main` → PR #24 lineer geçmişte, temiz.

## Sonuç
Bugünün 7. çalışması bir PR'ı doğrulayıp merge etti (OPT-2, gerçek 5x risk
açığı) ve 9 gündür açık kalan Sim-Live gap sorusunu araştırıp, mevcut kod
tabanında bunu açıklayacak yeni bir aktif hata olmadığı sonucuna vardı —
en olası açıklama zaten düzeltilmiş iki geçmiş hata ve küçük örneklem.
