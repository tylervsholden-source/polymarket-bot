# Günlük Strateji İncelemesi — 2026-09-13 (4. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum özeti
- Oturum başında açık **PR #21** bulundu ("cap survival-mode min-bet floor
  at CLAUDE.md's 20% position limit") — günün 3. çalışmasının çıktısı.
  Branch'i worktree'ye çekip diff'i okudum, `pytest tests/` yeniden
  çalıştırdım (**582 passed, 2 skipped**, PR'ın iddiasıyla eşleşti) ve
  squash-merge ettim (`cf72288`).
- `data/control.json`/`.env` bu checkout'ta yok; `data/positions.json` da
  yok (önceki incelemelerde referans verilen $169 sermaye rakamı
  `data/positions_backup.json`'dan, 2026-03 tarihli eski sim verisi —
  bugünün gerçek canlı sermaye durumunu yansıtmıyor). Çalışan başka açık PR
  kalmadı, `origin/main` güncel.
- Odak: aynı kalıp (`9b5fd52` commit'inin CLAUDE.md'de "aktif" olarak
  belgelenen bir güvenlik/risk kontrolünü sessizce devre dışı bırakması) —
  bu kez **v9 Optimizasyonları (Tümü Aktif)** tablosundaki 6 OPT tek tek
  `strategies/arbitrage_engine.py::_evaluate_market` içinde izlendi.

## İnceleme: OPT-1 … OPT-6 durumu

| OPT | CLAUDE.md iddiası | Kod durumu |
|-----|--------------------|------------|
| OPT-1 Regime Strength Cap | `str>0.75` → NO block | Sert blok kaldırılmış, ama `_regime_addon = regime_str × 0.08/0.10` ile OPT-5'in min-edge formülüne gömülü — aynı korumayı sürekli bir eşik olarak sağlıyor. Canlı, kasıtlı tasarım. |
| OPT-2 Max 1 Coin/Period | COIN_LIMIT 2→1 | `market_cooldowns.json` + coin blacklist mekanizması üzerinden uygulanıyor (bu oturumda ayrıca doğrulanmadı, önceki günlerde incelenmiş). |
| OPT-3 Momentum Deceleration Guard | Son 3 mumda \|change\| azalıyorsa → NO block | **Tamamen ölü kod.** `momentum_decelerating` satır ~429'da dikkatle hesaplanıyor (candle_changes karşılaştırması + volatilite fallback'i) ama `_evaluate_market`'te hiçbir yerde okunmuyor — satır 1524'te sadece `# MOMENTUM_DECEL kaldırıldı — sinyal neyse o` yorumu var, gate'in kendisi yok. **Bugün düzeltildi (aşağıda).** |
| OPT-4 Volume Confirmation Gate | `vol_ratio<1.2` → NO block | Aktif ama eşik gevşetilmiş: `VOLUME_GATE_MIN=0.8` (env ile), kodda "relaxed for live" notuyla. Kasıtlı/belgeli bir ayar, dokunulmadı. |
| OPT-5 Adaptive Edge Threshold | `min_edge = base + regime_str×0.08` | Dünkü (#20) incelemede zaten onarıldı, bugün değişmedi. |
| OPT-6 Loss Slot Cooldown | Kayıp sonrası 1 periyot atla | `9b5fd52`'de kaldırılmış, `d03c2fa`/`6574624`'te (12 Eylül) tekrar onarılıp sonra dead-code temizliği yapılmış — bugün dokunulmadı. |

## Bulunan ve düzeltilen hata

### OPT-3 Momentum Deceleration Guard hiç çalışmıyordu
`momentum_decelerating` flag'i hem "spot verisi var" hem "spot verisi yok"
dalında (satır 429-440 ve 488) tanımlanıp taşınıyordu, ama `direction`
belirlendikten sonraki gate zincirinde (GATE 0-4, satır 1468-1524)
hiçbir koşulda kontrol edilmiyordu — yalnız bir yorum satırı kalmıştı.
CLAUDE.md'nin "v9 Optimizasyonları (Tümü Aktif)" tablosu bu guard'ı 6
aktif optimizasyondan biri olarak listeliyor; kod bunu belgelenmiş
davranışın aksine hiç uygulamıyordu.

Düzeltme, OPT-5/compute_bet_size'daki aynı yaklaşımla: saf, test edilebilir
bir `_momentum_decel_blocks_no(direction, momentum_decelerating)` fonksiyonu
eklendi ve yorum satırının yerine gerçek gate konuldu:

```python
def _momentum_decel_blocks_no(direction: str, momentum_decelerating: bool) -> bool:
    return direction == "NO" and momentum_decelerating
```

```python
if _momentum_decel_blocks_no(direction, momentum_decelerating):
    logger.info(f"MOMENTUM_DECEL_BLOCK: ... NO blocked — bounce riski (OPT-3)")
    return None
```

CLAUDE.md'nin spesifikasyonuna tam sadık kalındı: sadece NO yönünü
engelliyor, YES'e dokunmuyor; override yok (OPT-4'ün aksine, spec'te
"edge>0.15 override eder" gibi bir istisna belgelenmemiş).

`tests/test_opt3_momentum_decel_gate.py` eklendi — saf fonksiyonu 4
kombinasyonda (NO+decel=block, YES+decel=geç, NO+no-decel=geç,
YES+no-decel=geç) doğruluyor.

## Doğrulama
- `pytest tests/` → **586 passed, 2 skipped** (582'den 586'ya: 4 yeni OPT-3
  regresyon testi eklendi, mevcut testlerden hiçbiri bozulmadı/değişmedi —
  bu, önceden hiçbir testin "deceleration sırasında NO geçer" davranışına
  bağımlı olmadığını, yani bu gate'in eksikliğinin önceden test
  edilmeyen bir boşluk olduğunu doğruluyor).
- `python -c "import strategies.arbitrage_engine"` → hatasız.

## Kullanıcıya not
Bu, `9b5fd52`'de aynı anda silinen kurallardan biri; günün 1-3. çalışmaları
stop-loss, MAX_OPEN_POSITIONS/OPT-5 ve %20 pozisyon tavanını onardı, bu da
dördüncüsü. OPT-1 ve OPT-4'ü kasıtlı/belgeli tasarım değişikliği olarak
değerlendirip **dokunmadım** (OPT-1 → OPT-5'in regime-addon'una evrilmiş,
OPT-4 → eşiği gevşetilmiş ama hâlâ aktif); OPT-3'te ise hiçbir yerde
alternatif bir koruma yoktu, o yüzden spec'e göre geri eklendi. Eğer OPT-3
kasıtlı olarak kaldırıldıysa (ör. live performansı düşürdüğü için), bunun
CLAUDE.md'de açık istisna olarak yazılması gerekir — aksi halde gelecekteki
incelemeler aynı deseni arayıp tekrar ekleyecektir.

## Dokunulmayan gözlemler
- `agents/orchestrator.py`'deki `self._regime_decay_pause` (v8 "decay
  guard") hâlâ hesaplanıp set ediliyor ama hiçbir yerde okunmuyor — CLAUDE.md
  v9 tablosunda bu isimle bir OPT listelenmediği için (v8'in decay guard'ı
  görünüşe göre v9'da OPT-1/OPT-5 ile değiştirilmiş), bunu "gevşetilmiş bir
  non-negotiable kural" değil, kullanılmayan eski kod olarak değerlendirdim
  ve dokunmadım. Gelecekte temizlenebilir.
- `review_bundle/`, `incident_bundle/`, `incident_bundle_v2/` altındaki eski
  repo kopyaları ve `data/shadow_journal_2026-03-*.jsonl` dosyaları hâlâ
  commit'li (önceki incelemelerin de notu) — canlı koda etkisi yok, bu
  oturumda dokunulmadı.
