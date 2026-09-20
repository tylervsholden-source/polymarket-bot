# Günlük Strateji İncelemesi — 2026-09-20 (101. tur, konsolidasyon)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = `7aaf0ae` (#175, 99. tur sonrası) idi. Aynı
gün içinde üç ayrı paralel "100. tur" oturumu bağımsız olarak çalışmış ve
üç ayrı açık PR bırakmıştı:

- **#178** — `fix: cycle risk-budget ceiling ignored carried-over sim
  position capital` — sim/paper modda `directional_locked`'ın
  `self._sim_trades`'teki hâlâ açık pozisyonların kilitlediği sermayeyi
  hiç saymaması (94. turun pozisyon-SAYISI limitlerinde düzelttiği aynı
  boşluğun dolar-bazlı tavan için düzeltilmemiş hâli). Gerçek, canlı
  karara etkili bir hata.
- **#177** ve **#176** — bağımsız "yeni hata bulunamadı" taramaları
  (aynı gün, aynı taban commit).

Konteynerde çalışan bir bot instance'ı yok (`data/status.json`/
`control.json`/`positions.json` bu sandbox'ta yok, ağ erişimi yok) —
%10 hedefine karşı gerçek zamanlı sermaye ilerlemesi bu oturumdan
doğrulanamıyor; katkı kod/strateji doğruluğu seviyesinde.

## Bu turda yapılanlar

1. **#178'in bağımsız doğrulaması**: PR'ı ayrı bir worktree'de checkout
   edip yeni testleri (`tests/test_cycle_risk_budget_includes_sim_locked.py`,
   5 test) ve tüm test suite'ini (`922 passed, 2 skipped`) çalıştırdım;
   ayrıca `compute_sim_trades_locked()`'ın çağrı noktasını ve
   `sim_entry["size"]` alanının gerçekten set edildiğini kaynak kodundan
   elle doğruladım. Hata gerçek, düzeltme minimal ve doğru — **main'e
   merge edildi** (`d2aaa76`).
2. **#177 ve #176 kapatıldı**: aynı günün aynı taban commit'i üzerinden
   çalışan, kod değişikliği içermeyen yinelenen taramalar. Her ikisine de
   #178'in merge edildiğini ve üç ayrı incelemenin aynı anda merge
   edilmesinin sadece çakışma/gürültü yaratacağını belirten birer yorum
   bırakılıp kapatıldı.
3. **Ek nokta kontrolü**: görev bütçesi dahilinde `signal_bridge/`
   modülü (bir önceki 100. tur raporunun "sıradaki tur için" önerdiği,
   az taranmış alanlardan biri) hızlıca tarandı.
   `grep -rl signal_bridge --include=*.py .` → tek gerçek referans
   `_gen_snapshot.py`/`_build_zip.py` (arşiv/rapor üretim script'leri) ve
   `review_bundle/`, `incident_bundle*/` altındaki eski anlık görüntü
   kopyaları; `agents/orchestrator.py` veya `agents/subagents/*.py`'de
   hiç import edilmiyor. Yani modül canlı/sim karar yoluna hiç
   bağlanmamış — 88. ve 100. turlarda aynı gerekçeyle atlanan diğer ölü
   kod örnekleriyle (approval_queue, maker_engine.check_paired_profit)
   aynı sınıftan, bugünkü karara sıfır etkisi var. Dokunulmadı.

## Sonuç
Bugünkü asıl katkı: üç paralel incelemenin ürettiği gerçek hatayı
doğrulayıp main'e taşımak ve geri kalan yinelenen çalışmayı temizlemek.
Ayrıca yapılan ek taramada yeni bir hata bulunmadı.

## Sıradaki tur için notlar
- `crypto_directional/` (feature pipeline/labeling/backtests) ve
  `agents/{binance_feed,ml_classifier,walk_forward}.py`'nin tam satır
  satır yeniden okunması hâlâ 100. turun önerdiği en verimli hedef —
  bu turda zaman bütçesi konsolidasyona gittiği için ele alınmadı.
- Aynı gün içinde birden fazla paralel "günlük inceleme" oturumunun
  tetiklenmesi tekrar oldu (3. kez). Otomasyonu tetikleyen zamanlayıcı
  tarafında bir çakışma/tekilleştirme sorunu olabilir — bot sahibinin
  bilgisine.
