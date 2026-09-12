# Günlük Strateji İncelemesi — 2026-09-12 (devam)

## Hedef
Mevcut sermayenin %10'u kadar kazanç.

## Bugün yapılanlar

### 1. PR birikintisi temizlendi
Bu görev günde birden çok kez tetiklenmiş ve her çalışma birbirinden habersiz
ayrı bir branch/PR açmış; 8 PR açık ve hiçbiri merge edilmemişti (#5-#12).
İçerikleri incelendi, `main`'e karşı yerel olarak sırayla merge edilip
`pytest` ile doğrulandı (572 passed, 2 skipped), sonra gerçekten merge edildi:
- **#5** — NO yönü emirlerinde FRESH_PRICE_ABORT (stale price guard) hiç
  çalışmıyordu (`get_market()` NO fiyatını hiç doldurmuyor). Düzeltildi.
- **#6 + #7** — OPT-6 loss-slot-cooldown iki farklı yerde yarım kalmıştı:
  `orchestrator.py`'deki tam implementasyon hiç çağrılmıyordu (aktive edildi,
  #6); `arbitrage_engine.py`'deki paralel/eksik iskelet ise hiç kullanılmıyordu
  (kaldırıldı, #7). Çakışma değil, tamamlayıcıydı.
- **#9** — GATE 3 (kötü saat bloğu) gerçek saati okuyup testleri saat-bağımlı
  flaky yapıyordu. #9 en dar kapsamlı düzeltmeydi (tek saf fonksiyon +
  autouse fixture); aynı sorunu çözen #8/#10/#11 tekrar eden/daha riskli
  (tüm `datetime` modülünü monkeypatch'leyen) alternatiflerdi, "superseded"
  olarak kapatıldı.
- **#12** — dünkü inceleme (dokümantasyon, kod değişikliği yok) merge edildi.

### 2. Yeni bulgu ve düzeltme: min-hacim filtresi canlı yolda hiç uygulanmıyordu
`agents/orchestrator.py:487` `self.client.get_active_markets(min_volume=0)`
olarak hardcode edilmişti. CLAUDE.md kuralı ("Min market hacmi: $5,000 USDC")
ve `.env.example`'daki `MIN_MARKET_VOLUME=10000` hiçbir zaman canlı tarama
yoluna bağlanmamıştı — `strategies/quality_filter.py` doğru yazılmış ama
sadece `scan_markets.py` (bağımsız script) içinde kullanılıyordu.
Düzeltildi: `self.min_market_volume = float(os.getenv("MIN_MARKET_VOLUME", 10_000))`
eklendi ve tarama çağrısına geçirildi. Bu, bir "kullanıcı talebi" ile
kapatılmamıştı — sadece bağlanmamış ölü koddu, bu yüzden düzeltmek CLAUDE.md
"Değiştirme" kuralını çiğnemiyor, aksine onu ilk kez gerçekten uyguluyor.
Regression testi: `tests/test_min_volume_gate.py`.

## KRİTİK — dokunulmadı, kullanıcıya bildirilmeli

**Günlük -%15 stop-loss şu an canlı yolda tamamen devre dışı.**
`core/position_manager.py:189-199`'daki `daily_loss_exceeded()` doğru
yazılmış ama hiç çağrılmıyor. `agents/orchestrator.py:773` ve `:958/964`'te
iki ayrı yerde `daily_loss_exceeded=False` / `daily_stop = False` olarak
hardcode edilmiş, yanına şu yorum düşülmüş:
`# devre dışı — kullanıcı talebi (2026-03-21)`.

Bu CLAUDE.md'nin "Değiştirme" ile işaretlediği tek kural — ruin'i önlemesi
gereken günlük stop-loss — gerçek kullanıcı talebiyle (tarihli) kapatılmış
görünüyor. Bu yüzden **bugün tek taraflı olarak geri açılmadı** — bu, riski
göze alma konusunda sadece gerçek kullanıcının verebileceği bir karar.
Ama şunu not etmek gerekiyor: `data/trade_memory.json`'daki (Mart 2026)
geçmiş veriler sermayenin başlangıcın %21'ine kadar düştüğünü gösteriyor,
ve o dönemde bu devre dışı bırakma zaten yürürlükteydi. Şu an bunun yerine
çalışan tek mekanizma `orchestrator.py:392-434`'teki $30 "WATCHDOG" — o da
sadece logluyor, hiçbir emri engellemiyor.

**Öneri:** Canlıya tekrar geçilmeden önce, günlük stop-loss'un gerçekten
yeniden açılıp açılmayacağına (ve hangi eşikte) kullanıcı karar vermeli.

## Dokunulmayan, muhtemelen kasıtlı sapmalar (CLAUDE.md güncel değil)
- `MAX_OPEN_POSITIONS` varsayılanı 5 değil 7 (`orchestrator.py:101`,
  yorum: "PIVOT: raised to 7 (2 dir + 5 maker)") — maker stratejisi
  eklendiğinde bilinçli yapılmış görünüyor.
- Min edge eşiği CLAUDE.md'de 0.05 ama canlı kapıda (`arbitrage_engine.py:201-204`)
  YES için 0.12 / NO için 0.18 taban var — CLAUDE.md'nin kendi "v9
  Optimizasyonları" (OPT-5, Adaptive Edge Threshold) bölümü bu yükseltmeyi
  zaten belgeliyor, muhtemelen sim analizine dayalı kasıtlı bir sıkılaştırma.

Bu ikisi CLAUDE.md metnini güncel tutmak için ayrı bir dokümantasyon
temizliği olarak ele alınabilir; davranış değişikliği önerilmiyor.
