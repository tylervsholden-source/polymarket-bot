# Günlük Strateji İncelemesi — 2026-09-18 (konsolidasyon turu #11)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = bu branch = `9c6ee20` (#140, 79. inceleme
sonrası). Bu ortamda canlı `data/positions.json` / `data/status.json` yok —
yani bu oturumdan gerçek bir pozisyon açma/kapatma yapılamıyor; elde
edilebilecek tek katkı kod/strateji katmanında. Oturum başladığında **3 açık
PR** bulundu, hepsi bugünün (80./81. inceleme) farklı eşzamanlı
oturumlarından, hiçbiri merge edilmemişti:
- PR #141 — `core/position_manager.py::_check_order_filled()`: tam dolum
  raporu, önceki bir kısmi dolumdan kalan küçültülmüş `pos["amount"]`
  değerini `size_matched` ile güncellemeden donduruyordu (available_capital
  ve close-time P&L'i bozan gerçek bir hata).
- PR #142 — 81. inceleme: yeni hata yok, bağımsız bir sweep (autonomous
  engine yorum hatası, kalshi_arb ilk-eşleşme seçimi, ml_classifier
  train/serve dakika ayrışması — üçü de incelenip düşük öncelikli/ertelenmiş
  olarak reddedildi).
- PR #143 — `agents/orchestrator.py::_update_loss_streak()`: OPT-6 loss-slot
  cooldown, sim/paper modunda (`_is_live_trading()==False`, bu ortamda
  ulaşılabilen tek mod) `_last_loss_slots`'u her cycle'da sadece gerçek CLOB
  `closed` listesinden (sim modunda boş) rebuild ettiği için hiç
  tetiklenmiyordu.

## Bu turda yapılanlar
1. Üç PR'ın da iddialarını kendi checkout'umda (`origin/main`, 9c6ee20)
   bağımsız olarak doğruladım — kod okuyarak ve her PR branch'ini ayrı ayrı
   checkout edip `pip install -r requirements.txt` sonrası
   `python3 -m pytest tests/ -q` çalıştırarak:
   - Baseline (`origin/main`): 844 passed, 2 skipped.
   - PR #141 branch: 846 passed, 2 skipped (iddia edildiği gibi).
   - PR #143 branch: 848 passed, 2 skipped (iddia edildiği gibi).
   - PR #142: kod değişikliği yok, sadece review dokümanı.
   `position_manager.py` düzeltmesinin mantığını satır satır okudum: eski
   kod `MATCHED`/`FILLED` durumunda `size_matched` reconciliation'ından ÖNCE
   erken dönüyordu; yeni kod reconciliation'ı her status için önce yapıp
   sonra dondurma kararını veriyor — mantık doğru.
   `orchestrator.py` düzeltmesini de doğruladım: `loss_slot_source =
   closed if self._is_live_trading() else self._sim_results` — canlı/sim
   ayrımı `_cycle()`'ın kendi trade kayıt mantığıyla tutarlı.
2. PR #141 ve #142 dosya bazında çakışmıyordu (position_manager.py + kendi
   review dokümanı vs. sadece kendi review dokümanı) — ikisini de sırayla
   (squash) merge ettim.
3. PR #143, PR #141 ile aynı review dosya adını (`docs/reviews/2026-09-18-
   strateji-incelemesi-80.md`, ikisi de bağımsız olarak "80. tur" numarası
   kullanmış) paylaştığı için main'e merge sonrası add/add conflict verdi.
   PR #143 branch'ini güncel main ile merge edip çakışmayı elle çözdüm: PR
   #141'in "80" dokümanını olduğu gibi bıraktım, PR #143'ün kendi
   yazısını `2026-09-18-strateji-incelemesi-82.md` olarak yeniden
   adlandırdım (kod dosyası `agents/orchestrator.py` çakışmadı, otomatik
   merge oldu). Merge sonrası tam suite'i tekrar çalıştırdım: **850 passed,
   2 skipped** (846 + PR #143'ün 4 yeni testi) — regresyon yok. Sonra push
   edip PR #143'ü merge ettim.
4. `agents/whale_tracker.py:48`'deki `market` query param sorusunu ve
   Binance/Bitstamp canlı fiyat erişimini tekrar test ettim
   (`data-api.polymarket.com`, `api.binance.com`) — bu oturumun ağ politikası
   da bu host'ları engelliyor (`connect_rejected`, organization policy).
   Soru önceki turlardaki gibi hâlâ doğrulanamadı.

## Sonuç
- Bugünün iki gerçek düzeltmesi (#141, #143) ve 81. incelemenin
  bulgu-yok dokümanı (#142) artık `main`'de. Üç eşzamanlı oturumun hiçbiri
  kendi PR'ını merge etmemişti; bu turun somut katkısı bulguları bağımsız
  doğrulamak, dosya adı çakışmasını çözmek ve temiz bir test taban çizgisi
  (850 passed / 2 skipped) doğrulamak oldu.
- Bu oturumda pozisyon açma/kapama gibi doğrudan bir trading eylemi
  yapılmadı — ortamda canlı sermaye/pozisyon durumu (`data/positions.json`,
  `data/status.json`) bulunmuyor, sadece kod tabanı mevcut. "%10 kazanç"
  hedefine katkı, koddaki gerçek hataları (available_capital/PnL bozulması,
  OPT-6 bounce koruması) canlıya çıkmadan düzeltmek yoluyla.
- CLAUDE.md'nin risk kuralları (max %20 pozisyon, günlük -%15 stop, max 5
  açık pozisyon, min $5,000 hacim, min 0.05 edge) kod tarafında
  değiştirilmedi.

## Sıradaki tur için notlar
- `agents/whale_tracker.py:48`'deki `market` query param sorusu hâlâ
  doğrulanamadı — gerçek prod ağ erişimi olan bir oturum
  `data-api.polymarket.com`'a ulaşabiliyorsa kontrol etmeli.
- Aynı takvim günü içinde birden fazla oturumun tetiklenmesi ve açtıkları
  PR'ları kimsenin merge etmemesi sorunu bugün üçüncü kez tekrarlandı (bkz.
  konsolidasyon-9, konsolidasyon-10). Zamanlama sıklığının azaltılması
  değerlendirilebilir; azaltılmıyorsa her oturumun günün diğer açık PR'larını
  kontrol edip merge etmesi gerekli bir alışkanlık olmaya devam ediyor.
- Bu oturumda canlı pozisyon/sermaye verisi yoktu; eğer bot bu ortamın
  dışında (gerçek ağ erişimi + cüzdan olan bir sunucuda) çalışıyorsa, günlük
  %10 hedefine karşı gerçek ilerlemeyi değerlendirmek için o ortamın
  `data/status.json`/`data/positions.json` dosyalarına erişim gerekir.
