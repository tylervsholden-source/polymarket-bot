# Günlük Strateji İncelemesi — 2026-09-13 (13. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum özeti
- Bu çalışma başladığında **bir açık PR** vardı: #30 (12. çalışma) —
  `get_adaptive_params()`'ın ardışık-kayıp bloğunun DEFENSIVE/SURVIVAL
  `aggression` etiketini yanlışlıkla AGGRESSIVE'e ezmesini düzeltiyordu
  (parayı etkilemiyor, sadece log doğruluğu).
- Doğrulama: izole `git worktree`'de PR branch'i checkout edilip bağımlılıklar
  kuruldu, `pytest tests/` → **601 passed, 2 skipped** — PR body'sinin
  iddiasıyla birebir eşleşti. **PR #30 squash-merge edildi** (main artık
  `04de109`).
- `data/control.json`/`data/positions.json`/`.env` bu ortamda yok → gerçek
  API kimlik bilgisi veya canlı pozisyon yok, bugün kapatılacak/açılacak
  gerçek bir pozisyon yoktu. `data/positions.json.bak` içindeki Oscar/e-spor/
  spor market kayıtları, mevcut `_pre_filter` whitelist'inden önceki eski
  bir yedek — canlı kod bugün bu marketlere asla emir gönderemez (aşağıda
  ayrıca doğrulandı).

## Bugünkü derin inceleme: daha önce hiç dokunulmamış dosyalara odaklanıldı

Önceki 12 rapor `autonomous_engine.py`, `coordinator.py`,
`kelly_criterion.py`, `position_manager.py`, `trade_analyzer.py`, OPT-1..6
gate'lerini, `min_bet`/`MIN_MARKET_VOLUME`/stop-loss wiring'ini zaten
doğrulamıştı. Bugün bunun yerine incelenenler:

1. **`agents/orchestrator.py`** — 2376 satırın tamamı uçtan uca okundu;
   sermaye/açık pozisyon sayısı/exposure her döngüde canlı yeniden
   hesaplanıyor, sağlam. `directional_count`/`MAX_DIRECTIONAL` cache'i
   döngü içinde `add_position` sonrası artmıyor (potansiyel gelecekteki
   kırılganlık) ama `_limit_coins_per_period` + 5m/15m-only ön filtre
   döngü başına onaylanan sinyali yapısal olarak ≤2 ile sınırladığı için
   bugün canlı bir hata değil — izlenmesi gereken not olarak bırakıldı,
   dokunulmadı (CLAUDE.md "Sadelik").
2. **`core/polymarket_client.py`** `place_order` — fiyat bump, min-notional/
   size rounding, GTC fill-poll/cancel mantığı incelendi, sağlam.
3. **`agents/resilience.py`** — `with_retry` tanımlı ama repo genelinde
   canlı yolda hiç çağrılmıyor (grep ile doğrulandı) → retry kaynaklı
   çift-emir riski yok.
4. **`agents/subagents/research_agent.py`, `signal_agent_v2.py`,
   `base_agent.py`** — saf veri zenginleştirme/timeout wrapper, boyutlandırma
   veya gate hatası bulunamadı.
5. **Market whitelist doğrulaması** — `main.py` → `Orchestrator._pre_filter`
   (`_CRYPTO_UPDOWN_KEYWORDS`) tek gate olarak onaylandı; spor/politika/oscar
   marketlerine ulaşan hiçbir canlı kod yolu yok. `strategies/mean_reversion.py`
   hiçbir yerden import edilmiyor — dead file, çalışmıyor.

## Bugün bulunan ve düzeltilen gerçek hata: rule-based reviewer'ın counter-regime VETO'su hiç tetiklenemiyordu

### Kod incelemesi
`agents/subagents/reviewer_agent.py:346` (düzeltme öncesi):

```python
if "REGIME_OVEREXTENDED" in sig.risk_flags and any(f == "COUNTER_REGIME" for f in sig.risk_flags):
    verdict = ReviewVerdict.VETO
```

Bu, CLAUDE.md'nin kendi "Kritik Keşifler" bölümünün tanımladığı en kritik
kayıp örüntüsü için VETO kuralı: *"Regime str > 0.70 = LOSS bölgesi — Tüm
sim'lerde güçlü regime ile kayıp korelasyonu."* Ama kontrol ettiği literal
string — `"COUNTER_REGIME"` — hiçbir yerde üretilmiyor.
`agents/subagents/signal_agent_v2.py:245-248` (`_detect_risk_flags`) sadece
`"COUNTER_REGIME_NO"` ya da `"COUNTER_REGIME_YES"` ekliyor, asla çıplak
`"COUNTER_REGIME"` değil. Üstündeki yorum ("FIX-A: exact match yerine
substring kullan") önceki bir düzeltmenin substring kontrolünü exact-match'e
çevirirken yanlış literal seçtiğini gösteriyor — bu VETO koşulu kalıcı
olarak ölü kod haline gelmiş, hiçbir sinyalde asla tetiklenemez.

### Etki
Rule-based fallback nadir bir durum değil — `docs/architecture.md`'nin kendisi
*"ReviewerAgent → Claude API ile APPROVE/VETO/REDUCE (API yoksa →
rule-based fallback)"* diyor, ve `_review_with_claude`'un except bloğu her
API hatası/timeout'unda da buraya düşüyor. Rule-based yol çalıştığında,
`REGIME_OVEREXTENDED` + `COUNTER_REGIME_NO`/`COUNTER_REGIME_YES` taşıyan bir
sinyal artık VETO edilmiyor; REDUCE/APPROVE dallarına düşüyor (ör.
`regime_strength > 0.60 and direction == "NO"` → 0.6x REDUCE, ya da
confluence/edge iyi görünüyorsa doğrudan APPROVE). Somut senaryo: BTC 5dk
market, regime_strength=0.82 (UP, overextended), sinyal yönü=NO (counter-trend),
edge=0.13, confluence=0.55 → risk_flags=[`REGIME_OVEREXTENDED`,
`COUNTER_REGIME_NO`]. Beklenen: VETO. Gerçekleşen (düzeltme öncesi):
`len(risk_flags)>=3`? Hayır (sadece 2 flag) → `confluence<0.4`? Hayır →
`regime_strength>0.60 and NO`? Evet → REDUCE 0.6x → emir, tamamen
engellenmek yerine `bet_size`'ın %60'ı büyüklüğünde gönderiliyor. Tam olarak
CLAUDE.md'nin tanımladığı bounce/overextended-regime kayıp örüntüsüne giren
trade'ler için koruma sessizce devre dışı kalmıştı.

### Düzeltme
`_rule_based_review`'daki kontrol artık `f.startswith("COUNTER_REGIME")`
kullanıyor, böylece hem `COUNTER_REGIME_NO` hem `COUNTER_REGIME_YES`
eşleşiyor ve VETO gerçekten tetiklenebiliyor. Diğer üç VETO/REDUCE koşulu
(`WHALE_OPPOSITION`, `RSI_OVERBOUGHT`/`RSI_OVERSOLD`) zaten doğru
`f == "..."` eşleşmesi kullanıyordu — kontrol edildi, `signal_agent_v2.py`'de
üretilen flag adlarıyla birebir eşleşiyorlar, dokunulmadı.

`tests/test_reviewer_agent_counter_regime_veto.py` eklendi (3 yeni test):
1. `COUNTER_REGIME_NO` + `REGIME_OVEREXTENDED` → VETO tetikleniyor.
2. `COUNTER_REGIME_YES` + `REGIME_OVEREXTENDED` → VETO tetikleniyor.
3. Sadece `REGIME_OVEREXTENDED` (counter-regime flag'i olmadan) → bu kural
   VETO tetiklemiyor (regresyon: kural aşırı geniş hale getirilmedi).

## Doğrulama
- `python3 -c "import agents.orchestrator"` → hatasız.
- `pytest tests/` → **604 passed, 2 skipped** (601'den 604'e: 3 yeni test
  eklendi, mevcut testlerden hiçbiri bozulmadı).

## Sonuç
13. çalışma önce açık PR'ı (#30, aggression etiket düzeltmesi) doğrulayıp
merge etti, sonra daha önce hiç derinlemesine incelenmemiş dosyalara
(`orchestrator.py` tam okuma, `polymarket_client.py`, `resilience.py`,
research/signal/base subagent'ları, market whitelist zinciri) odaklanarak
yeni bir sermaye-etkili hata aradı. Bu sefer gerçek ve ciddi bir hata
bulundu: rule-based reviewer'ın counter-regime-in-overextended-regime VETO
kuralı, yanlış bir string literal'i yüzünden hiç tetiklenemiyordu — tam da
CLAUDE.md'nin "en kötü kayıp örüntüsü" dediği senaryoda korumayı sessizce
devre dışı bırakıyordu. Düzeltme minimal (tek satır, exact-match →
prefix-match) ve regresyon testleriyle kilitlendi.
