# Günlük Strateji İncelemesi — 2026-09-13 (8. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum özeti
- Açık PR yok, `origin/main` HEAD `8a52479` (7. çalışmanın sonucu) ile local
  birebir aynıydı, çalışma ağacı temizdi.
- `pytest tests/` → **589 passed, 2 skipped** — 7. çalışmanın bıraktığı
  durumla eşleşti.
- Önceki 7 çalışmanın raporları okundu; zaten kapatılmış maddeler (daily
  stop-loss, MIN_MARKET_VOLUME, MAX_OPEN_POSITIONS/OPT-5, survival-mode %20
  tavanı, OPT-3, OPT-2, OPT-6, FRESH_PRICE_ABORT NO-side, YES/NO token-index
  cross-check, OPT-1/OPT-4'ün kasıtlı sapmaları, Sim-Live gap araştırması)
  yeniden incelenmedi — sadece henüz bakılmamış alanlara odaklanıldı: Market
  Filtresi, pozisyon muhasebesi formülü, ve CLAUDE.md/docs'taki diğer
  sayısal kurallar.

## Bugün yapılan işlem: dashboard `min_bet` kontrolü canlı emir boyutlandırmadan kopuktu

`docs/architecture.md`'nin iddiası:
> `min_bet: dashboard 1/5/10/20$ -> orchestrator okur -> bet_size = max(min_bet, kelly)`

Kod incelemesi (`agents/orchestrator.py`):
- `_cycle()` içinde `min_bet_override = float(ctrl.get("min_bet", 1.0))`
  (satır 588) `data/control.json`'dan (dashboard'ın yazdığı dosya) doğru
  okunuyordu.
- Ama tek kullanım yeri `_record_shadow_decisions(...)` (satır 599) — sadece
  shadow/log günlüğü. Gerçek emir boyutlandırma çağrısı
  (`compute_bet_size(..., min_bet=self._min_bet, ...)`, eski satır 717)
  `self._min_bet`'i kullanıyordu — başlangıçta bir kez okunan statik
  `MIN_BET_SIZE` env değişkeni (varsayılan $3.0).
- **Etki:** Kullanıcı dashboard'daki min-bet slider'ını (1/5/10/20$)
  değiştirdiğinde bu değer log'a yazılıyor ve shadow journal'a kaydediliyordu,
  ama gerçek emir boyutuna hiçbir etkisi yoktu — her trade her zaman statik
  $3.0 varsayılanına göre taban buluyordu. Önceki 7 incelemenin hiçbiri bunu
  bulmamış (`docs/reviews/` içinde `min_bet_override` veya dashboard'a dair
  bir not yok) — MIN_MARKET_VOLUME/OPT-2/OPT-3/OPT-6'da görülen "kontrol var
  ama canlı yola bağlı değil" deseninin bir başka örneği.

### Düzeltme
- `agents/orchestrator.py`: `compute_bet_size(...)` çağrısına artık
  `min_bet=self._min_bet` yerine `min_bet=min_bet_override` geçiliyor —
  dashboard artık gerçekten canlı boyutlandırmayı etkiliyor.
- Bunu sararken `compute_bet_size()`'da gizli bir ikinci hata ortaya çıktı:
  fonksiyon `effective_min`'i hiçbir zaman `effective_max`'a karşı
  clamp'lemiyordu. Statik $3.0 ile hiç sorun çıkarmıyordu, ama dashboard'un
  gerçek (1-20$ aralığında) değerleri artık akış içine girince, yüksek
  sermayeli hesaplarda (`capital > ~$100`) min_pct tabanı (`%4`)
  `hard_max_bet` ($4) tavanını aşabiliyordu — taban tavanı geçip "tek trade
  asla $4'ten fazla olamaz" watchdog kuralını sessizce eziyordu (ör.
  capital=$500, dashboard min_bet=$20 → eski kodda bet_size=$20 çıkardı).
  `compute_bet_size()`'a `effective_min = min(effective_min, effective_max)`
  eklendi — taban asla tavanı geçemez.
- `tests/test_min_bet_dashboard_wiring.py` eklendi: (1) `_cycle`'ın
  `compute_bet_size`'ı `min_bet_override` ile çağırdığını kaynak
  incelemesiyle kilitliyor, (2) `compute_bet_size`'ın büyük sermaye + büyük
  dashboard min_bet kombinasyonunda hâlâ hard cap'i ($4) aştığını
  doğrulamıyor.

## Doğrulama
- `pytest tests/` → **592 passed, 2 skipped** (589'dan 592'ye: 3 yeni test
  eklendi, mevcut testlerden hiçbiri bozulmadı — özellikle
  `tests/test_bet_size_position_cap.py`'nin 4 testi de aynen geçti).
- `python -c "import agents.orchestrator"` → hatasız.

## Bugün taranan, sapma bulunmayan alanlar (tek satır özet)
- **Market Filtresi** (`_pre_filter`/`_CRYPTO_UPDOWN_KEYWORDS`,
  `orchestrator.py:1245-1293`): sadece bitcoin/eth/sol/xrp/doge/bnb/hype
  up-or-down — docs ile birebir uyumlu; sports/oscar/politika sızdıran
  `market_classifier.py` zaten "DEVRE DIŞI" işaretli ölü yol, canlı gate
  değil.
- **Pozisyon muhasebesi** (`core/position_manager.py:679`):
  `capital += pnl` (double-count yok), `available_capital()` formülü docs
  ile aynı.
- **Max tek pozisyon %20 / Kelly override yapmaz**: `compute_bet_size`
  hem `effective_min` hem `effective_max`'ı `capital * max_position_pct`'e
  clamp'liyor — hâlâ sağlam.
- `.env.example`'daki eski `MAX_OPEN_POSITIONS=3`/`MIN_EDGE_THRESHOLD=0.12`
  (2. incelemede zaten not edilmiş) — repo'da gerçek bir `.env` olmadığından
  ve değerler zaten daha sıkı (3≤5, 0.12≥0.05) olduğundan etkisiz.

## Sonuç
8. çalışma, dashboard'daki min-bet kontrolünün canlı emir boyutlandırmaya
hiç ulaşmadığı yeni bir "kontrol var ama bağlı değil" hatası buldu ve
düzeltti; düzeltmeyi sararken ortaya çıkan ikincil bir taban/tavan çakışma
riskini de kapattı. Market filtresi, pozisyon muhasebesi ve %20 tavanı gibi
daha önce hiç bakılmamış diğer alanlar denetlendi ve sağlam bulundu.
