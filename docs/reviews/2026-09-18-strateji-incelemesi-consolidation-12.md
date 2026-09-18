# Günlük Strateji İncelemesi — 2026-09-18 (konsolidasyon turu #12)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = `3355a01` (#145, 83. inceleme sonrası). Bu
ortamda canlı `data/positions.json` / `data/status.json` yok — yani bu
oturumdan gerçek bir pozisyon açma/kapatma yapılamıyor; elde edilebilecek tek
katkı kod/strateji katmanında. Oturum başladığında **2 açık PR** bulundu,
ikisi de bugünün (84. inceleme) farklı eşzamanlı oturumlarından, hiçbiri
merge edilmemişti:
- PR #146 — `agents/kalshi_arb.py::get_edge_adjustment()`: `self._cache`
  aynı asset için birden fazla ticker/vade tutabildiğinden, kod
  `matching[0]` (insertion-order'daki ilk eşleşme) yerine `timestamp`'i en
  yeni olan girdiyi seçmeliydi — düzeltilmeden önce eski bir fiyat, canlı
  `bayesian_prob`'a beslenen `_kalshi_adj`'de bazen işareti ters bir
  adjustment üretebiliyordu.
- PR #147 — `agents/orchestrator.py`: `_cycle()`'ın `closed_trades`
  anlık görüntüsü (Dynamic Kelly `update_streak`, Walk-Forward `validate`,
  `AutonomousDecisionEngine.evaluate`) ve `run()`'ın `get_adaptive_params()`
  çağrısı, `_update_loss_streak()`'in 82./83. incelemede düzeltilen
  live/sim ayrımını hiç görmeden koşulsuz `position_manager.data["closed"]`
  okuyordu — sim/paper modunda (fiili varsayılan) bu liste hep boş
  kaldığından dört ayrı risk/boyut adaptasyonu mekanizması sessizce inert
  kalıyordu.

## Bu turda yapılanlar
1. İki PR'ın da iddialarını kendi checkout'umda (`origin/main`, `3355a01`)
   bağımsız olarak doğruladım: her iki PR diff'ini okudum, dosya bazında
   çakışmadıklarını (`agents/kalshi_arb.py` vs `agents/orchestrator.py`)
   doğruladım.
2. Her iki branch'i lokal bir test branch'inde sırayla merge edip
   (fast-forward + clean merge, çakışma yok) tam test suite'i çalıştırdım:
   `pip install -r requirements.txt` + `pip install scikit-learn` sonrası
   `python3 -m pytest -q` → **1692 passed, 4 skipped** (bu repo'daki tüm
   test dizinleri dahil; PR'ların kendi bildirdiği 853/854/859 rakamları
   sadece `tests/` alt kümesini kapsıyor — tutarsızlık yok, sadece kapsam
   farkı).
3. Regresyon yok, mantık her iki PR'da da doğru (Kalshi: `max(matching,
   key=lambda v: v["timestamp"])`; orchestrator: tek bir
   `_current_closed_trades()` helper'ına konsolide edilmiş dört çağrı
   noktası) — PR #146'yı, ardından PR #147'yi `main`'e merge ettim.

## Sonuç
- Bugünün iki gerçek düzeltmesi artık `main`'de. Her iki oturum da kendi
  PR'ını merge etmemişti; bu turun somut katkısı bulguları bağımsız
  doğrulamak ve temiz bir test taban çizgisi (1692 passed / 4 skipped)
  doğrulamak oldu.
- Bu oturumda pozisyon açma/kapama gibi doğrudan bir trading eylemi
  yapılmadı — ortamda canlı sermaye/pozisyon durumu bulunmuyor, sadece kod
  tabanı mevcut. "%10 kazanç" hedefine katkı, koddaki gerçek hataları
  (Kalshi cross-arb sinyal işareti, sim-mode risk adaptasyonu sessizliği)
  canlıya çıkmadan düzeltmek yoluyla.
- CLAUDE.md'nin risk kuralları (max %20 pozisyon, günlük -%15 stop, max 5
  açık pozisyon, min $5,000 hacim, min 0.05 edge) kod tarafında
  değiştirilmedi.

## Sıradaki tur için notlar
- `agents/whale_tracker.py:48`'deki `market` query param sorusu bu turda da
  doğrulanamadı (`data-api.polymarket.com`'a erişim bu oturumda da
  EGRESS_BLOCKED).
- Aynı takvim günü içinde birden fazla oturumun tetiklenip açtıkları PR'ları
  kimsenin merge etmemesi deseni bugün de tekrarlandı (bkz. konsolidasyon
  #9, #10, #11). Zamanlama sıklığının azaltılması değerlendirilebilir;
  azaltılmıyorsa her oturumun günün diğer açık PR'larını kontrol edip
  merge etmesi gerekli bir alışkanlık olmaya devam ediyor.
- Bu oturumda canlı pozisyon/sermaye verisi yoktu; eğer bot bu ortamın
  dışında (gerçek ağ erişimi + cüzdan olan bir sunucuda) çalışıyorsa, günlük
  %10 hedefine karşı gerçek ilerlemeyi değerlendirmek için o ortamın
  `data/status.json`/`data/positions.json` dosyalarına erişim gerekir.
