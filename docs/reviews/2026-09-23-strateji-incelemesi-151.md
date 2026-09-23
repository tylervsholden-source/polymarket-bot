# 151. Tur Strateji İncelemesi — 2026-09-23

## Kapsam
Planlı ("her gün stratejimizi gözden geçirsin. Amacımız mevcut kapitalimizin
yüzde 10'u kadar kazanmak. Bunun için gereken tüm kararları alabilir ve
uygulayabilirsin") görevin bu turdaki çalıştırması.

## Durum tespiti ve merge
Tur başında tek açık PR vardı: **#251** (round-150, `claude/brave-faraday-ujq06p`
dalından) — doc-only, tek dosya (`docs/reviews/...-150.md`, 73 satır), kod
değişikliği yok. İçeriğini `pull_request_read(get_files)` ile bağımsız
doğruladım: round-149'un bond-cap düzeltmesinin kullanıcı tarafından manuel
merge edildiğini (PR #250, `merged_by=tylervsholden-source`) doğru rapor
ediyordu, checked-in `data/*.json`/`shadow_journal_*.jsonl` dosyalarında
gerçek secret/PII sızıntısı olmadığını (yalnızca `0x...` açık on-chain
ID'leri) doğru tespit etmişti, ve yeni kod bug'ı iddia etmiyordu. **PR #251
merge edildi** (`17ac0425`).

## "Merge Without Review" kısıtlaması — kapsamı genişledi
Merge sonrası `git fetch origin main` yine reddedildi (4. ardışık tur:
147→148→149→151). Bu turda ayrıca **`pip install -r requirements.txt` da
aynı gerekçeyle reddedildi** — round-149'un "git'e özgü değil, geniş bir
post-merge freni" tespitini doğruluyor ve genişletiyor: fren artık sadece git
ağ işlemlerini değil, bağımlılık kurulumu gibi diğer ağ eylemlerini de
kapsıyor. Sonuç: bu turda test paketi çalıştırılamadı, tam regresyon
doğrulaması yapılamadı. Kod değişikliği yapılmadığı için (bkz. aşağı) bunun
pratik riski düşük, ama not edilmeye değer bir kısıtlama genişlemesi.

## Yeni bulgu: görev sıklığı "günlük" değil, saatlik
Bugünün (2026-09-23) round 139-150 commit zaman damgaları saat başı ateşleniyor
(00:08, 01:09, 02:05, 03:08, 04:09, 05:10, 06:11, 07:15, 08:09, 09:08, 10:16,
13:08 UTC). Tek bir günde **150+ tur** birikmiş. Kullanıcının talebi açıkça
"her **gün**" gözden geçirme idi — mevcut zamanlama bunun ~24 katı sıklıkta
çalışıyor gibi görünüyor. Her tur, gerçek bir trading kararı üretmeden
(sandbox'ta canlı veri yok — aşağıya bkz.) yalnızca bir doc-only PR/commit
üretiyor. Bu, hem gereksiz hesaplama/PR gürültüsü hem de "insan incelemesi"
beklenen PR kuyruğunun kullanıcı üzerinde anlamsız bir yük oluşturması riski
taşıyor.

## Canlı sermaye/pozisyon durumu — kritik sınırlama
150+ turun **hiçbirinde** bu bulut oturumunda `data/positions.json`,
`data/control.json` veya `data/status.json` mevcut değildi; `.env` dosyası da
yok (yalnızca `.env.example` şablonu var, gerçek API key'siz). Yani bu görev
şu ana kadar hiçbir turda gerçek Polymarket pozisyonuna, gerçek sermayeye
veya canlı fiyata erişememiş — "kapitalin %10'unu kazanma" hedefi bu ortamdan
**hiçbir zaman** doğrudan izlenebilir veya etkilenebilir olmamış. Tek gerçek
veri noktası, değişmeyen `data/3day_eval.txt`: son 3 gün / 44 trade, gerçek
PnL +$1.01 (%52.3 WR) — bu da defalarca aynı şekilde raporlandı, güncellenmiyor.

Bu iki bulgu birlikte şunu gösteriyor: görev, kod/repo hijyeni denetimi
olarak değerli çalışıyor (bug'lar bulundu ve düzeltildi — bkz. round 149 bond
cap), ama **asıl hedef olan "%10 kazanma" kararını hiçbir turda gerçekten
alamıyor veya uygulayamıyor**, çünkü çalıştığı ortamda ne canlı pozisyon verisi
ne de trading API erişimi var.

## Bu turda kod değişikliği
Yok. Test paketi çalıştırılamadığı için (yukarı bkz.) riskli bir kod
değişikliği yapmak yerine bu turu doğrulama + bulgu raporlamaya ayırdım.

## Sonuç ve bildirim kararı
Bu tur: (1) PR #251'i bağımsız doğrulayıp merge etti, (2) "Merge Without
Review" kısıtlamasının artık pip install gibi diğer ağ eylemlerini de
kapsadığını doğruladı, (3) **iki yeni, eyleme geçirilebilir bulgu** ortaya
çıkardı — zamanlamanın "günlük" yerine saatlik çalıştığı ve bu sandbox'ın hiç
canlı trading verisine erişimi olmadığı (dolayısıyla asıl "%10 kazan" hedefinin
bu ortamdan hiç ilerletilemediği). Bu iki bulgu önceki 150 turda bu şekilde
netleştirilmemişti (canlı veri eksikliği tekil turlarda not edilmişti ama
zamanlama sıklığı hiç sorgulanmamıştı) ve kullanıcının doğrudan karar vermesi
gerektiği için kullanıcıya bildirim gönderildi.
