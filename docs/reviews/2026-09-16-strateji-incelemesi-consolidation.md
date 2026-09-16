# Günlük Strateji İncelemesi — 2026-09-16 (konsolidasyon turu)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Bulgu — 11 doğrulanmış düzeltme `main`'e hiç ulaşmamıştı

Bu turun başında `main` hâlâ `caf9085` üzerindeydi. Aynı gün içinde ~11
saatlik bir pencerede (05:16–16:15 UTC) açılmış **12 ayrı, unmerged PR**
vardı — #92–#103, her biri "Nth daily review" etiketli (56.–59. turlar arası,
birden fazla paralel oturumun aynı günde farklı "tur" numaralarıyla
çalıştığını gösteriyor), her biri kendi testleriyle doğrulanmış, hepsi
`mergeable_state: clean` (repoda CI yok), ama hiçbiri `main`'e alınmamıştı.
Bu, tam olarak 15 Eylül'deki konsolidasyon turunun ("Sıradaki tur için
öneriler") uyardığı sorunun tekrarı: doğrulanmış düzeltmelerin PR kuyruğunda
birikip canlı koda hiç ulaşmaması.

**Neden önemli:** merge edilmemiş bir düzeltmenin canlı bot davranışına hiçbir
etkisi yoktur. 12 PR'ın kapsadığı hatalar arasında sermaye/risk açısından
kritik olanlar:
- **#97 — çift bot / lock kaçırma:** `ProcessLock.acquire()` Linux'ta eski
  process'i hiç öldürmüyordu (`taskkill` Windows-only, hata sessizce
  yutuluyordu) ama yine de kilidi devralıyordu → iki bot instance'ı aynı anda
  gerçek emir verebilirdi (`INC-2026-03-15-001` senaryosunun ta kendisi).
- **#96 — aggregate boost cap no-op:** SmartMoney/TopTrader/OB_Depth/KalshiArb
  boost'larının toplamını ±0.04 ile sınırlaması gereken kontrol, yanlış
  referans noktası yüzünden hiç çalışmıyordu; bu 4 sinyal aynı yönde
  hizalandığında `bayesian_prob` kontrolsüz +0.07/+0.10 kayabiliyordu.
- **#102 — TF_CONFLICT guard:** 5dk/4sa rejim çakışması bir yönü zorla
  aktive ederken YES fiyat tavanını (`_YES_MAX_PRICE=0.47`) veya NO'nun
  gerçek orderbook/kalite şartlarını hiç kontrol etmiyordu.
- **#98 — single-market arb körlüğü:** gerçek risksiz arbitraj fırsatları
  (`YES_ask+NO_ask<1`) yanlış fiyat kaynağı yüzünden hiçbir zaman
  tespit edilemiyordu.
- **#103 — NO pozisyon değerlemesi:** gerçek sıfır-bid orderbook, "kayıp
  quote" ile aynı sayılıp NEUTRAL kapanışa (gerçek LOSS yerine) yol
  açabiliyordu.
- **#95 + #100 (+ kapatılan #101) — maker pool aşımı:** `MakerEngine` dolu
  envanteri hiç düşmeden pool'un tamamını her döngüde yeniden
  tahsis ediyordu; #100 ayrıca per-side $15 envanter tavanını hiç
  uygulamıyordu (maker şu an `.env`'de kapalı, ama açılırsa risk gerçek).
- Kalan 4'ü (#92, #93, #94, #99): shadow-journal execute kaydı, reviewer
  REDUCE boyut clamp'i, expiry guard atlanması, trade-analyzer dedup
  bozulması.

## Bu turda yapılanlar

Her PR'ın diff'i teker teker okundu (kod + eklenen testler + açıklamadaki
önce/sonra test kanıtı), sonra oluşturulma sırasına göre (en eski önce)
lokal olarak `main` üzerine merge edilip her adımda tam test suite'i
(`pytest tests/`) çalıştırılarak sayı artışı PR'ın kendi iddiasıyla
karşılaştırıldı:

1. `#92` (EXECUTE_YES/EXECUTE_NO) → 762 passed
2. `#93` (suggested_size_pct clamp) → 765 passed
3. `#94` (expiry guard date-only) → 767 passed
4. `#95` (maker committed capital) → 772 passed
5. `#96` (aggregate boost cap) → 773 passed
6. `#97` (process lock SIGTERM/SIGKILL) → 775 passed
7. `#98` (single-market arb + readiness dashboard) → 775 passed (yeni test yok, mevcut testlerle doğrulandı)
8. `#99` (trade analyzer dedup) → 776 passed
9. `#100` (maker per-side inventory cap) → 780 passed
10. **`#101` — MERGE EDİLMEDİ, kapatıldı.** #95 ile aynı kökü (maker pool
    aşımı) farklı bir implementasyonla düzeltiyordu; dalı #95'ten önce
    açılmış olduğu için hem #95'in `get_committed_capital()` public
    metodunu (orchestrator.py hâlâ çağırıyor) kaldırıyor hem de #100'ün
    per-side envanter tavanını geri alıyordu. `git merge` gerçek bir içerik
    çakışması verdi (sadece metin değil, mantıksal regresyon). PR'a
    açıklayıcı yorum bırakılıp "superseded" olarak kapatıldı.
11. `#102` (TF_CONFLICT price/quality gate) → 782 passed
12. `#103` (NO real-zero-bid valuation) → 786 passed

Üç dosyada gerçek üst üste binme vardı (`agents/orchestrator.py`:
#92/#94/#95; `strategies/maker_engine.py`: #95/#100/#101;
`strategies/arbitrage_engine.py`: #96/#98/#102) — hepsi #101 dışında git'in
otomatik 3-way merge'i ile çakışmasız birleşti; her birleşmeden sonra
önceki turların fix'lerinin diff'te hâlâ mevcut olduğu `grep` ile doğrulandı.

Tüm merge'ler GitHub API üzerinden squash-merge olarak uygulandı ve her
adımdan sonra `origin/main`'den fetch edilip aynı doğrulama tekrarlandı.
Son durum: `main` üzerinde **786 passed, 2 skipped, 0 failed**, 0 açık PR.

## Gözlem — inceleme sıklığı (tekrar)

15 Eylül'deki konsolidasyon turu bu sorunu zaten işaret etmiş ve "her turun
sonunda bir önceki açık PR kuyruğu da gözden geçirilmeli" önerisini
bırakmıştı — ama arada geçen dönemde hiçbir tur bunu yapmadı ve kuyruk 12
PR'a kadar büyüdü. Bugünün PR'larının "Nth daily review" numaraları (56, 57,
58, 59) aynı takvim günü içinde karışık sırada — bu, "günde bir" beklenen
görevin göründüğünden çok daha sık, muhtemelen paralel/örtüşen oturumlarla
tetiklendiğini gösteriyor. Bu, tek başına düzeltilebilecek bir kod hatası
değil, zamanlama/orkestrasyon katmanında bir gözlem — kullanıcıya ayrıca
bildirildi.

## Sonuç
`main` artık `#92`–`#100`, `#102`–`#103` dahil 10 doğrulanmış düzeltmeye
sahip; `#101` kasıtlı olarak dışarıda bırakıldı (zaten kapsanmış, birleşimi
regresyon olurdu). Bu oturum yeni bir kod hatası bulmak yerine, zaten
bulunmuş ve doğrulanmış 10 düzeltmenin gerçekten canlı koda ulaşmasını
sağladı — merge edilmemiş bir düzeltmenin canlı performansa hiçbir etkisi
olmadığı için, sermaye hedefine en doğrudan katkı budur.
