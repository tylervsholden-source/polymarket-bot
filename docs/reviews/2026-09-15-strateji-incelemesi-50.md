# Günlük Strateji İncelemesi — 2026-09-15 (50. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Bu turda yapılanlar — Monte Carlo payout formülü düzeltildi, shadow-mode kapı eklendi

49. tur, `ArbitrageEngine._maybe_run_monte_carlo()`'nun dönüş değerinin
`analyze()` tarafından hiç tüketilmediğini ve `MonteCarloSimulator`'ın
kazanç formülünün miskalibre olduğunu (`bet * net_edge * 2.0`) tespit edip
kayıt altına almış, ama "wiring blast radius" nedeniyle kod değişikliği
yapmadan bırakmıştı. Bu tur o planı uyguladı:

### 1. `strategies/monte_carlo.py` — kazanç formülü gerçek Polymarket
   ekonomisine bağlandı

Eski formül: kazanınca `w += bet * net_edge * 2.0`, kaybedince `w -= bet`,
`win_prob = 0.50 + net_edge/2`. Bu, fiyattan tamamen bağımsızdı ve EV'yi
matematiksel olarak bozuyordu — `EV/bet = 1.5*net_edge + net_edge² - 0.5`,
yani `net_edge=0.10`'da bile `EV ≈ -0.34` (kesin kayıp), `net_edge=0.30`'a
kadar hiçbir gerçekçi canlı edge (0.05-0.30) pozitif EV üretmiyordu — 49.
turun ampirik bulgusuyla birebir örtüşüyor.

Düzeltme: `simulate()` artık alınan token'ın giriş fiyatını (`price`,
0.02-0.98 arası clamp'li) parametre olarak alıyor. Polymarket ekonomisi
`bet/price` adet payda satın alınıp kazanırsa her biri $1 ödüyor:
- Kazanç: `w += bet * (1 - price) / price`
- Kayıp: `w -= bet` (değişmedi)
- `win_prob = clip(price + net_edge, 0.03, 0.97)` — `edge = true_prob -
  price - costs` tanımından `true_prob = price + net_edge`.

Doğrulama: `price=0.50` civarında `edge=0.15-0.30` aralığı artık `viable=True`
dönüyor; `price=0.30` gibi düşük fiyat + düşük edge kombinasyonlarında
yüksek varyans nedeniyle (yüksek payout ama düşük win_prob) drawdown/win-rate
kapılarından haklı olarak `viable=False` çıkıyor — bu, `data/trade_memory.json`
içindeki `CAPITAL_LOW` uyarısı ve gerçek $1000→$102 sermaye erimesiyle tutarlı:
mevcut `MAX_POSITION_PCT=0.20` boyutlandırması, düşük-fiyat/düşük-edge
sinyalleri için aşırı agresif.

### 2. `strategies/arbitrage_engine.py` — gerçek giriş fiyatı iletildi + shadow-mode kapı

- `_maybe_run_monte_carlo()` artık üçüncü parametre olarak `price` alıyor
  ve `analyze()` çağrısında `signals[0].entry_price` iletiliyor (önceden
  fiyat hiç geçilmiyordu, simülasyon sabit `price=0.50` varsayımıyla
  çalışıyordu).
- `analyze()` artık dönen `viable` bool'unu tüketiyor. `MC_GATE_ENFORCE`
  env değişkeni `false` (varsayılan) iken sinyalleri **bloklamıyor**, sadece
  `MC_GATE_SHADOW` log satırıyla "bloklardım" diye işaretliyor — 49. turun
  planladığı "önce shadow mode'da doğrula" adımı bu. `MC_GATE_ENFORCE=true`
  yapıldığında gerçek filtreleme devreye giriyor (`return []`).
- Neden hemen enforce edilmedi: formül artık ekonomik olarak tutarlı, ama
  canlıda kaç sinyalin bloklanacağını birkaç günlük shadow log olmadan
  bilmiyoruz — riskli sinyalleri fazla agresif bloklayıp botu sinyalsiz
  bırakma ihtimaline karşı bir sonraki turda log'lar okunup karar verilecek.

### 3. `tests/test_whale_tracker_outcome_side_mismatch.py` — flaky zaman-bağımlı test düzeltildi

Test suite'i çalıştırırken (Monte Carlo değişikliğinden bağımsız, `git
stash` ile main'de de doğrulandı) 2 test kırık bulundu:
`test_buy_of_down_outcome_counts_as_bearish_not_bullish` ve
`test_buy_of_up_outcome_still_counts_as_bullish`. Kök neden kod değil,
testin kendisiydi: trade'lere sabit `"2026-09-15T12:00:00Z"` timestamp'i
veriliyordu; `WhaleTracker._analyze()`'ın "akıllı para" penceresi
`now() - 6h`. Test saat 18:00 UTC'den sonra çalıştığında (bugün 19:06 UTC'de
olduğu gibi) sabit 12:00 timestamp'i pencerenin dışına düşüyor,
`smart_money_buys/sells` sıfırlanıyor ve assertion patlıyor — gerçek bir
whale-tracker mantık hatası değil, test her gün farklı saatte tetiklendiği
için kaçınılmaz biçimde flake ediyordu. Düzeltme: sabit string yerine
`_recent_ts()` helper'ı ile testin çalıştığı ana göre `now - 30dk`
timestamp'i üretiliyor, pencereye her zaman düşüyor.

## Doğrulama
- `python3 -m pytest tests/` → **744 passed, 2 skipped, 0 failed** (önceki
  çalıştırmada 2 fail vardı, whale_tracker test fix'i sonrası düzeldi).
- Manuel Monte Carlo doğrulama scripti: çeşitli `(edge, price)`
  kombinasyonlarında `mean_return`/`win_rate`/`max_drawdown`/`viable`
  çıktıları ekonomik olarak tutarlı (düşük fiyat + düşük edge → yüksek
  varyans → haklı red; orta fiyat + yüksek edge → viable).

## Sıradaki tur için
- `MC_GATE_SHADOW` log satırlarını birkaç canlı döngü boyunca izle: kaç
  sinyal "bloklanırdı" işaretleniyor, hangi edge/price aralığında.
  Aşırı agresif değilse (örn. sinyallerin çoğunu bloklamıyorsa)
  `MC_GATE_ENFORCE=true` yapılabilir.
- `data/trade_memory.json`'daki `CAPITAL_LOW` uyarısı ($102.61, başlangıcın
  %21'i) hâlâ açık — Monte Carlo kapısı devreye girdiğinde düşük-fiyat/
  düşük-edge sinyallerin bir kısmını otomatik eleyerek bu erimeyi
  yavaşlatması bekleniyor, ama kapı enforce edilene kadar bu sadece teori.
