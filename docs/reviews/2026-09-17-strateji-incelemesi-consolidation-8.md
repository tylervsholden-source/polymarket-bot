# Günlük Strateji İncelemesi — 2026-09-17 (konsolidasyon turu #8)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = bu branch = `864bffd` (#128 merge). Açık PR
kontrolünde **1 açık PR** bulundu: #129, `claude/brave-faraday-hfnao6`
branch'inden, "fix: shadow-decision journal hardcoded
snapshot_age_seconds=0.0 (73rd daily review)" — başka bir eşzamanlı oturum
tarafından bugünün gerçek hata düzeltmesi zaten açılmış durumda (daha önceki
konsolidasyon turlarının bildirdiği "aynı gün çoklu oturum" zamanlama
sorunu hâlâ geçerli).

## Bu turda yapılanlar
1. Ayrı bir alt-ajan ile canlı yoldaki dosyaların (agents/, core/,
   strategies/) tamamı satır satır tekrar tarandı (~4 saatlik derin geçiş,
   `docs/reviews/*.md`'deki 60+ önceki turla çapraz kontrol edilerek).
   Sonuç: yeni, somut, yüksek güvenilirlikli bir hata bulunamadı — incelenen
   her fonksiyon ya zaten düzeltilmiş (satır içi yorumla belgeli) ya da daha
   önce bilinçli olarak "mimari boşluk" diye bırakılmış (ör. MakerEngine
   envanterinin position_manager'a hiç yansımaması, approval_queue'nun hiç
   enqueue edilmemesi — bkz. `-44.md`, `-47.md`, `-consolidation-6.md`).
2. Alt-ajanın bulduğu en güçlü aday (orta-düşük güven): `has_spot_data`
   yolunda her YES-aktivasyon noktasında uygulanan `_YES_MAX_PRICE=0.47` /
   `_YES_MIN_PRICE=0.15` fiyat bandının, spot veri olmadığı (`else:`,
   ~satır 1548) fallback dalında hiç uygulanmadığı — asimetri gerçek.
   Bunu bizzat düzeltmeyi denedim (sabitleri if/else'in üstüne taşıyıp
   fallback dalına da bandı ekledim). **Sonuç: yanlış pozitifti.**
   `tests/test_no_side_execution_path.py` (2026-09-15 tarihli,
   `_base_market()` docstring'i: "Prices chosen outside the 0.45-0.55
   coin-flip dead zone: YES@0.70 / NO@0.46") ve
   `tests/test_direction_logic.py` bu fallback dalının **bilinçli olarak**
   farklı bir tasarıma sahip olduğunu kanıtlıyor: spot veri yokken sadece
   Bayesian edge eşiği (yes_edge>0, no_edge>0.15) filtre olarak kullanılıyor,
   0.45-0.55 "coin-flip dead zone" dışındaki her fiyat kabul ediliyor —
   0.47 tavanı spot-momentum mantığına özgü, no-data yoluna genellenmesi
   amaçlanmamış. Değişikliği uyguladığımda 4 test kırıldı
   (`test_bullish_yields_yes_direction`, `test_yes_signal_market_price_is_yes_price`,
   `test_yes_direction_uses_yes_token_id`,
   `test_signal_direction_matches_diagnostics_selected_direction_yes`) —
   hepsi bu bilinçli tasarımı doğruluyordu. Değişiklik geri alındı
   (`git checkout -- strategies/arbitrage_engine.py`).
3. Alt-ajanın ikinci adayı (düşük güven): `agents/whale_tracker.py:48`
   `params={"market": condition_id, ...}` — data-api.polymarket.com'un bu
   parametreyi gerçekten filtre olarak kabul edip etmediği bu ortamda
   doğrulanamadı (ağ erişimi engelli — `data-api.polymarket.com`'a `curl`
   407/403 dönüyor). Sıradaki tur için not olarak bırakılıyor; gerçek ağ
   erişimi olan bir oturum doğrulamalı.
4. Tam test suite (geri alma sonrası temiz durumda): **828 passed, 2
   skipped** — regresyon yok, 72. incelemenin bıraktığı durumla birebir aynı.

## Sonuç
Bugün için yeni bir kod değişikliği gerekmedi. Bulunan tek somut asimetri
adayı araştırıldı, denendi ve mevcut test suite'in bilinçli olarak
belgelediği bir tasarım farkı olduğu doğrulandı — hata değil. Bugünün gerçek
düzeltmesi zaten PR #129'da açık durumda, mükerrer çalışma yapılmadı.
CLAUDE.md'nin risk kuralları (max %20 pozisyon, günlük -%15 stop, max 5 açık
pozisyon, min $5,000 hacim, min 0.05 edge) kod tarafında değiştirilmedi.

## Sıradaki tur için notlar
- PR #129 (`snapshot_age_seconds=0.0` düzeltmesi) merge edilmeli/izlenmeli.
- `agents/whale_tracker.py:48`'deki `market` query param'ının
  data-api.polymarket.com için geçerli bir filtre olup olmadığı gerçek ağ
  erişimiyle doğrulanmalı — değilse `WhaleTracker.get_activity()` her
  market için filtrelenmemiş global trade listesi dönüyor olabilir.
- Aynı takvim günü içinde birden fazla oturumun tetiklenmesi sorunu hâlâ
  geçerli (kullanıcı tarafında zamanlama ayarı gerekiyor, önceki
  konsolidasyon turlarının hepsinde tekrarlanan not).
