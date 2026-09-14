# Günlük Strateji İncelemesi — 2026-09-14 (22. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum özeti
- Bu oturum açıldığında `main`'de (`b7cb554`, 20. çalışmanın sonucu) bekleyen
  bir PR vardı: **#42**, "SmartTraderTracker never purges closed positions
  from cache" (21. çalışma). `agents/smart_trader_tracker.py::_fetch_positions()`
  bir trader'ın API'nin artık döndürmediği (kapatılmış) pozisyonlarını
  `self._positions` cache'inden hiç silmiyordu — `get_signal()` bu stale
  veriyi `strategies/arbitrage_engine.py`'nin `bayesian_prob`'una (satır
  ~621-634, feature flag yok, her cycle/her market) süresiz olarak
  besliyordu.
- Doğrulama: izole worktree'de PR #42 branch'i (`origin/main` ile birebir
  güncel) checkout edilip kod bağımsız olarak yeniden okundu
  (`agents/smart_trader_tracker.py`, `strategies/arbitrage_engine.py`) ve
  iddia doğrulandı. `pytest tests/test_smart_trader_stale_position_purge.py`
  fix öncesi (dosyanın `b7cb554` revizyonuyla) **1 fail** (`assert 1.0 ==
  0.0`), fix sonrası **2 passed** — gerçek A/B doğrulaması yapıldı. Tam suite:
  **627 passed, 1 failed, 2 skipped** — tek hata (`test_reduce_verdict_size_
  not_double_applied.py`) PR'ın diff'iyle ilgisiz, saat-bağımlı (bkz. aşağı);
  aynı hata `main`'in PR öncesi haliyle de birebir aynı şekilde tetikleniyor,
  yani PR'ın kendisi bir regresyon yaratmıyor. **PR #42 squash-merge edildi.**
- Yerel `claude/brave-faraday-azees4` branch'i, merge sonrası `origin/main`
  (`879811a`) tepesine `git reset --hard` ile hizalandı (branch'in kendine
  özgü commit'i yoktu, `git log origin/main..azees4` boştu).
- **Yan not (bug olarak raporlanmadı):** `tests/test_reduce_verdict_size_not_
  double_applied.py::test_reduce_verdict_does_not_shrink_size_multiplier_
  below_suggested` şu anda `main`'de de bağımsız olarak fail ediyor — ama
  bunun sebebi bir kod regresyonu değil, testin `agents/autonomous_engine.py`
  içindeki `LOW_LIQUIDITY_HOURS` (UTC 0-6 → ×0.7) zaman-bazlı gate'ini mock'
  lamadan gerçek `time.gmtime()` saatine bağlı kalması — oturum tam UTC 00:xx
  civarında çalıştığı için test'in varsaydığı "başka risk faktörü yok"
  koşulu bozuluyor. Üretim davranışı kendi içinde tutarlı (gece saatlerinde
  boyut küçültme, diğer risk faktörleriyle `min()` ile birleşiyor — ikinci
  test `test_reduce_verdict_still_composes_with_independent_risk_factors`
  zaten bu birleşmeyi doğru bekliyor); tek sorun testin flaky olması, canlı
  paraya etkisi yok. Kapsam dışı bırakıldı, aşağıdaki asıl bulguyla
  karıştırılmadı.

## Bugün yapılan işlem: AutonomousDecisionEngine'in risk-bazlı boyut küçültmesi, Kelly tabanına geri yapıştırılarak etkisizleştiriliyordu

### Hata
`agents/orchestrator.py`'nin ana sinyal döngüsünde, `compute_bet_size()`
(Kelly'nin `signal.size`'ını sermaye-ölçekli bir min/max banda sıkıştıran
saf fonksiyon, 5. çalışmada `cf72288` ile çıkarıldı) çağrıldıktan hemen sonra
`AutonomousDecisionEngine.evaluate()`'in ürettiği `size_multiplier` şöyle
uygulanıyordu:

```python
if _auto_size_mult < 1.0:
    original_bet = bet_size
    bet_size = max(_effective_min, bet_size * _auto_size_mult)
```

`_effective_min`, `compute_bet_size()`'ın döndürdüğü, **Kelly'nin kendi
`signal_size`'ını** sermaye/`dashboard min_bet`'e göre taşımayan alt sınırdır
— küçük hesaplarda tipik olarak $2-4 arası. Ama `compute_bet_size()`'ın
kendisi zaten `signal_size` bu tabanın altındaysa (`edge` zayıfsa — yani tam
da risk korumasının en çok gerektiği durumda) `bet_size`'ı `effective_min`'e
kadar yukarı tasıyor. Sonuç: `bet_size` bu blok'a girerken çoğu zaman zaten
`effective_min`'e eşit. Bu durumda:

```
bet_size * multiplier < effective_min        (multiplier < 1.0 için her zaman)
max(effective_min, bet_size * multiplier) == effective_min
```

yani `AutonomousDecisionEngine`'in ürettiği **her** koruyucu küçültme —
`REVIEWER_VETO` (×0.25), `CRITICAL`/`DRAWDOWN` (×0.3-0.5), `LOSS_STREAK`
(×0.5-0.6), `SURVIVAL_MODE` (×0.3) — sessizce sıfırlanıyordu ve emir,
onaylanmış/riski düşük bir sinyalle **birebir aynı** boyutta gönderiliyordu.
Bu, mimarinin (`CLAUDE.md`: "Otonom Karar Akışı — Risk skor hesapla ... Final:
EXECUTE/REDUCED/SKIP + size_multiplier") anlattığı korumanın tam tersini
gerçek emirlerde yaratıyordu: en riskli sinyaller (VETO edilmiş olanlar dahil)
tam boyutta işlem görüyordu.

Bunun bir tasarım kararı değil, gözden kaçmış bir hata olduğu iki şekilde
doğrulandı:
1. Aynı döngüde birkaç satır aşağıda uygulanan **iki farklı** boyut ayarı
   (`wf_mult` — walk-forward güven çarpanı, `self._adaptive_bet_multiplier` —
   performans bazlı adaptif çarpan) çarpanı **doğrudan** uyguluyor
   (`bet_size *= wf_mult`), `effective_min`'e geri yapıştırmıyor — yani aynı
   fonksiyonun içinde üç benzer ayardan sadece biri bu hatalı davranışa
   sahip, tutarsızlık hatayı işaret ediyor.
2. `git log -p -L` ile satırın geçmişi izlendiğinde, bu kod ilk defa
   projenin orijinal "feat" commit'inde (`9b5fd52`) tanıtılmış ve o günden bu
   yana (21 günlük inceleme dahil) hiç değiştirilmemiş — hiçbir önceki
   inceleme bu spesifik satırı incelememiş.

**Etki:** Bu hata, botun kendi "otonom karar motoru"nun (mimarideki en
belirgin risk-yönetim katmanlarından biri) küçük/orta sermayeli hesaplarda —
tam da $1000→$3000 hedefi süresince beklenen sermaye aralığı — pratikte
neredeyse hiç iş yapmadığı anlamına geliyor: Kelly zaten zayıf/marjinal bir
sinyal önerdiğinde (bet_size floor'a oturduğunda) risk motoru "küçült"
dediğinde bile emir küçülmüyordu.

### Düzeltme
- `agents/orchestrator.py`: `max(_effective_min, bet_size * _auto_size_mult)`
  satırı, yeni saf bir fonksiyona (`apply_risk_size_multiplier(bet_size,
  multiplier) -> bet_size * multiplier`) çıkarıldı ve çağrı yeri bunu
  kullanacak şekilde değiştirildi — artık `wf_mult`/`_adaptive_bet_multiplier`
  ile birebir aynı davranış (doğrudan çarpma, floor'a geri yapıştırma yok).
  Fonksiyon `compute_bet_size()`'ın hemen altına eklendi (aynı emsal:
  5. çalışmada inline mantık test edilebilir saf fonksiyona çıkarılmıştı).
- Tek satırlık minimal değişiklik + yeni fonksiyonun docstring'i; başka
  davranış değiştirilmedi.
- `tests/test_autonomous_size_reduction_not_reclamped.py` eklendi (4 test):
  `compute_bet_size()` ile bet_size'ı gerçekten `effective_min`'e oturtan
  gerçekçi senaryolar kurup (1) eski formülün (`max(effective_min,
  bet_size*mult)`, testte açıkça yeniden yazıldı) küçültmeyi sıfırladığını
  doğruluyor, (2) yeni `apply_risk_size_multiplier()`'ın çarpanı gerçekten
  uyguladığını doğruluyor, (3) `multiplier=1.0`'ın no-op olduğunu, (4)
  bet_size floor'un üzerindeyken de çarpanın tam uygulandığını doğruluyor.
  Ayrıca fix öncesi `agents/orchestrator.py` revizyonu (`879811a`) geçici
  olarak geri getirilip test çalıştırıldı: `apply_risk_size_multiplier`
  fonksiyonu o revizyonda hiç yok — `ImportError` ile collection hatası —
  bağımsız olarak fix'in öncesinde bu korumanın test edilebilir/var
  olmadığı doğrulandı.

## Doğrulama
- PR #42 (izole worktree, merge öncesi): `pytest tests/` → 627 passed,
  1 failed (saat-bağımlı, main'de de aynı), 2 skipped.
- Bugünkü düzeltme + PR #42 sonrası `main`: `pytest tests/` → **631 passed,
  1 failed (aynı önceden var olan saat-bağımlı test), 2 skipped** (627'den
  631'e: 4 yeni test eklendi, mevcut testlerden hiçbiri bozulmadı).
- Fix öncesi dosya revizyonuyla yeni test dosyası → `ImportError` (fonksiyon
  yok) — beklenen, doğrulandı.
- `git diff agents/orchestrator.py` → tek, minimal değişiklik (yeni saf
  fonksiyon + tek satır çağrı-yeri değişimi), başka hiçbir davranış
  değiştirilmedi.
- Test çalıştırmalarının yan etkisi olan `data/autonomous_state.json`
  commit öncesi eski haline döndürüldü.

## Sonuç
22. çalışma önce bekleyen PR #42'yi (SmartTraderTracker'ın kapatılmış
pozisyonları cache'den hiç silmemesi) bağımsız olarak doğrulayıp merge etti,
sonra 21 önceki incelemenin hiç bakmadığı bir alanda — otonom karar
motorunun risk-bazlı boyut küçültmesinin gerçek emir boyutuna nasıl
uygulandığı — yeni ve canlı yola bağlı gerçek bir hata buldu: küçültme,
Kelly'nin kendi tabanına geri yapıştırılarak sessizce iptal ediliyordu, özellikle
tam da risk korumasının en çok gerekli olduğu zayıf-sinyal/küçük-sermaye
senaryolarında. Minimal bir düzeltmeyle (üç benzer boyut-ayarından ikisiyle
tutarlı hale getirilerek) kapatıldı ve dört regresyon testiyle kilitlendi.
