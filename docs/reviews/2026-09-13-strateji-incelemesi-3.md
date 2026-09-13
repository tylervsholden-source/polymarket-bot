# Günlük Strateji İncelemesi — 2026-09-13 (3. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Bugünkü görev talimatı, hedefe
ulaşmak için gereken kararları alma ve uygulama yetkisi verdi.

## Durum özeti
- Bu checkout'ta da `data/control.json`/`.env` yok; `data/positions.json`
  içindeki $169 sermaye/PnL rakamları 2026-03 tarihli eski sim verisi —
  bugünün gerçek canlı sermaye durumunu yansıtmıyor (önceki incelemelerin
  de not ettiği gibi). Açık PR yoktu, `origin/main` güncel.
- Odak: günün ilk iki çalışmasının bulduğu kalıba (aynı `9b5fd52`
  commit'inin CLAUDE.md non-negotiable kurallarını sessizce gevşetmesi)
  uyan **başka** bir kural ihlali arandı — bu sefer "Max tek pozisyon:
  portföyün %20'si (Kelly override yapmaz)" kuralı uçtan uca izlendi.

## Bulunan ve düzeltilen hata

### Survival-mode min-bet tabanı, %20 pozisyon tavanını çiğniyordu
`agents/orchestrator.py`'nin bahis boyutlandırma bloğu (yine `9b5fd52`'den,
`bet_size = max(self._min_bet, min(self._max_bet, signal.size))` →
capital-skalalı bir min/max banda dönüştürülmüştü):

```python
if capital < 20:
    _cap_pct = 0.80   # survival mode
    _min_pct = 0.40
...
_effective_min = max(1.0, min(self._min_bet, capital * _min_pct))
...
bet_size = max(_effective_min, min(_effective_max, signal.size))
```

`strategies/kelly_criterion.py:127` Kelly'nin kendi `signal.size`'ını zaten
`capital * max_position_pct` (%20) ile sınırlıyor. Ama yukarıdaki
`_effective_min` tabanı, düşük sermayede (`capital < $20`) Kelly'nin bu
%20'lik kararını **yukarı** override edebiliyordu:

| Sermaye | Eski `_effective_min` | % of capital | Kural |
|---------|------------------------|---------------|-------|
| $5      | $2.00                  | %40           | İhlal |
| $8      | $3.00                  | %37.5         | İhlal |
| $10     | $3.00                  | %30           | İhlal |
| $15     | $3.00                  | %20           | Sınırda |
| $19     | $3.00                  | %15.8         | OK |

`bet_size = max(_effective_min, ...)` olduğu için, Kelly düşük bir edge/
confidence nedeniyle örneğin $10 sermayede $1 önerse bile taban onu $3'e
(%30) zorluyordu — bu tam olarak CLAUDE.md'nin "Kelly override yapmaz"
dediği şey. Bu, dünkü OPT-5/stop-loss/MAX_OPEN_POSITIONS bulgularıyla
birebir aynı desen: aynı squash commit, aynı şekilde doğrulanamaz bir
tuning yorumu ("survival mode"), aynı şekilde hiçbir testte yakalanmıyor
(`tests/` içinde bu sizing bloğunu hedefleyen tek bir test yoktu).

**Düzeltme:** Sizing mantığını `agents/orchestrator.py` içinde saf,
test edilebilir bir `compute_bet_size()` fonksiyonuna çıkardım (davranış
aynı, sadece izole edildi) ve sonuna sermaye tabanlı bir tavan ekledim:

```python
position_cap = capital * max_position_pct   # CLAUDE.md: %20 (Kelly override yapmaz)
effective_min = min(effective_min, position_cap)
effective_max = min(effective_max, position_cap)
```

Böylece survival-mode tabanı/tavanı hiçbir zaman %20'lik sınırı aşamıyor;
sermaye çok düşükse (%20'si $1'in altındaysa, örn. capital<$5) bahis artık
kurala uymak için tamamen atlanıyor (aşırı düşük boyutta işlem açmak
yerine "Capital too low for any trade" ile pas geçiliyor) — bu, kuralı
gevşetmek yerine, kuralın gerektirdiği daha muhafazakâr sonuç.

`tests/test_bet_size_position_cap.py` eklendi: survival-mode tabanının/
tavanının hiçbir girdide %20'yi aşmadığını, normal sermaye dalının
(`>= $20`, %12 tavan) etkilenmediğini ve çok düşük sermayede ($3) "işlem
yok" sonucuna düştüğünü doğruluyor.

## Doğrulama
- `pytest tests/` → **582 passed, 2 skipped** (578'den 582'ye: yeni
  `compute_bet_size` regresyon testleri eklendi, mevcut testlerden hiçbiri
  değişmedi/bozulmadı — extraction davranışı korudu).
- `python -c "import agents.orchestrator"` → hatasız.
- Manuel doğrulama: `compute_bet_size(capital=10, signal_size=0, min_bet=3,
  max_bet=8, max_position_pct=0.20)` → önce `$3.00` (%30, ihlal), şimdi
  `$2.00` (%20, tam sınırda).

## Kullanıcıya not
Bu, aynı `9b5fd52` commit'inde bulunan üçüncü sessiz kural gevşetmesi
(dünkü stop-loss + OPT-5/MAX_OPEN_POSITIONS'dan sonra). Eğer survival-mode
tasarımı kasıtlıysa ve %20 tavanının düşük sermayede esnetilmesi
isteniyorsa, bu CLAUDE.md'de açıkça istisna olarak yazılmalı — aksi halde
gelecekteki incelemeler aynı deseni tekrar arayıp bulacaktır. Bu oturumda
kural, metninde yazıldığı gibi ("Kelly override yapmaz") kesin kabul edilip
uygulandı.

## Dokunulmayan gözlemler
- `review_bundle/`, `incident_bundle/`, `incident_bundle_v2/` altındaki
  ~37MB'lık eski repo kopyaları hâlâ commit'li — canlı koda etkisi yok,
  housekeeping kararı gerektirir, bu oturumda dokunulmadı (önceki
  incelemelerde de aynı not var).
- `data/shadow_journal_2026-03-*.jsonl` dosyaları (toplam ~100MB) repo
  içinde — eski sim verisi, canlı davranışı etkilemiyor.
