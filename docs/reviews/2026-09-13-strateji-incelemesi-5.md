# Günlük Strateji İncelemesi — 2026-09-13 (5. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum özeti
- Açık PR yok, `origin/main` ile local branch birebir aynı (`ae0d9c3`).
- `pytest tests/` → **586 passed, 2 skipped** — 4. çalışmanın bıraktığı
  durumla birebir eşleşiyor.
- `data/control.json` / `data/positions.json` bu checkout'ta yok (container
  her oturumda temiz açılıyor) — gerçek canlı sermaye/pozisyon durumu bu
  ortamdan gözlenemiyor, bu yüzden inceleme kod-seviyesinde kaldı.

## Bugün yapılan doğrulama turu (yeni bulgu yok)
Bugünkü 4 çalışmanın (stop-loss, MIN_MARKET_VOLUME, OPT-5/MAX_OPEN_POSITIONS,
survival-floor cap, OPT-3) hepsinin uçtan uca hâlâ doğru bağlı olduğunu
kod okuyarak tek tek doğruladım:

| Kontrol | Sonuç |
|---|---|
| Günlük -%15 stop-loss | `agents/orchestrator.py:1004/1010/1037` → `check_live_gate(daily_loss_exceeded=...)` → `control_plane/live_gate.py:94` `passed=not daily_loss_exceeded`. Gerçekten emir engelliyor, sadece log değil. `tests/test_daily_stop_loss_wiring.py` bunu kilitliyor. |
| Max 5 açık pozisyon | `self.max_open_positions = int(os.getenv("MAX_OPEN_POSITIONS", 5))` ve gate'e geçiriliyor. |
| Max tek pozisyon %20 | `agents/orchestrator.py:90` `position_cap = capital * max_position_pct`; survival-floor artık bu tavanı aşamıyor (dünkü PR #21). |
| Min market hacmi $5.000 | Önceki oturumda (#13) canlı market taramasına bağlanmıştı, değişmemiş. |
| OPT-1..OPT-6 (v9) | OPT-3 bugün onarıldı ve hâlâ yerinde; OPT-1 (regime-addon'a evrilmiş) ve OPT-4 (gevşetilmiş eşik) kasıtlı tasarım olarak işaretli, dokunulmadı; OPT-2/OPT-5/OPT-6 önceki günlerde doğrulanmış, bugün değişiklik yok. |
| PnL çift-sayım | `core/position_manager.py:679` → `self.data["capital"] += pnl` (sadece pnl, amount+pnl değil) — architecture.md'nin belgelediği düzeltme hâlâ geçerli. |
| Market filtresi (sadece btc/eth/sol/xrp/doge/bnb/hype up-or-down) | `agents/orchestrator.py:1245-1253` listede tümü mevcut (hype/hyperliquid dahil); `mean_reversion.py` stratejisi `agents/` altında hiçbir yerden import edilmiyor → hâlâ devre dışı, dokümante edildiği gibi. |

## Sonuç
Bugünün 1-4. çalışmalarında bulunan dokümante-ama-uygulanmamış kural
ihlallerinin hepsi kalıcı ve doğru şekilde düzeltilmiş durumda; bu turda
yeni bir sapma bulunamadı. Kod tabanı şu an CLAUDE.md/docs'taki "Temel
Kurallar" ve "v9 Optimizasyonları" tablosuyla tutarlı. Yeni bir PR
açılmadı — değişiklik gerektiren bir bulgu yok.

## Not
`review_bundle/`, `incident_bundle/`, `incident_bundle_v2/` altındaki eski
repo kopyaları hâlâ commit'li (önceki incelemelerin de notu); canlı koda
etkisi yok, dokunulmadı.
