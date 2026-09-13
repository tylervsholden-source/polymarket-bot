# Günlük Strateji İncelemesi — 2026-09-13 (2. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Bugünkü görev talimatı, hedefe
ulaşmak için gereken kararları alma ve uygulama yetkisi verdi.

## Durum özeti
- Bu checkout'ta yine `data/control.json`, `data/positions.json` veya
  `.env` yok → bu ortamda canlı sermaye/pozisyon durumu görülemiyor,
  bugün kapatılacak/açılacak gerçek bir pozisyon yoktu.
- Günün ilk çalışması (bkz. `2026-09-13-strateji-incelemesi.md`) zaten
  3 çakışan PR'ı temizleyip daily stop-loss wiring'i merge etmişti;
  `origin/main` bu değişiklikle güncel, açık PR yoktu.
- Bu çalışmada odak: geçmiş oturumların bulduğu kalıba uyan **yeni**
  canlı-yol (live path) kablolama hatalarını aramak — CLAUDE.md'nin
  "Temel Kurallar" bölümündeki her non-negotiable kuralın gerçekten
  canlı emir yoluna kadar uygulandığını uçtan uca doğrulamak.

## Bulunan ve düzeltilen 2 yeni hata

### 1. OPT-5 adaptif min-edge kapısı hesaplanıyor ama hiç kontrol edilmiyordu
`strategies/arbitrage_engine.py` — `effective_min_edge` (YES için 0.12,
NO için 0.18, + rejim/coin ek payları) tam CLAUDE.md/docs/architecture.md
OPT-5 spesifikasyonuna göre hesaplanıyordu, ama sonrasında `edge` ile
hiç karşılaştırılmıyordu — kodun kendi yorumu bunu doğruluyordu:
`"...EDGE_REJECT, CAUTIOUS_HOUR — hepsi kaldırıldı"`. Bu, dünkü
stop-loss hatasıyla **aynı commit'ten** (`9b5fd52`) geliyor ve aynı
şekilde doğrulanamaz bir "kaldırıldı" yorumuna dayanıyordu.

Etkisi: CLAUDE.md kural 5 ("Min edge eşiği: 0.05") ve OPT-5'in tüm
adaptif eşik mantığı fiilen devre dışıydı. Gerçek kapı sadece Kelly
boyutlandırma sonrası `edge < 0.03` ve `$3` altı boyut kombinasyonunda
devreye giriyordu — yani 0.03 üstü her pozitif edge, tasarlanan
0.12-0.19+ eşiklerini hiç görmeden emre gidiyordu. Bu doğrudan hedefe
(10% kazanç) zarar veriyor: motorun kendi verisiyle kalibre ettiği
kalite filtresi bypass ediliyordu.

**Düzeltme:** `effective_min_edge` hesaplandıktan hemen sonra
`if edge < effective_min_edge: return None` (EDGE_REJECT log'u ile)
eklendi. Testler bu gate'in var olmadığını varsayarak yazılmış birkaç
sınır-değer senaryosunu (NO ask=0.46 → max ulaşılabilir edge ≈ eşiğin
tam altında) kullanıyordu; bu testlerin fixture fiyatlarını (no_ask
0.46→0.35) düşürerek asıl test ettikleri şeyi (YES/NO token-id seçimi)
bozmadan payı büyüttüm. Yeni `tests/test_opt5_min_edge_gate.py` bu
gate'in kendisini kilitliyor (eşik altı → None, eşik üstü → sinyal).

### 2. `MAX_OPEN_POSITIONS` varsayılanı "max 5" kuralını çiğniyordu
`agents/orchestrator.py:101` — env değişkeni ayarlanmamışsa varsayılan
**7** idi (yorum: `"PIVOT: raised to 7 (2 dir + 5 maker)"`), yine aynı
`9b5fd52` commit'inden. Bu hem CLAUDE.md'nin non-negotiable
"Aynı anda max 5 açık pozisyon" kuralını hem de `.env.example`
(`MAX_OPEN_POSITIONS=3`) ve `core/web_server.py`'nin kendi varsayılanını
(5) çelişiyordu — hatta `orchestrator.py`'nin kendi içindeki bir yorum
(satır 597) zaten "max_open_positions (5) yeterli koruma" diyerek 5
varsayıyordu. Varsayılan **5**'e düzeltildi.

## Doğrulama
- `pytest tests/` → **578 passed, 2 skipped** (576'dan 578'e: yeni
  OPT-5 gate regresyon testleri eklendi).
- Değişen tüm testler (`test_direction_logic.py`,
  `test_no_side_execution_path.py`) tek tek okunup, edge-eşiği dışında
  test ettikleri asıl davranış (token-id seçimi, entry_price,
  side_diagnostics) korunduğu doğrulandı — sadece fixture fiyatları
  yeni gate'i geçecek şekilde kalibre edildi.

## Kullanıcıya not
Bu ikisi de dünkü stop-loss durumuyla aynı desende: `9b5fd52` squash
commit'i CLAUDE.md'nin birden fazla non-negotiable kuralını sessizce
gevşetmiş görünüyor. Eğer bu gevşetmelerden biri gerçekten bilinçli bir
karardıysa (örn. min-edge gate'inin kaldırılması veya 7 pozisyon limiti
gerçekten istendiyse), CLAUDE.md güncellenmeli — aksi halde her günlük
inceleme aynı kalıbı tekrar bulup düzeltmeye çalışacak.

## Dokunulmayan gözlemler
- `review_bundle/`, `incident_bundle/`, `incident_bundle_v2/` altında
  436 dosyalık eski repo kopyaları hâlâ git'e commit'li (~37MB) —
  canlı koda etkisi yok, ayrı bir housekeeping kararı gerektirir, bu
  oturumda dokunulmadı.
- `data/*.bak`, `positions_backup.json`, `bot_log.txt` gibi dosyalardaki
  sermaye/PnL rakamları (ör. $140, $169) 2026-03 tarihli eski
  sim/test verileri — bu checkout'ta gerçek canlı sermaye durumu yok,
  bu rakamlar bugünün performansını yansıtmıyor.
