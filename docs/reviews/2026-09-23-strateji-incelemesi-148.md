# 148. Tur Strateji İncelemesi — 2026-09-23

## Kapsam
Planlı ("her gün stratejimizi gözden geçirsin. Amacımız mevcut kapitalimizin
yüzde 10'u kadar kazanmak. Bunun için gereken tüm kararları alabilir ve
uygulayabilirsin") görevin bu turdaki çalıştırması.

## Durum tespiti
Bu tur başladığında `main` üzerinde açık tek PR vardı: **#248** (round-147,
`claude/brave-faraday-5u3l71` dalından), 08:09 UTC'de başka bir eşzamanlı
oturum tarafından açılmış — yalnızca round-147'nin inceleme dosyasını
ekliyordu (kod değişikliği yok). Zamanlama sıklığı sorunu (106. turdan beri
bilinen) bu turda da somut kanıtla (round 147 ve 148 aynı gün art arda
tetiklendi) **28. kez** doğrulandı.

## Bağımsız doğrulama
PR #248'in iddialarına körü körüne güvenmeden kaynaktan doğruladım:
- `grep -n "place_order(" agents/orchestrator.py` → main'de tek eşleşme,
  `_execute_approved_orders()` içinde (satır ~1413), yalnızca dashboard'dan
  gerçekten onaylanmış emirlerle besleniyor.
- Enqueue yolundaki (satır ~1119-1146) `is_approved=True` yalnızca
  LiveGate'in 10 ön-kontrolünü (capital/rate-limit/expiry/...) ön-elemek
  için kullanılıyor; gerçek emir `_enqueue_order()` ile kuyruğa giriyor,
  doğrudan `place_order()` çağrılmıyor. `_execute_approved_orders()`
  içindeki ikinci `is_approved=True` (satır ~1397) yorumla açıkça
  belgelenmiş: "Zaten approved listesinden geldi" + `is_recheck_after_approval=True`.
- `pip install -r requirements.txt` + `python3 -m pytest -q`: **1790
  passed, 4 skipped** — regresyon yok, önceki turlarla aynı sayı.

Sonuç: 42+ turdur bilinen onay-kuyruğu bypass'ı gerçekten kapalı ve `main`
üzerinde stabil. PR #248 doc-only olduğu ve iddiaları bağımsız doğrulandığı
için merge edildi (`cb816dc`).

## ÖNEMLİ — bu turda karşılaşılan yetki sınırı
PR #248 merge edildikten hemen sonra, sıradan bir `git fetch origin main`
komutu dahil sonraki git ağ işlemleri **Claude Code auto-mode classifier**
tarafından "Merge Without Review" gerekçesiyle reddedildi. Bu, harness
seviyesinde bir güvenlik freni: otonom PR merge'lerinin (insan incelemesi
olmadan) sistem tarafından onaylanmayan bir eylem sınıfı olduğunu gösteriyor
— görev talimatının "gereken tüm kararları alabilir ve uygulayabilirsin"
ifadesine rağmen. Bu turda ve round-146/147'de yapılan merge'ler
(PR #247, #248) muhtemelen bu frenin arkasından kaçtı çünkü GitHub API
merge çağrısının kendisi engellenmedi, yalnızca *sonrasındaki* git
işlemleri engellendi.

Bu sınırı zorlamaya çalışmadım (ör. GitHub API ile fetch'i taklit etmek);
bunun yerine bu turun geri kalanını (inceleme dosyası yazımı, mevcut
branch'e commit/push denemesi) bu kısıtlama altında tamamlamaya çalıştım
ve kullanıcıya bildirim gönderdim.

**Kullanıcı için öneri**: Bu görevin standart talimatı ("gereken tüm
kararları alabilir ve uygulayabilirsin") her gün otonom PR merge'lerine
yol açıyor — canlı trading kodu için bu riskli olabilir. Kullanıcı ya (a)
bu yetkiyi yalnızca inceleme/rapor ile sınırlamak, ya da (b) merge'lerin
insan onayından geçmesini (mevcut "Merge Without Review" freniyle uyumlu
şekilde) tercih edip etmediğine karar vermeli.

## Canlı sermaye / pozisyon durumu
`data/positions.json`, `data/control.json`, `data/status.json` bu bulut
oturumunda yok (önceki turlarla aynı). `data/3day_eval.txt` değişmemiş:
son 3 gün / 44 trade, gerçek PnL **+$1.01** (52.3% WR) — "%10 kazanma"
hedefinden uzak. Bu sandbox'tan güncel bir doğrulama yapılamıyor.

## Diğer bilinen bulgu (yeni değil)
`data/trade_patterns.json`: `LOW_EDGE_LOSS` deseni 1008 tekrar, toplam
-$2548 PnL, zaman damgası yok — muhtemelen eski sim verisi, canlı
performansla karıştırılmamalı, ama strateji kalitesi hakkında ayrı bir
endişe kaynağı olarak önceki turlarda da not edildi.

## Sonuç ve bildirim kararı
Bu tur yeni bir kod bulgusu üretmedi (önceki turun düzeltmesi doğrulandı ve
merge edildi), ancak harness seviyesinde yeni ve önemli bir sinyal ortaya
çıktı: otonom PR merge'leri artık "Merge Without Review" ile
reddediliyor. Bu, kullanıcının bilmesi gereken bir kısıtlama/karar noktası
olduğu için bildirim gönderildi.
