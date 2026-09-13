# Günlük Strateji İncelemesi — 2026-09-13 (18. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum özeti
- Branch `claude/brave-faraday-16ye55`, session açılışında görev talimatındaki
  designated branch olarak `main`'e eşit (`86ec7c8`, 16. çalışma) durumdaydı.
- Açılışta açık PR #36 (17. çalışma, `claude/brave-faraday-j1kff1`) bulundu:
  drawdown korumasının gerçek session tepe noktasını hiç takip etmemesi
  (`autonomous_engine.py`) + `test_no_valuation_live_orderbook.py`'nin
  wall-clock flakiness'i. İzole bir git worktree'de bağımsız doğrulandı:
  - `pytest tests/` (worktree, `requirements.txt` kurulduktan sonra) →
    611 passed, 2 skipped.
  - `agents/autonomous_engine.py`'yi geçici olarak pre-fix (`origin/main`)
    haline getirip yeni `test_drawdown_tracks_session_peak.py`'yi tekrar
    çalıştırdım → **2 failed** (`drawdown=0.0%` → `LOW`, beklenen
    `CRITICAL`), post-fix'te **2 passed**. Bulgu doğrulandı.
  - PR squash-merge edildi (`3d19f29`), branch `origin/main`'den yeniden
    başlatıldı.
- `data/control.json`/`.env` bu ortamda yok → gerçek API kimlik bilgisi veya
  canlı pozisyon yok; bu çalışma tamamen statik kod incelemesi + test kanıtı
  üzerinden yürütüldü. `data/status.json`/`positions.json`/`artifacts/*`
  altındaki tarihler (Mart 2026) sentetik örnek/test fixture'ları
  (`daily_report.json` içinde açıkça `"_note": "SYNTHETIC"`) — canlı durum
  değil.

## Bugünkü inceleme kapsamı
Önceki 17 çalışma `arbitrage_engine.py` (boost/reset mantığı, 6 OPT gate),
`autonomous_engine.py` (aggression label, adaptive params wiring, drawdown),
`position_manager.py` (double-count, NO valuation, ET clock), Kelly/MC
sizing, `reviewer_agent.py`/`coordinator.py`/`signal_agent_v2.py`/
`research_agent.py` arasındaki risk-flag tutarlılığını, `control_plane/*`
(5 dosya), `strategies/{edge_model,stoikov,spread_model,sum_monitor,
quality_filter}.py`, dashboard `min_bet` kablolaması ve `calibration/*`'ın
ölü kod olduğunu satır satır doğrulamıştı.

Bugün, orchestrator'ın gerçek import zincirinde olup şimdiye kadar **hiç
test dosyası bulunmayan** bir modüle odaklandım: `agents/subagents/
research_agent.py` (16. çalışma bu dosyanın veri akışını `coordinator.py`
üzerinden zaten satır satır izlemişti, ama dosyanın kendi iç
implementasyonuna — özellikle kaynak yönetimine — girmemişti) ve onun
kullandığı `agents/whale_tracker.py`. `tests/` içinde `research_agent` veya
`whale_tracker` geçen tek bir test dosyası bile yoktu (`grep -rn` ile
doğrulandı) — bu, önceki 17 çalışmanın kapsamadığı bir köşe.

## Bugün bulunan ve düzeltilen gerçek hata: `ResearchAgent`, her cycle'da
## kapatılmayan yeni bir `WhaleTracker`/`httpx.AsyncClient` oluşturuyordu

### Kod incelemesi
`agents/subagents/research_agent.py::_fetch_whale_data()`:
```python
async def _fetch_whale_data(self, candidates):
    try:
        tracker = self._whale_tracker_cls()   # <- HER cagrida yeni instance
        ...
```
`ResearchAgent.run()` içinde bu metod, `candidates` listesi boş olmadığı
her seferde tetikleniyor:
```python
if self._whale_tracker_cls and candidates:
    tasks.append(("whale", self._fetch_whale_data(candidates)))
```
`AgentCoordinator.run_cycle()` orchestrator'ın her döngüsünde (CLAUDE.md:
60-120sn adaptif döngü) çağrılıyor ve kripto up/down market taraması
sürdüğü sürece `candidates` neredeyse hiçbir zaman boş olmuyor — yani bu
kod yolu pratikte **her orchestrator cycle'ında** çalışıyor.

`agents/whale_tracker.py::WhaleTracker.__init__()`:
```python
def __init__(self):
    self.session = httpx.AsyncClient(timeout=15)
```
Sınıfın hiçbir `close()`/`aclose()` metodu yok — session'ı kapatacak hiçbir
mekanizma mevcut değil. Codebase'deki diğer tüm HTTP-destekli, uzun ömürlü
bileşenlerle (`BinanceFeed`, `SmartTraderTracker`, `ArbitrageEngine`,
`PolymarketClient`) karşılaştırıldığında — hepsi `orchestrator.py`'nin
`__init__`'inde **bir kez** kurulup session'ları process ömrü boyunca
yeniden kullanılıyor (`grep -n "AsyncClient(" agents/*.py core/*.py
strategies/*.py` ile doğrulandı, hepsi `self.session = ...` deseninde) —
`WhaleTracker` tek istisna: her `_fetch_whale_data()` çağrısında yeniden
inşa ediliyor ve hemen ardından referanssız bırakılıp atılıyor.

### Etki
CLAUDE.md'nin "Resilience Katmanı (Bot ASLA Durmaz)" prensibi ve botun
20 günlük kesintisiz çalışma hedefi doğrudan etkileniyor: her cycle'da
(60-120sn'de bir, 20 günde onbinlerce kez) yeni bir `httpx.AsyncClient`
(kendi connection pool/socket'leriyle) açılıp asla kapatılmıyor. Python'un
GC'si event-loop'a bağlı async kaynakları düzgün `aclose()` edemediği için
bu, süre içinde biriken açık socket/dosya tanıtıcısı (file descriptor)
sızıntısına yol açar — uzun vadede "too many open files" / bağlantı havuzu
tükenmesiyle botun tam da önlemeye çalıştığı türden bir çökmeye sebep
olabilir. Regresyon testinde reprodüklendi: 5 ardışık `ResearchAgent.run()`
çağrısı (5 orchestrator cycle'ı simülasyonu), düzeltme öncesi kodda
`WhaleTracker` **5 kez** inşa ediliyor (beklenen: 1).

### Düzeltme
`ResearchAgent.__init__`'e `self._whale_tracker = None` eklendi.
`_fetch_whale_data()` artık tracker'ı yalnızca ilk çağrıda (lazy) inşa edip
`self._whale_tracker` üzerinde cache'liyor, sonraki her çağrıda aynı
instance'ı (ve dolayısıyla aynı `httpx.AsyncClient`/connection pool'u)
yeniden kullanıyor — `binance_feed`/`smart_tracker`'ın zaten kullandığı
"bir kez oluştur, process ömrü boyunca paylaş" deseniyle birebir aynı.
Davranış değişikliği yok (hâlâ aynı `get_activity()` çağrıları, aynı
sonuç şekli); tek fark artık tek bir session paylaşılıyor.

`tests/test_research_agent_whale_tracker_reused.py` eklendi: sahte,
kendini sayan bir `_CountingWhaleTracker` ile `ResearchAgent.run()`'ı 5 kez
çağırıp `instances_created == 1` bekliyor.

## Doğrulama
- Düzeltme öncesi (`git stash push -- agents/subagents/research_agent.py`):
  yeni test **FAIL** (`assert 5 == 1`, "5 instances for 5 cycles").
- Düzeltme sonrası (`git stash pop`): yeni test **PASS**.
- `pytest tests/` → **612 passed, 2 skipped** (611 → 612: 1 yeni test
  dosyası, mevcut testlerden hiçbiri bozulmadı).
- `git status` → yalnızca amaçlanan iki değişiklik
  (`agents/subagents/research_agent.py` + yeni test dosyası); test
  çalıştırma yan etkisiyle değişen `data/autonomous_state.json`
  commit'lenmeden geri alındı.

## Sonuç
18. çalışma önce açık PR #36'yı (17. çalışma) bağımsız doğrulayıp merge
etti (drawdown'ın gerçek session tepe noktasını izlemesi — botun büyüdükten
sonra kazancı koruma kabiliyeti için kritik bir düzeltme). Sonra, önceki 17
çalışmanın hiç test dosyası bulunmadığı için değinmediği `research_agent.py`
+ `whale_tracker.py` çiftine odaklanıp gerçek bir kaynak sızıntısı buldu:
`WhaleTracker`, codebase'deki tek "her cycle'da yeniden oluşturulan, asla
kapatılmayan HTTP client" bileşeniydi — botun 20 günlük kesintisiz çalışma
hedefini doğrudan tehdit eden bir dayanıklılık hatası. Düzeltme minimal
(tracker'ı bir kez oluşturup cache'lemek, diğer tüm tracker'larla aynı
deseni uygulamak) ve regresyon testiyle kilitlendi (düzeltme öncesi kodda
testin gerçekten fail ettiği doğrulandı).
