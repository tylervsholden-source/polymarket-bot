# Günlük Strateji İncelemesi — 2026-09-17 (konsolidasyon turu #4)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `main` `1e3e1fc` (#112) üzerindeydi. Bu, **bugünün en az
dördüncü** ayrı oturumuydu (64. tur, 65. tur, konsolidasyon #3, ve şimdi bu
tur) — yine tek bir takvim günü içinde. Kontrol edildiğinde **1 doğrulanmış,
unmerged PR** bekliyordu:

- **#113 — 66. tur:** `agents/orchestrator.py::_bond_cycle()` — pozisyon
  `entry_price` olarak `place_passive_order()`'ın döndürdüğü gerçek
  (2 ondalığa yuvarlanmış) fiyat yerine, ön-ayar BondScanner fırsat fiyatı
  (`opp.price`) kaydediliyordu. Bond fırsat fiyatları 2-ondalık temiz değil
  (NO: `round(1.0 - yes_price + 0.02, 4)`; YES: ham Gamma `best_ask`), bu
  yüzden gerçek fill fiyatı `opp.price`'tan sapabiliyordu.
  `PositionManager.add_position()` bunu doğrudan `entry_price` olarak yazıp
  her P&L/kapama hesabı `shares = amount / entry_price` üzerinden
  türetildiği için, her bond trade'de pay sayısı ve getiri sessizce yanlış
  hesaplanıyordu. Aynı hata sınıfı yönlü emir yolunda (`_cycle`) zaten
  düzeltilmişti; bond yoluna hiç uygulanmamıştı.

## Bu turda yapılanlar
1. PR #113'ün diff'i okundu; `core/polymarket_client.py::place_passive_order()`
   içinde `price = round(min(max(price, 0.01), 0.99), 2)` satırı ve
   `order_result["price"]`'ın bu yuvarlanmış değeri döndürdüğü doğrulandı.
   `_bond_cycle()`'ın `"price": opp.price` yazdığı, `order_result.get("price",
   opp.price)` kullanmadığı teyit edildi — iddia doğru, gerçek bir bug.
2. `mergeable_state: clean` doğrulandı. Lokal olarak `origin/main` üzerine
   merge edildi — temiz, çakışma yok.
3. `python3 -m py_compile` + tam test suite: **795 passed, 2 skipped**
   (beklenen 794 taban + 1 yeni test, PR açıklamasıyla birebir eşleşti).
   Ayrıca `calibration/tests` + `execution_realism/tests` +
   `signal_bridge/tests`: **722 passed** — hepsi yeşil.
4. GitHub API üzerinden squash-merge: `expectedHeadSha` PR'ın son commit'ine
   kilitlendi → `d0b2051`. Merge sonrası `origin/main` fetch edilip gerçek
   main üzerinde test suite tekrar çalıştırıldı: **795 passed, 2 skipped**
   (lokal simülasyonla birebir aynı).
5. Açık PR kontrolü: **0 open PR** kaldı.

## Neden bu, sermaye hedefine en doğrudan katkı
Bond stratejisi gerçek pozisyonlar açıyor; yanlış `entry_price` her kapanışta
gerçek P&L'i (ve `shares` sayısını) sessizce çarpıtıyordu — bu doğrudan
sermaye muhasebesini bozan bir hata sınıfı. Düzeltmenin PR kuyruğunda
beklemesi canlı davranışa hiçbir fayda sağlamaz; bu turun katkısı onu
doğrulayıp `main`'e taşımaktı.

## Gözlem — zamanlama sorunu tekrar (kullanıcıya bildirildi)
15 Eylül, 16 Eylül (×2) ve şimdi 17 Eylül'de **dördüncü kez aynı gün**: aynı
kalıp tekrarlanıyor — "günde bir" beklenen zamanlanmış görev, aynı takvim
günü içinde birden fazla örtüşen oturumla tetikleniyor (bugün tek başına en
az 4 oturum: 64, 65, konsolidasyon #3, ve bu tur). Önceki konsolidasyon turu
(#3) bunu zaten not etmişti; sorun hâlâ devam ediyor, bu yüzden bu turda
kullanıcıya doğrudan bir bildirim (push notification) gönderildi. Bu, kod ile
düzeltilemeyecek bir zamanlama/scheduled-task yapılandırma sorunu —
kullanıcının zamanlama aralığını (muhtemelen günlük yerine saatlik/sık
tetikleniyor) kontrol etmesi gerekiyor.

## Sonuç
`main` artık `#113` dahil, 0 açık PR ile güncel (tip: `d0b2051`). Tam test
suite gerçek main üzerinde yeşil (795 passed, 2 skipped).
