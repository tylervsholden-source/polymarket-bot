# Günlük Strateji İncelemesi — 2026-09-17 (konsolidasyon turu #6)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Bu, **bugünün en az yedinci** ayrı oturumu (64, 65, konsolidasyon #3/#4/#5,
ve şimdi bu tur). Konsolidasyon #4'ün bildirdiği zamanlama sorunu (tek
"günlük" görev aynı takvim günü içinde tekrar tekrar tetikleniyor) hâlâ
geçerli görünüyor — bu turda tekrar bildirmedim, yeni bir bilgi yok, sadece
aynı örüntü tekrarlanıyor (konsolidasyon #5'in de yaptığı gibi).

Oturum başında `origin/main` = bu branch = `9f71b61` (#119). **0 open PR**
(GitHub API ile doğrulandı).

## Bu turda yapılanlar
1. `git fetch origin main` — branch zaten `origin/main` ile birebir aynı.
2. Bağımlılıklar taze container'da yoktu, kuruldu (kod değişikliği değil).
3. Tam test suite: **807 passed, 2 skipped** — konsolidasyon #5 ile birebir
   aynı sayı, regresyon yok.
4. Konsolidasyon #5'in devrettiği not üzerine, `strategies/arbitrage_engine.py`
   içinde daha önce sistematik taranmamış üç bölüm satır satır incelendi:
   - **COMPOSITE TECH SCORE GATE** (~L1397-1410): `tech_score` sadece
     logluyor, blok mantığı ("TECH_BLOCK kaldırıldı — sinyal neyse o")
     bilinçli olarak devre dışı. Sorun yok.
   - **REGIME ADDON / effective_min_edge** (~L1704-1749): OPT-5 adaptif
     min-edge formülü (`_base_edge + _regime_addon`) elle hesaplanıp koddaki
     mantıkla karşılaştırıldı — `_follows_regime`/`_is_neutral` dallanması
     ve `_regime_str * 0.08` / `* 0.10` çarpanları CLAUDE.md'nin v9 OPT-5
     spesifikasyonuyla tutarlı. Sorun yok.
   - **ML SCORE / GOLDEN HOUR** (~L1839-1909): `ml_score` capping
     (`_thin_liquidity`, `_macro_contradicts`) ve `_GOLDEN_HOURS = {17,18,19}`
     seti ilk bakışta yorumdaki "5-8PM" ile çelişiyor gibi göründü, ancak
     "5-8PM" ifadesi saat *aralığı* (17:00-20:00, yani 17/18/19 saatlerini
     kapsayan 3 saatlik pencere) anlamında kullanılmış — kod doğru,
     yanlış pozitif. `MAX_BET_CAP_POST_BOOST` (post-boost re-cap) önceki bir
     turda zaten eklenmiş, hâlâ doğru çalışıyor. Sorun yok.
5. `control_plane/live_gate.py::_check_rate_limit/_check_readiness/_check_live_trading`
   tek tek elle doğrulandı (65. çalışmanın kapattığı fail-open sınıfına
   yeniden düşüp düşmediklerini kontrol için) — üçü de doğru, `_check_readiness`
   hâlâ `generated_utc` eksikse fail-closed. Sorun yok.
6. `agents/orchestrator.py`'nin iki `check_live_gate()` çağrı noktası
   (`_cycle` doğrudan emir yolu ve `_execute_approved_orders`) tekrar
   incelendi — `tests/test_expiry_guard_date_only_wiring.py`'nin kapattığı
   "T in end_iso" hatası hâlâ düzeltilmiş halde (`if end_iso else None` /
   `if _order_end_iso else None`), gerileme yok.
7. **Gözlem (aksiyon alınmadı)**: `agents/orchestrator.py`, `core.approval_queue`'dan
   `enqueue` fonksiyonunu import ediyor (`_enqueue_order`) ama **hiçbir yerde
   çağırmıyor** — canlı emir yolu tamamen "DOĞRUDAN EMİR VER (onay kuyruğu
   bypass)" yorumuyla belgelenmiş şekilde `_enqueue_order`'ı atlıyor.
   `control_plane/approval_queue.py`'nin kendi docstring'i bunu
   "INC-2026-03-15-001 dersi: Sinyal → emir arasında insan onayı ZORUNLU"
   olarak tanımlıyor, yani onay kuyruğu başlangıçta zorunlu bir güvenlik
   katmanı olarak tasarlanmış. Ancak CLAUDE.md'nin v3 mimarisi
   (`AutonomousDecisionEngine.evaluate() → EXECUTE/EXECUTE_REDUCED/SKIP/DEFER`)
   insan onayını değil otonom karar motorunu birincil kontrol olarak
   tanımlıyor — yani bu, bir hata değil, v3'e geçişte kasıtlı olarak
   bypass edilmiş eski bir güvenlik katmanı (dashboard'daki onay
   kuyruğu UI'ı da bu yüzden hep boş kalıyor, `web_server.py:195/280`).
   Kod değişikliği önerilmiyor — bu bir mimari/ürün kararı, dar kapsamlı bir
   "bug fix" değil; ancak sıradaki tur için not olarak bırakılıyor (bkz.
   aşağı).

## Sonuç
`main` = bu branch, `9f71b61`, **0 open PR**, tam test suite yeşil (807
passed / 2 skipped). CLAUDE.md'nin risk kuralları (max %20 pozisyon, günlük
-%15 stop, max 5 açık pozisyon, min $5,000 hacim, min 0.05 edge) kod
tarafında değiştirilmedi ve ihlal bulunmadı. Bu turda üç önceden taranmamış
kod bölgesi (tech score gate, regime addon, ml score/golden hour) satır
satır incelendi, gerçek bir hata bulunmadı — bugün için yeni bir kod
değişikliği gerekmedi.

## Sıradaki tur için notlar (devralınan + yeni)
- Zamanlama sorunu (aynı günde çoklu oturum) hâlâ kullanıcı tarafında
  çözülmeyi bekliyor — kod ile düzeltilemez.
- `MC_GATE_ENFORCE=true` geçişi hâlâ bekliyor (devralınan, gerçek canlı
  `MC_GATE_SHADOW` logu toplanmadan).
- `data/trade_memory.json`'daki `CAPITAL_LOW` uyarısı hâlâ ~6 ay eski sim
  artığı, gerçek bir sinyal değil (devralınan).
- **Yeni**: Onay kuyruğu (`control_plane/approval_queue.py` +
  `agents/orchestrator.py`'nin kullanmadığı `_enqueue_order` importu +
  dashboard'daki boş onay UI'ı) tamamen ölü kod — v3 otonom mimariye geçişte
  bilinçli olarak bypass edilmiş görünüyor. Bir hata değil, ama gelecekte
  temizlik (import kaldırma, dashboard UI'ı kaldırma/güncelleme) gerekip
  gerekmediği kullanıcıya sorulabilir — bu tur kapsamına dahil edilmedi
  (mimari karar, "sadelik" ilkesi gereği tek taraflı dokunulmadı).
- `strategies/arbitrage_engine.py`'nin şimdi taranan üç bölümü (tech score,
  regime addon, ml score/golden hour) dahil, dosyanın geri kalanı hâlâ
  büyük ve yoğun — `agents/subagents/*.py`, `agents/latency_arb.py`,
  `agents/kalshi_arb.py` gibi az taranmış alanlar (65. çalışmanın notu)
  hâlâ sistematik olarak taranmadı.
