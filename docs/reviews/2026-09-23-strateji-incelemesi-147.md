# 147. Tur Strateji İncelemesi — 2026-09-23

## Kapsam
Planlı ("her gün stratejimizi gözden geçirsin. Amacımız mevcut kapitalimizin
yüzde 10'u kadar kazanmak. Bunun için gereken tüm kararları alabilir ve
uygulayabilirsin") görevin bu turdaki çalıştırması. Tetiklenme: ~08:03 UTC
(2026-09-23).

## Durum tespiti
- Bu tur başladığında `main` üzerinde açık tek PR vardı: **#247** (round-146,
  `claude/brave-faraday-fq8v4l` dalından), 07:15 UTC'de başka bir oturum
  tarafından açılmış. İçeriği: 42+ turdur bilinen kritik onay-kuyruğu
  bypass'ını düzeltiyordu (`agents/orchestrator.py`'deki doğrudan
  `place_order()` çağrısı `_enqueue_order(...)` ile değiştirilmişti).

## Bu turda yapılan doğrulama (bağımsız, kaynaktan)
PR'ı kör güvenmek yerine bağımsız doğruladım:
1. `git diff origin/main...origin/claude/brave-faraday-fq8v4l` ile tam patch
   okundu — sadece `agents/orchestrator.py`, ilgili test dosyası ve iki review
   dosyası değişmiş, kapsam dışı bir şey yok.
2. **İzole worktree'de** (`git worktree add`) PR dalını checkout edip
   `pip install -r requirements.txt` + `python3 -m pytest -q` çalıştırdım:
   **1790 passed, 4 skipped** — PR'ın kendi iddiasıyla birebir eşleşti,
   bağımsız olarak doğrulandı (PR'ın CI durumu boştu: `get_status` → 0 check).
3. Merge sonrası `main` üzerinde `grep -n "place_order(" agents/orchestrator.py`
   → **tek eşleşme kaldı**, `_execute_approved_orders()` içinde. O metodun
   `approved = _get_approved_orders()` listesi yalnızca dashboard'dan
   gerçekten onaylanmış emirleri döndürüyor (`control_plane/approval_queue.py`
   state machine) — yani artık hiçbir kod yolu onaysız gerçek emir veremiyor.
   Bypass tamamen kapandı, kısmi/yarım bir düzeltme değil.
4. Merge sonrası `main`'de tekrar `python3 -m pytest -q`: **1790 passed,
   4 skipped** — regresyon yok.

**PR #247 merge edildi** (`b3fece8`). Merge sonrası `list_pull_requests(state=open)`
boş liste döndürdü — başka açık PR yok.

## Kullanıcının bilmesi gereken netleştirme
Round-146'nın kendi bildirimi "düzeltildi ve pushlandı" diyordu, ama o an
düzeltme yalnızca ayrı bir PR dalındaydı, **`main`'e henüz merge edilmemişti**.
Bu tur o merge'i yaptı ve bağımsız olarak yeniden test etti. Canlı bot bu
repodan (`main`) çalışıyorsa, düzeltmenin fiilen etkili olması için botun
güncel `main`'i çekmesi/deploy etmesi gerekiyor — bu adım kullanıcının kendi
ortamında hâlâ yapılması gereken bir şey.

Davranış değişikliği hatırlatması (round-146'dan): canlı yönlü sinyaller artık
doğrudan emre dönüşmüyor, dashboard onay kuyruğuna giriyor (varsayılan
zaman aşımı 300 saniye). Dashboard düzenli onaylanmazsa emirler EXPIRED olur
ve bot gerçek emir vermeden çalışmaya devam eder.

## Diğer bilinen bulgu
**Zamanlama sıklığı** (106. turdan beri açık, bu turda da **27. kez**
doğrulandı): görev günlük yerine saatlik/düzensiz tetikleniyor. `CronList`
bu oturumda yine "No scheduled jobs" döndürdü — hesap seviyesinde bir ayar,
bu sandbox'tan değiştirilemiyor.

## Yeni kod bulgusu
Bu turda yeni bir bug bulunmadı. Onay-kuyruğu mimarisi artık uçtan uca tutarlı:
enqueue → dashboard onayı → `_execute_approved_orders()` → tek `place_order()`
noktası.

## Canlı sermaye / pozisyon durumu
`data/positions.json`, `data/control.json`, `data/status.json` bu bulut
oturumunda yok. `data/3day_eval.txt` önceki turdan değişmemiş (aynı senkron
anlık görüntü): son 3 gün / 44 trade, gerçek PnL **+$1.01** (52.3% WR) —
"%10 kazanma" hedefinden uzak, pratikte breakeven. Bu sandbox'tan güncel bir
P&L doğrulaması yapılamıyor.

## Sonuç ve bildirim kararı
Kritik güvenlik düzeltmesi (round-146) bu turda **bağımsız olarak yeniden
doğrulandı ve `main`'e merge edildi** — önceki tur yalnızca bir dalda
duruyordu. Bu, kullanıcının canlı ortamının güncel `main`'i çekmesi gerektiği
anlamına geldiğinden ve düzeltmenin fiilen etkili olup olmadığı kullanıcının
kendi deploy adımına bağlı olduğundan, kısa bir bildirim gönderildi.
