# Günlük Strateji İncelemesi — 2026-09-14 (32. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum özeti
- Bu oturum açıldığında `claude/brave-faraday-myvsz9` branch'i zaten
  `origin/main` (`eb170aa`, 31. çalışmanın sonucu — PR #57) ile birebir
  aynıydı, bekleyen PR yoktu. Taban test suite: `pytest tests/ -q` →
  **672 passed, 2 skipped**.
- Önceki turlarda "temiz" olarak işaretlenen alanlar (`coordinator.py`,
  `signal_agent_v2.py`, `kelly_criterion.py::update_streak`,
  `polymarket_client.py`'nin daha önce bakılan kısımları, `main.py`,
  `backtesting/engine.py`) hariç tutularak, o zamana kadar hiç ayrıntılı
  incelenmemiş canlı-yol dosyalarında (`strategies/arbitrage_engine.py`,
  `strategies/bayesian.py`, `strategies/edge_model.py`,
  `strategies/stoikov.py`, `strategies/monte_carlo.py`,
  `agents/market_classifier.py`, `agents/resilience.py`,
  `agents/smart_trader_tracker.py`, `agents/autonomous_engine.py`'nin
  MED/LOW/SURVIVAL dalları, `core/position_manager.py`'nin kalan kısımları)
  yeni bir tur inceleme yapıldı.

## ⚠️ KRİTİK — CANLI SERMAYE MUHASEBESİNİ DOĞRUDAN BOZAN BULGU
Bu turun bulgusu, `docs/architecture.md`'nin de vurguladığı üzere
`position_manager`'ın kendi P&L hesabının güvenilmez kanıtlandığı (752
yanlış resolution → $670 tracking hatası) ve bu yüzden **CLOB bakiyesinin
tek doğruluk kaynağı** ilan edildiği mekanizmanın ta kendisinde. Yani hata,
projenin "sermaye muhasebesini düzeltmek için eklenen" korumanın içinde —
ve döngü başına (60-120sn'de bir) koşulsuz çalışıyor.

## Bugün yapılan işlem: `get_real_balance()` başarısız çağrıda gerçek $0 bakiye ile ayırt edilemeyen `0.0` döndürüyordu

### Hata
`core/polymarket_client.py::PolymarketClient.get_real_balance()`:
```python
def get_real_balance(self) -> float:
    if not self._clob:
        return 0.0          # CLOB yapılandırılmamış — "bilinmiyor" değil, "$0" gibi okunuyor
    try:
        ...
        return balance
    except Exception as e:
        logger.warning(f"Bakiye alınamadı: {e}")
        return 0.0          # geçici API hatası da aynı sentinel'i döndürüyor
```

`agents/orchestrator.py::Orchestrator._sync_real_balance()` — `run()`
içinde başlangıçta bir kez (satır 366) ve her cycle sonunda (satır 406)
koşulsuz çağrılıyor, live/sim modundan bağımsız:
```python
balance = self.client.get_real_balance()
if balance < 0:
    return                         # başarısız çağrıyı atlamak İÇİN var
...
new_capital = balance + locked
self.position_manager.data["capital"] = round(new_capital, 4)
self.position_manager._save()
```

`if balance < 0: return` koruması, `get_real_balance()`'ın başarısızlığı
negatif bir sentinel ile bildirmesi **niyetiyle** yazılmış — ama fonksiyon
hiçbir zaman negatif döndürmüyor. Yani:
- CLOB client hiç yapılandırılmamışsa (ör. `docs/api_guide.md`'nin resmen
  belgelediği "API key olmadan da çalışır" simülasyon modu),
- veya tam yapılandırılmış canlı bir hesapta CLOB bakiye endpoint'i geçici
  bir hata fırlatırsa (ağ blip'i, rate limit, vb.),

`0.0`, `if balance < 0` korumasından geçip **gerçek bakiye gibi** işleniyor.
Sonuç: `capital`, sadece o an açık (henüz resolve olmamış) pozisyonların
toplamına (`locked`) eşitlenip **diske kaydediliyor** — gerçek sermayenin
geri kalanı sessizce yok ediliyor. Bu bozuk `capital` değeri doğrudan Kelly
pozisyon boyutlandırmasını, `AutonomousDecisionEngine`'in SURVIVAL-mode
eşiğini ve CLAUDE.md'nin taviz vermediği günlük -%15 stop-loss'unu
uygulayan `PositionManager.daily_loss_exceeded()`'i besliyor.

`git blame` orijinal `feat` commit'ine (`9b5fd52`) kadar izlendi — CLOB
bakiyesinin "tek gerçek kaynak" yapıldığı mimari karar bu commit'te
geldi, ama başarısızlık sentinel'i hiçbir zaman doğru kodlanmamış; önceki
31 günlük incelemenin hiçbiri bu fonksiyona dokunmamış.

### Düzeltme
Her iki başarısızlık dönüşü `0.0` yerine `-1.0` yapıldı — `_sync_real_
balance()`'ta zaten var olan `if balance < 0` korumasıyla birebir
eşleşiyor. Başarılı bir çağrıda gerçek $0.00 bakiye hâlâ tam olarak `0.0`
dönüyor (ayrı bir testle doğrulandı). `_sync_real_balance()`'ın kendisi ve
`locked` hesaplama mantığı dokunulmadı.

`tests/test_balance_sync_failure_sentinel.py` eklendi (5 test):
1. CLOB yapılandırılmamışken sentinel negatif mi.
2. CLOB çağrısı exception fırlatınca sentinel negatif mi.
3. Gerçek $0 bakiyede hâlâ tam `0.0` dönüyor mu (regresyon değil).
4. Uçtan uca: gerçek `Orchestrator._sync_real_balance()` çağrısı — başarısız
   fetch sonrası `capital` $500'den bozulmadan kalıyor mu (asıl senaryo).
5. Uçtan uca non-regresyon: başarılı bir fetch hâlâ `capital`'i doğru
   güncelliyor mu (`balance + locked`).

## Doğrulama
- Fix öncesi (`git stash` ile sadece kaynak dosya fix'i geri alınmış):
  `pytest tests/test_balance_sync_failure_sentinel.py -v` → **3 failed, 2
  passed** — asıl entegrasyon testi `capital was corrupted to 5.0` diye
  fail ediyor (log: `CAPITAL_SYNC: $500.0000 → $5.0000`), tam yukarıda
  anlatılan sermaye yok etme senaryosunu birebir üretiyor.
- Fix sonrası: aynı dosya → **5/5 pass**.
- Tam suite (fix sonrası): `pytest tests/ -q` → **677 passed, 2 skipped**
  (672'den 677'ye — sadece bu turun 5 yeni testi, sıfır regresyon).
- `git diff core/polymarket_client.py` → iki satırlık değişiklik (`0.0` →
  `-1.0`, her iki başarısızlık dalında) + açıklayıcı docstring; `_clob`
  bağlantı mantığı, başarılı yol, `_sync_real_balance()` dokunulmadı.
- Test çalıştırmalarının yan etkisi olan `data/autonomous_state.json`
  commit öncesi eski haline döndürüldü.

## İncelenip hata bulunamayan alanlar (bu turda)
- `strategies/arbitrage_engine.py`, `strategies/bayesian.py`,
  `strategies/edge_model.py`, `strategies/stoikov.py`,
  `strategies/monte_carlo.py`, `strategies/spread_model.py`,
  `strategies/quality_filter.py`, `strategies/orderbook_analyzer.py`
- `agents/market_classifier.py`, `agents/resilience.py`,
  `agents/smart_trader_tracker.py`, `agents/hit_rate_tracker.py`
- `agents/autonomous_engine.py`'nin MED/LOW risk dalları, SURVIVAL modu,
  drawdown/volatilite adaptasyonu (HIGH dalı 28. çalışmada, reviewer
  verdict entegrasyonu 29. çalışmada zaten düzeltildi)
- `agents/orchestrator.py`'nin `directional_count` ve stop-loss UTC
  midnight dışındaki kalan cycle mantığı
- `core/position_manager.py`'nin kalan açma/kapama muhasebesi

Detaylar için arka plandaki inceleme oturumunun tam raporuna bakılabilir
(bu PR'ın açıklamasında özetlenmiştir).

## Sonuç
32. çalışma, `position_manager`'ın güvenilmez P&L hesabına karşı "CLOB
bakiyesi tek doğruluk kaynağıdır" diye eklenen korumanın kendisinde kritik
bir hata buldu: başarısız bir bakiye çağrısı gerçek bir $0 bakiyeyle
ayırt edilemiyordu, bu yüzden her geçici API hatası (ya da API key'siz
simülasyon modu) canlı `capital`'i sessizce sadece kilitli pozisyon
toplamına düşürüp diske yazıyordu — Kelly boyutlandırma, SURVIVAL-mode ve
günlük -%15 stop-loss'un hepsini bozan bir sınıf hatası. Zaten var olan
`if balance < 0` korumasıyla eşleşecek şekilde iki satırlık minimal bir
değişiklikle (`0.0` → `-1.0` sentinel) kapatıldı, beş regresyon testiyle
kilitlendi.
