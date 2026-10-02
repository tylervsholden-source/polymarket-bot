# 267. Tur Strateji İncelemesi — 2026-10-02

## Kapsam
Planlı günlük strateji incelemesi (hedef: sermayenin %10'u kadar kazanç).

## Bu turda yapılanlar
- **Açık PR kontrolü:** round-266 (#368) merge edilmiş; açık PR yok.
- **Test:** `python -m pytest tests -q` → **951 passed, 2 skipped**, regresyon yok.
- **Risk parametreleri:** `MAX_POSITION_PCT` (`strategies/arbitrage_engine.py:305`, varsayılan 0.20) ve
  `MAX_OPEN_POSITIONS` (`core/web_server.py:226`, varsayılan 5) env'den okunuyor, değişmedi.
  `.env.example:12` → `LIVE_TRADING_ENABLED=false`.
- **Canlı veri:** `data/positions.json` bu ortamda yok; `data/` altı Mart 2026 simülasyonuna ait
  (son log 2026-09-30 22:02) — canlı P&L gözlemlenemiyor, önceki turlarla aynı bulgu.

## Kod değişikliği
Yok. Yeni, maddi bir bulgu olmadığından bildirim gönderilmedi.
