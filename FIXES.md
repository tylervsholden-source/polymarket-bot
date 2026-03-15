# FIXES — Polymarket Bot v2

Tarih: 2026-03-15
Gözden geçiren: Claude (Sonnet 4.6)
Kaynak: ChatGPT code review bulgularına karşılık yapılan düzeltmeler

---

## Issue 1 — Capital Double-Count (KRİTİK)

**Problem:**
`_close_position` içinde `capital += amount + pnl` yapılıyordu.
Yani kapanışta hem ana para hem kâr eklendi — sermaye şişiyordu.

**Fix — `core/position_manager.py`:**
```python
# ÖNCE (yanlış):
self.data["capital"] += amount + pnl

# SONRA (doğru):
self.data["capital"] += pnl   # Sadece kâr/zarar eklenir; ana para zaten "locked"
```
`available_capital = capital - locked_in_open_positions`
Kapanışta `capital` zaten ana parayı içeriyor; üstüne eklenmez.

**Test Kanıtı:**
`tests/test_position_accounting.py::test_breakeven_close_no_double_count` — PASSED
`tests/test_position_accounting.py::test_yes_win_capital_correct` — PASSED
`tests/test_position_accounting.py::test_yes_loss_capital_correct` — PASSED

---

## Issue 2 — NO Pozisyon PnL Yanlış (KRİTİK)

**Problem:**
`unrealized_pnl` hesabında NO pozisyonlar için `current_price = yes_ask` kullanılıyordu.
NO token fiyatı `1 - yes_ask` olmalı.

**Fix — `core/position_manager.py`:**
```python
if outcome == "NO":
    current_price = 1.0 - yes_ask   # NO token fiyatı
else:
    current_price = yes_ask          # YES token fiyatı
```

**Test Kanıtı:**
`tests/test_position_accounting.py::test_no_win_capital_correct` — PASSED
`tests/test_position_accounting.py::test_no_loss_capital_correct` — PASSED

---

## Issue 3 — Sadece YES Yönü (KRİTİK)

**Problem:**
`arbitrage_engine.py` her zaman YES alıyordu.
Fiyat düşüyorsa NO daha kârlı olabilir — bu tamamen görmezden geliniyordu.

**Fix — `strategies/arbitrage_engine.py`:**
```python
yes_edge = bayesian_prob - yes_price
no_prob  = 1.0 - bayesian_prob
no_price_ask = round(1.0 - yes_price, 4)
no_edge  = no_prob - no_price_ask

if yes_edge >= no_edge and yes_edge > 0:
    direction = "YES"; token_id = market.get("yes_token_id")
elif no_edge > yes_edge and no_edge > 0:
    direction = "NO";  token_id = market.get("no_token_id")
else:
    return None
```
`TradeSignal.token_id` eklendi — orchestrator doğru tokeni CLOB'a iletir.

**Test Kanıtı:**
`tests/test_direction_logic.py::test_bullish_yields_yes_direction` — PASSED
`tests/test_direction_logic.py::test_bearish_yields_no_direction` — PASSED
`tests/test_direction_logic.py::test_neutral_no_signal` — PASSED
`tests/test_direction_logic.py::test_no_signal_entry_price_is_no_price` — PASSED

---

## Issue 4 — State Migration (Kirli positions.json)

**Problem:**
`positions.json` içinde:
- 24 kapalı pozisyon: çoğu duplike, Oscar/LOL/Spor çöpü, -0.0 pnl
- Sermaye: $0.068 (sıfırlanmış)
- `outcome: "UP"/"DOWN"` (geçersiz format — CLOB reddeder)

**Fix — `scripts/migrate_positions.py` çalıştırıldı:**
- UP → YES, DOWN → NO normalize edildi
- Duplikeler order_id ile temizlendi (24 → 1)
- Crypto dışı marketler silindi
- Sermaye `$3.59` olarak düzeltildi

**Kanıt — `data/positions.json` güncel hali:**
```json
{
  "capital": 3.59,
  "positions": {},
  "closed": [
    {
      "order_id": "0xcf5ce20cd5bbb31bbcfcaec996351b76e3ecbe6c",
      "question": "Solana Up or Down - March 14, 12:30PM-12:45PM ET",
      "outcome": "YES",
      "pnl": 0.0
    }
  ]
}
```

---

## Issue 5 — Backtest Look-Ahead Bias

**Problem:**
Backtest'te `SignalAgent` (Claude AI) kullanılıyordu.
AI geçmiş market sonuçlarını "tahmin" ederken cevabı biliyordu — look-ahead bias.
Ayrıca NO-tarafı hiç simüle edilmiyordu.

**Fix — `backtesting/engine.py` yeniden yazıldı:**
- Claude AI tamamen kaldırıldı
- Neutral Bayesian prior (sadece piyasa fiyatı, geçmiş spot yok)
- YES ve NO tarafları her market için simüle edilir
- Sabit random seed (reproducible)
- Docstring'de açık BIAS UYARILARI

**Kanıt:**
```python
# engine.py satır 1-30 docstring:
# BIAS UYARISI 1: Gerçek candle verisi yok (look-ahead bias yok ama sinyal da zayıf)
# BIAS UYARISI 2: Komisyon/slippage tahmini kaba
# BIAS UYARISI 3: Fill rate %100 varsayılır
```

---

## Issue 6 — Torture Agent Import Crash

**Problem:**
`import anthropic` satırı her durumda çalışıyordu.
`anthropic` kurulu değilse bot başlamıyordu.

**Fix — `agents/torture_agent.py`:**
```python
try:
    import anthropic as _anthropic
    _HAS_ANTHROPIC = True
except ImportError:
    _anthropic = None
    _HAS_ANTHROPIC = False
```
`__init__` ve `evaluate` metodlarında guard eklendi.

**Test Kanıtı:**
`tests/test_torture_agent.py::test_parse_valid_json` — PASSED
`tests/test_torture_agent.py::test_parse_json_with_nested_content` — PASSED
`tests/test_torture_agent.py::test_parse_invalid_json` — PASSED
`tests/test_torture_agent.py::test_build_prompt` — PASSED

---

## Issue 7 — Docs ile Live Code Uyuşmuyor

**Problem:**
`docs/architecture.md` WhaleTracker ve SignalAgent'ı aktif gösteriyordu.
Gerçekte bunlar kaldırılmış, yerini ArbitrageEngine almış.

**Fix — `docs/architecture.md` yeniden yazıldı:**
- Gerçek bileşenler: BayesianEstimator, ArbitrageEngine, SmartTraderTracker
- "KALDIRILDI" başlığı altında WhaleTracker, SignalAgent, MeanReversion
- Capital accounting formülü belgelenmiş
- YES/NO PnL formülleri belgelenmiş
- Backtest sınırlamaları açıkça yazılmış

---

## Issue 8 — Sabit Offset → Adaptif Pagination (KRİTİK)

**Problem:**
`get_active_markets()` sabit offset listesi `[1000,1500,2000,2500,3000,3500,4000]` kullanıyordu.
Crypto up/down marketleri Gamma API'de zaman içinde offset kayıyor (gün sonunda 4500-8500+ bandı).
Sabit liste bu marketleri tamamen kaçırıyordu → 0 sinyal.

**Fix — `core/polymarket_client.py`:**
```python
# Adaptif tarama: 0'dan 10000'e kadar 500'erli adımlarla
# Future crypto up/down bulunamayan 3 ardışık batch'te dur
step = 500; max_offset = 10_000; empty_streak = 0
offsets = list(range(0, max_offset, step))
batch_size = 4  # 4 paralel istek
for i in range(0, len(offsets), batch_size):
    results = await asyncio.gather(*[_fetch_offset(o) for o in offsets[i:i+batch_size]])
    found_future = check_future_crypto(results)
    if not found_future:
        empty_streak += 1
        if empty_streak >= 3 and i > batch_size * 3:
            break
    else:
        empty_streak = 0
```

**Performans:** 7378 market, ~1.8 saniyede tarandı.

**Sinyal Sayısı Zaman Bağımlılığı (ÖNEMLİ NOT):**
Sinyal sayısı piyasa koşullarına göre dakikadan dakikaya değişir.
- 2026-03-14 23:53 çalıştırması: 15 sinyal (ETH RSI=72 yüksek, piyasa hareketli)
- 2026-03-15 00:28 çalıştırması: 2 sinyal (BTC/ETH nötr RSI ~54-56, düşük edge)
Bu çelişki değil — aynı mantık farklı piyasa koşullarında farklı edge buluyor.
Sinyalin varlığı garantili değil; bot sadece gerçek edge varken giriyor.

**Kanıt — `logs/runtime_evidence_20260315.log`:**
```
2026-03-15 00:28: 1928 market, 2 sinyal (BTC NO, ETH NO)
Bitstamp: BTC $70840 RSI=54 chg=-0.14%, ETH $1845 RSI=56 chg=-0.09%
```

---

## Issue 9 — Pozisyon Büyüklüğü Forced-50% Bug

**Problem:**
`_evaluate_market` içinde:
```python
min_bet = max(1.0, min(1.0, capital * 0.50))  # her zaman 1.0 çıkar
if size < min_bet:
    size = round(capital * 0.50, 2)  # zorla %50 bet
```
`max(1.0, min(1.0, x))` her zaman `1.0`; ve `1.0 < min_bet` durumunda
sermayenin %50'sini zorla bahse sokuyordu. Kelly hesabını anlamsız kılıyordu.

**Fix — `strategies/arbitrage_engine.py`:**
```python
# ÖNCE (yanlış):
min_bet = max(1.0, min(1.0, capital * 0.50))
if size < min_bet:
    if capital >= min_bet:
        size = round(capital * 0.50, 2)
    else:
        return None

# SONRA (doğru):
if size <= 0 or capital < size:
    return None
```
Kelly'nin hesapladığı boyut artık doğrudan kullanılıyor.

---

## Issue 10 — Balance Sync positions.json'a Yazılmıyordu

**Problem:**
`orchestrator._sync_real_balance()` CLOB'dan bakiyeyi okuyup
`position_manager.data["capital"]` güncelliyordu ama `_save()` çağırmıyordu.
Bot restart sonrası değişiklik kayboluyordu.

**Fix — `agents/orchestrator.py`:**
```python
if balance > 0:
    prev = self.position_manager.data.get("capital", 0)
    self.position_manager.data["capital"] = balance
    self.position_manager._save()  # ← EKLENDİ
    if abs(prev - balance) > 0.01:
        logger.warning(f"Sermaye: positions.json=${prev:.4f} → CLOB=${balance:.4f}")
```

---

## Issue 11 — paper_run_report.py Hardcoded Capital

**Problem:**
`CAPITAL = 3.59` sabit kodlanmıştı. Gerçek bakiye değişince rapor yanıltıcı oluyordu.

**Fix — `paper_run_report.py`:**
```python
def _read_capital() -> float:
    """positions.json'dan capital oku. CLI arg ile override edilebilir."""
    import argparse, json, pathlib
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--capital", type=float, default=None)
    args, _ = parser.parse_known_args()
    if args.capital is not None:
        return args.capital
    try:
        p = pathlib.Path(__file__).parent / "data" / "positions.json"
        return float(json.loads(p.read_text())["capital"])
    except Exception:
        return 1.0

CAPITAL = _read_capital()
```

---

## Issue 12 — status.json Kirli (Oscar/LOL/Spor Çöpü)

**Problem:**
`data/status.json` içinde:
- Oscar/LoL/CS:GO/spor bahisleri (kapsam dışı)
- `outcome: "UP"/"DOWN"` (geçersiz format)
- 3 adet duplicate Solana pozisyonu
- `capital: 0.068` (sıfırlanmış)
- Legacy alanlar: `mr_type`, `target_price`, `whale_signal`

**Fix:**
`data/status.json` sıfırlandı (temiz başlangıç state).

---

## Issue 13 — Test Suite Ağ Bağımlılığı

**Problem:**
`test_integration.py::test_bitstamp_live` ve `test_smart_trader_refresh`
offline ortamda `ConnectionError` ile çöküyordu — CI/CD bozuk.

**Fix — `tests/test_integration.py`:**
```python
try:
    import httpx
    async with httpx.AsyncClient(timeout=5) as s:
        r = await s.get("https://www.bitstamp.net/api/v2/ticker/btcusd/")
        if r.status_code != 200:
            pytest.skip("Bitstamp API erisilemez")
except Exception:
    pytest.skip("Network erisimi yok — test atlanıyor")
```

---

## Issue 14 — Execution Path Kanıtı Yoktu

**Problem:**
Signal → Order → Position → Close zincirinin tam çalıştığına dair test yoktu.

**Fix — `tests/test_execution_path.py` (YENİ, 6 test):**
- `test_simulate_yes_order` / `test_simulate_no_order` — sim order dönüşü
- `test_yes_unrealized_pnl_math` — YES token aritmetiği
- `test_no_unrealized_pnl_math` — NO token aritmetiği
- `test_close_no_double_count` — capital double-count yok
- `test_full_sim_execution_chain` — uçtan uca: BinanceFeed mock → signal → order → position → close

---

## Test Özeti (2026-03-15)

```
pytest tests/ -v
52 passed in 3.95s
```

| Test Dosyası | Testler | Sonuç |
|---|---|---|
| test_position_accounting.py | 8 | PASSED |
| test_direction_logic.py | 5 | PASSED |
| test_execution_path.py | 6 | PASSED (YENİ) |
| test_torture_agent.py | 4 | PASSED |
| test_signal_agent.py | 7 | PASSED |
| test_position_manager.py | 8 | PASSED |
| test_kelly.py | 5 | PASSED |
| test_dashboard.py | 5 | PASSED |
| test_integration.py | 4 | PASSED (2'si ağ yoksa skip) |

---

## Değişen Dosyalar

| Dosya | Değişiklik |
|---|---|
| `core/position_manager.py` | Double-count fix, NO PnL fix, outcome storage |
| `core/polymarket_client.py` | Adaptif pagination, CONDITIONAL allowance kaldırıldı |
| `strategies/arbitrage_engine.py` | YES/NO direction, token_id routing, forced-50% bug fix |
| `strategies/bayesian.py` | Longshot bias, tiered volume, news lag |
| `agents/orchestrator.py` | token_id fix, balance sync + _save(), outcome pass |
| `agents/torture_agent.py` | Optional import, deterministic JSON parser |
| `agents/signal_agent.py` | Optional anthropic import guard |
| `paper_run_report.py` | Hardcoded capital kaldırıldı, positions.json'dan okur |
| `backtesting/engine.py` | AI kaldırıldı, NO-side eklendi, bias uyarıları |
| `docs/architecture.md` | Live koda eşitlendi |
| `data/positions.json` | State migration ile temizlendi |
| `data/status.json` | Kirli state sıfırlandı |
| `scripts/migrate_positions.py` | YENİ — tek seferlik migration scripti |
| `tests/test_position_accounting.py` | YENİ — capital/pnl doğruluk testleri |
| `tests/test_direction_logic.py` | YENİ — YES/NO direction testleri |
| `tests/test_execution_path.py` | YENİ — uçtan uca execution chain testleri |
| `tests/test_integration.py` | Network isolation (skip-if-offline) |
