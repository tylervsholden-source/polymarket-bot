# Günlük Strateji İncelemesi — 2026-09-21 (106. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Bu turun kapsamı — bilinçli olarak sınırlı

Bu turda **yeni bir paralel derin-tarama fan-out'u başlatılmadı**. Sebebi
aşağıdaki tespit: son birkaç günde art arda gözlemlenen paralel-oturum
çakışmaları (103. tur: 2 çakışma, 104. tur: 7 çakışma, bu turdan önce
105. tur: 3 ayrı eşzamanlı oturum) zaten kendi başına bir maliyet
oluşturuyor — her round yeni bir fan-out başlattıkça çakışma riski ve
tekrar eden "no bug found" raporu hacmi büyüyor. Bu tur onun yerine (1)
main'de birikmiş, zaten doğrulanmış açık PR'ları konsolide etti ve (2) asıl
kısıtlayıcı bulguyu — otomasyonun günlük değil saatlik tetiklendiği —
belgeledi.

## Yapılanlar

### 1. Açık PR konsolidasyonu (#197, #198, #199, #200)
Round başladığında main = `230c718` idi ama main'e alınmamış 4 açık PR
vardı — hepsi aynı taban commit üzerinden bağımsız "105. tur" oturumlarının
ürünüydü (bu kez dosya adı çakışması yoktu, önceki turların rename
konvansiyonu her oturum tarafından zaten uygulanmıştı):

| PR | Tür | Özet |
|----|-----|------|
| #197 | **fix** | `monitoring/regime_review.py`: tam olarak 1 pencere oluştuğunda (`n`=20-29, varsayılan `window_size=20`) trend karşılaştırması hiç çalışmıyor, `is_stable` yanlışlıkla `True` dönüyordu → GREEN → `orchestrator.py._readiness_clears_live()` canlı emir akışını açma kararına hatalı katkı. Artık "unknown ≠ stable" kuralına tabi, WARN veriyor. |
| #198 | docs-only | `latency_arb.py`/`maker_engine.py`/`market_index_watcher.py` satır satır incelendi, gerçek düzeltme gerektiren bir şey bulunamadı (daha önceki turlarda zaten düzeltilmişler doğrulandı). |
| #199 | **fix** | `strategies/arbitrage_engine.py`: OVERPRICED MARKET BLOCK, kendi yorumu ve `docs/PRICING_SANITY_SPEC.md`'nin belgelediği `1.10` yerine `1.50` eşiği kullanıyordu — YES+NO toplamı 1.10-1.50 arası (sahte-edge, likidite sorunlu) marketler bu korumayı hiç tetiklemeden emir akışına ulaşabiliyordu. Eşik `1.10`'a düzeltildi. |
| #200 | docs-only | 104. turdan devreden 14 aday dosya iki paralel ajanla tam taranmış, yeni canlı-karar hatası bulunamamış. |

Yerel worktree'de sırayla merge edilip (`git merge --no-edit`), `origin/main`
ile fark alınarak diff'in tam olarak 4 PR'ın birleşimi olduğu (kayıp/çakışma
yok) doğrulandı, sonra tam suite çalıştırıldı: **1790 passed, 4 skipped**
(baseline 1784 + #197'nin 3 testi + #199'un 3 testi, 0 regresyon). Ardından
GitHub üzerinden 4 PR de sırayla (#197→#198→#199→#200) normal `merge`
yöntemiyle main'e alındı. Merge sonrası `origin/main` (`3be6454`) taze bir
worktree'de yeniden test edildi: **1790 passed, 4 skipped** — merge
commit'lerinin kendisi regresyon getirmedi.

**İki gerçek canlı-karar düzeltmesi** main'e alınmış oldu (#197 rejim
kararlılık yanlış-GREEN, #199 overpriced-block eşik hatası) — ikisi de risk
azaltıcı yönde (yanlış "güvenli/geç" sinyalini önlüyor), sermaye riskini
artıran değil.

### 2. Kritik bulgu: "günlük" görev aslında saatlik tetikleniyor

Bu görev talimatı "her gün stratejimizi gözden geçirsin" diyor, ancak repo
geçmişi bunun günlük değil **saatte bir** tetiklendiğini gösteriyor:

- Son 4 günün commit sayısı: 17 Eylül 21, 18 Eylül 34, 19 Eylül 38,
  20 Eylül 47 — artan bir hacim.
- 20 Eylül'de "104. tur" etiketli **9 ayrı eşzamanlı oturum** aynı taban
  commit'e karşı bağımsız PR açmıştı (#187-195), 7'si aynı dosya adına
  çakışıyordu. "105. tur" da benzer şekilde en az 3 eşzamanlı oturumla
  başladı (#197-200'ün 3'ü ayrı ayrı "105. tur" etiketli).
- Round zaman damgaları 20 Eylül boyunca yaklaşık saatlik aralıklarla
  dizili: 09:09, 10:12, 11:10, 12:14, 13:11, 14:08, 15:10, 16:11, 17:08,
  18:12, 19:12, 20:10, 21:09, 22:14 — bu "günde bir" değil, "günde ~14
  kez" demek.

**Sonuç:** Bu otomasyon muhtemelen tasarlanandan çok daha sık çalışıyor.
Etkisi: (a) her round'da anlamlı yeni sermaye kararı yok (bot instance'ı
bu sandbox'ta hiç çalışmıyor, `data/control.json`/`status.json`/
`positions.json` yok, canlı API erişimi yok — %10 hedefine karşı gerçek
ilerleme bu ortamdan hiçbir zaman doğrulanamaz), (b) her round yine de tam
bir Claude Code oturumu (çoğu zaman birden fazla paralel oturum) tüketiyor,
(c) çakışan PR'lar art arda konsolidasyon yükü yaratıyor. Bu, kullanıcının
"günlük" niyetiyle örtüşmüyor gibi görünüyor — tetikleyicinin (trig_id:
`trig_018B5PhS4xUw24eBYCFrUW6X`, "Strateji Rutin") gerçek zamanlama
ayarının kontrol edilmesi öneriliyor.

## Sermaye/performans notu
100'den fazla önceki turda tutarlı şekilde tespit edildiği gibi: bu
sandbox'ta çalışan bir bot instance'ı yok, bu yüzden %10 hedefine karşı
gerçek zamanlı sermaye ilerlemesi bu oturumdan doğrulanamıyor.

## Sıradaki tur için notlar
- **Öncelik:** Tetikleyici sıklığı kullanıcıya bildirildi (bu round bir
  push notification gönderdi). Sonraki round'lar, kullanıcı sıklığı
  değiştirmediyse, gereksiz paralel fan-out başlatmaktan kaçınıp önce açık
  PR/main durumunu kontrol etmeli.
- 104. ve 105. turlardan devreden, henüz kullanıcı kararı bekleyen açık
  mimari sorular hâlâ geçerli: (1) onay kuyruğu/doğrudan emir yolu
  çelişkisi, (2) `enhanced_signals.py`'nin confluence/risk-flag'e hiç
  bağlı olmaması, (3) `copytrade.py`'nin ölü kod olması, (4)
  `top_trader_signal.py`'nin `TOP_TRADERS` listesinin kullanılmaması,
  (5) `write_readiness_verdict()` manuel-gate sorusu.
- Henüz derinlemesine taranmamış adaylar: `agents/{latency_arb sonrası
  dead-code temizliği, context_fetcher}.py`, `strategies/{quality_filter,
  sum_monitor,bond_scanner,walk_forward,stoikov}.py` (stoikov'daki
  potansiyel mantık sorunu #197'nin PR notunda işaretlendi, düzeltme
  yapılmadı).
