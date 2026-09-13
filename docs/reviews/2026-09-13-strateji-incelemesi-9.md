# Günlük Strateji İncelemesi — 2026-09-13 (9. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum özeti
- Açık PR yok. `origin/main` HEAD `effed4c` (8. çalışmanın sonucu, #26) ile
  local çalışma ağacı birebir aynıydı, çalışma ağacı temizdi.
- `pytest tests/` → **592 passed, 2 skipped** — 8. çalışmanın bıraktığı
  durumla eşleşti, regresyon yok.

## Bugün incelenen ve YANLIŞ ALARM olduğu anlaşılan konu: OPT-1 hard-cap + v8 decay-pause

`strategies/arbitrage_engine.py:1534` içindeki
`# REGIME_STR_CAP + REGIME_DECAY kaldırıldı — sinyal neyse o` yorumunu ve
`self._regime_decay_pause`'ın hesaplanıp hiçbir yerde okunmamasını, önceki
günlerde OPT-2/OPT-3/OPT-6/min_bet'te bulunan "kontrol var ama canlı yola
bağlı değil" deseninin bir tekrarı sandım. Bunun üzerine `_evaluate_market`
zincirine iki yeni hard-block gate (`_regime_strength_cap_blocks_no`,
`_regime_decay_blocks_no`) ekledim, izole testler yazdım (12/12 geçti),
tam test paketi 600/2'ye çıktı.

Commit atmadan önce `docs/reviews/` geçmişini bu iki konu için taradım ve bu
değerlendirmenin **3 ayrı önceki incelemede zaten kasıtlı olarak yapılmış ve
her seferinde tekrar doğrulanmış** olduğunu gördüm:

- **4. çalışma** (`ae0d9c3`, #22): "OPT-1'in sert bloku kaldırılmış, ama
  `_regime_addon = regime_str × 0.08/0.10` ile OPT-5'in min-edge formülüne
  gömülü — aynı korumayı sürekli bir eşik olarak sağlıyor. Canlı, kasıtlı
  tasarım." Aynı incelemede `_regime_decay_pause` de ayrıca ele alınmış:
  CLAUDE.md'nin v9 tablosunda bu isimle bir OPT listelenmediği için "gevşetilmiş
  bir non-negotiable kural değil, kullanılmayan eski (v8) kod" olarak
  değerlendirilip dokunulmamış.
- **5. çalışma** (`32483a2`, #23): OPT-1..OPT-6 taraması tekrarlanmış, aynı
  sonuç doğrulanmış.
- **6. çalışma** (`ae0d9c3`→`49b7969`, #24): OPT-2 gerçek hatası düzeltilirken
  OPT-1 tekrar "regime-addon'a evrilmiş, kasıtlı — dokunulmadı" olarak
  işaretlenmiş.

Yani OPT-1'in sert bloğu, OPT-5'in edge eşiğine sürekli/kademeli bir ceza
olarak taşınmış — cliff-edge blok yerine tercih edilen, belgelenmiş bir
tasarım kararı. Benim eklediğim hard-block bunun üzerine binerdi (çifte
ceza) ve üç bağımsız incelemenin vardığı, tutarlı bir sonucu gerekçesiz
şekilde tersine çevirirdi. Bu nedenle **değişikliği geri aldım**
(`git checkout -- strategies/arbitrage_engine.py`, yeni test dosyası
silindi) — commit edilmedi, `main`'e hiç gitmedi.

## Bugün ayrıca bakılan, sapma bulunmayan alan
- `strategies/kelly_criterion.py`: formül (`f*=(bp-q)/b`), adaptive fraction
  (0.25→0.35), streak multiplier (0.70-1.30), regime-strength azaltımı,
  `max_position_pct` tavanı (`min(capital*kelly_f, max_size)`) — hepsi
  docs/strategy.md ve CLAUDE.md ile tutarlı, dokunulmadı.
- `KellyCriterion.should_enter()` canlı yolda hiçbir yerden çağrılmıyor
  (yalnız kendi birim testinde kullanılıyor) — ama bu, önceki günlerin
  bulduğu "kontrol var ama bağlı değil" hatalarından farklı: gerçek edge
  gate'i zaten `arbitrage_engine.py`'deki `effective_min_edge` kontrolüyle
  (0.12/0.18, bu metodun varsayılan 0.08'inden daha sıkı) uygulanıyor, o
  yüzden bu metodun çağrılmaması davranışı gevşetmiyor. Bilgi notu olarak
  bırakıldı, kod değişikliği gerektirmiyor.

## Doğrulama
- `pytest tests/` (geri alma sonrası) → **592 passed, 2 skipped** — 8.
  çalışmanın bıraktığı durumla birebir, hiçbir yeni test eklenmedi/kalmadı.
- `git status` → temiz, `origin/main` ile birebir aynı.

## Sonuç
9. çalışma, ilk bakışta önceki günlerin "kontrol var ama bağlı değil"
deseniyle örtüşen bir konuyu (OPT-1 hard-cap, v8 decay-pause) daha derin
incelemeyle **kasıtlı ve zaten üç kez doğrulanmış bir tasarım kararı**
olarak teyit etti ve kendi hatalı düzeltmesini commit'lemeden geri aldı.
Yeni bir kod değişikliği gerekmedi; `main` mevcut haliyle CLAUDE.md/docs
kurallarıyla tutarlı kalmaya devam ediyor.
