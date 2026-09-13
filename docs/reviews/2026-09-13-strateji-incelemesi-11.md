# Günlük Strateji İncelemesi — 2026-09-13 (11. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum özeti
- Bu çalışmaya başlarken **iki açık PR** vardı: #27 (9. çalışma, kod
  değişikliği yok — OPT-1/decay-pause'un tekrar doğrulanması) ve #28 (10.
  çalışma, gerçek bir hata: reviewer REDUCE verdict'inin `suggested_size_pct`'i
  coordinator + autonomous_engine'de iki kez uygulanıyordu). Her ikisi de bu
  çalışma tarafından doğrulandı (kod okuması + `pytest tests/` → 594 passed,
  2 skipped, iddia edilenle birebir eşleşti) ve squash-merge edildi
  (main artık `890c81e`).
- `data/control.json` / `data/positions.json` / `.env` bu checkout'ta yine
  yoktu → kapatılacak/açılacak gerçek bir pozisyon yoktu.

## Bugün bulunan hata: `AutonomousDecisionEngine.get_adaptive_params()`'ın min_edge/bet-boyutu alanları hesaplanıp atılıyordu

### Kod incelemesi
- `agents/autonomous_engine.py:404-443` — `get_adaptive_params()`, docstring'i
  gereği ("Orchestrator bu değerleri okuyup uygulayabilir") performansa göre
  `min_edge_yes`, `min_edge_no` ve `max_bet_multiplier` öneriyor: win_rate<%40
  (10+ trade) → DEFENSIVE (`max_bet_multiplier=0.6`, edge eşikleri yükselir);
  capital<$10 → SURVIVAL (`max_bet_multiplier=0.3`, edge eşikleri daha da
  yükselir); win_rate>%65 (10+ trade) → AGGRESSIVE (`max_bet_multiplier=1.15`,
  `min_edge_yes` düşer).
- `agents/orchestrator.py:384` (düzeltme öncesi) — bu sözlüğün repo genelinde
  **tek çağrı yeri**: `run()` döngüsünün sonunda, sadece `cycle_interval_seconds`
  (bekleme süresi) ve `aggression` (log string'i) okunuyordu.
  `min_edge_yes`/`min_edge_no`/`max_bet_multiplier` hesaplanıp hiçbir yere
  yazılmadan atılıyordu.
- Gerçek canlı edge gate'i `strategies/arbitrage_engine.py:224-225`'te
  (`self.min_edge_yes = max(0.12, _env_edge)`, `self.min_edge_no = max(0.18,
  _env_edge)`) engine constructor'ında **bir kez** set ediliyor, sonra hiç
  mutasyona uğramıyor (repo genelinde grep: bu iki attribute'a başka hiçbir
  yazma yok). Gerçek bet boyutu da `compute_bet_size()` + `_auto_size_mult`
  (autonomous engine'in per-signal `evaluate()`'i, PR #28'de düzeltilen ayrı
  bir yol) + walk-forward çarpanından geliyor — `max_bet_multiplier`'a hiç
  referans yok.

### Etki
Bot kendi performansına göre DEFENSIVE/SURVIVAL moduna geçtiğine "inanıyor"
(log'da `ADAPTIVE_INTERVAL` mesajıyla görünüyor) ve daha sıkı edge eşiği +
daha küçük bet boyutu öneriyordu, ama gerçek emir yolu bunu hiç görmüyordu —
CLAUDE.md'nin "Kritik Keşifler" bölümünün altını çizdiği tam senaryoda
(win-rate düşüşü, sim-live gap) bot düşük kaliteli/aynı boyutta trade almaya
DEVAM ediyordu, tam da performans kötüleştiğinde sıkılaşması gerekirken.
Önceki 10 incelemedeki "kontrol var ama canlı yola bağlı değil" deseninin bir
tekrarı, ama daha önce hiç bakılmamış bir dosyada (`get_adaptive_params`,
`run()`'daki tek çağrı noktası dışında hiç referans alınmamıştı).

### Düzeltme
- `agents/orchestrator.py.__init__`: `arb_engine` oluşturulduktan hemen sonra
  statik taban değerleri sakla: `self._base_min_edge_yes`,
  `self._base_min_edge_no` (arb_engine'in kurucudan gelen 0.12/0.18'i);
  `self._adaptive_bet_multiplier = 1.0` başlangıç değeri.
- `run()`: `adaptive = self.autonomous_engine.get_adaptive_params()`'dan hemen
  sonra:
  - `self.arb_engine.min_edge_yes = max(self._base_min_edge_yes,
    adaptive["min_edge_yes"])` ve aynısı `min_edge_no` için — **max() ile**,
    çünkü NORMAL/AGGRESSIVE modun varsayılan önerileri (0.08/0.15) statik
    tabandan (0.12/0.18) daha düşük; direkt atama yapılsaydı NORMAL modda
    canlı edge eşiği gevşetilmiş olurdu (mevcut, kasıtlı "Makul: sadece
    gerçek edge varsa gir" davranışının tersi). `max()` sadece DEFENSIVE/
    SURVIVAL'ın sıkılaştırmasına izin veriyor, hiçbir zaman gevşetmiyor.
  - `self._adaptive_bet_multiplier = adaptive["max_bet_multiplier"]` bir
    sonraki `_cycle()` çağrısı için saklanıyor.
- `_cycle()`: walk-forward çarpanından hemen sonra, `bet_size *=
  self._adaptive_bet_multiplier` (≠1.0 ise) — mevcut `HARD_MAX_BET=$4` ve
  capital yeterlilik kontrolleri bu adımdan SONRA çalışıyor, yani
  AGGRESSIVE'in olası boyut artışı (×1.15) da her zaman hard cap'e tabi
  kalıyor.
- `tests/test_adaptive_params_wiring.py` eklendi: (1) `run()` kaynağının
  `arb_engine.min_edge_yes/no`'yu `max(base, adaptive)` ile güncellediğini,
  (2) `_cycle()`'ın `bet_size *= self._adaptive_bet_multiplier` uyguladığını,
  (3) `__init__`'in taban değerleri ve başlangıç çarpanını sakladığını,
  (4) DEFENSIVE önerisinin (0.10) statik tabanın (0.12) altına
  düşürülmediğini, (5) SURVIVAL önerisinin (0.25) statik tabanın (0.18)
  üstüne sıkılaştırdığını doğruluyor.

## Doğrulama
- `python3 -c "import agents.orchestrator"` → hatasız.
- `pytest tests/` → **599 passed, 2 skipped** (594'ten 599'a: 5 yeni test
  eklendi, mevcut testlerden hiçbiri bozulmadı).

## Sonuç
11. çalışma önce açık iki PR'ı (#27 doğrulama, #28 REDUCE double-apply
düzeltmesi) doğrulayıp merge etti, sonra daha önce hiç incelenmemiş
`get_adaptive_params()`'ın performans-bazlı risk sıkılaştırmasının canlı
yola hiç bağlı olmadığını buldu ve düzeltti — DEFENSIVE/SURVIVAL modda artık
gerçek edge eşiği yükseliyor ve gerçek bet boyutu küçülüyor, min_edge hiçbir
zaman kasıtlı statik tabanın altına düşürülmüyor. Değişiklik minimal (üç
küçük ekleme + regresyon testi), mevcut davranış korunarak sadece eksik
bağlantı tamamlandı.
