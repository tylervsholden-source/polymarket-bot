"""Live trading self-audit — denetim raporu."""
import json
from collections import defaultdict
from pathlib import Path

data = json.load(open("data/positions.json"))
closed = data["closed"]

# Bugunku tradeler (yeni model)
new_trades = [t for t in closed if "March 17, 6:" in t.get("question","") or "March 17, 7:" in t.get("question","") or "March 18" in t.get("question","")]

print("=" * 70)
print("CANLI TRADING DENETIM RAPORU")
print("=" * 70)

wins = [t for t in closed if t["result"] == "WIN"]
losses = [t for t in closed if t["result"] == "LOSS"]
neutrals = [t for t in closed if t["result"] == "NEUTRAL"]

print(f"Toplam kapanan: {len(closed)}")
print(f"  WIN: {len(wins)} | LOSS: {len(losses)} | NEUTRAL: {len(neutrals)}")
total_pnl = sum(t.get("pnl", 0) for t in closed)
print(f"  Toplam PnL: ${total_pnl:+.2f}")
if wins or losses:
    wr = len(wins) / (len(wins) + len(losses)) * 100
    print(f"  Win Rate: {wr:.1f}%")
print()

print("=" * 70)
print("YENI MODEL TRADELERI (bugun)")
print("=" * 70)
for t in new_trades:
    q = t.get("question", "")[:55]
    d = t.get("outcome", "?")
    e = t.get("entry_price", 0)
    c = t.get("close_price", 0)
    p = t.get("pnl", 0)
    r = t.get("result", "?")
    a = t.get("amount", 0)
    print(f"  {r:7s} | {d:3s} @ {e:.3f} -> {c:.3f} | ${a:.2f} | PnL: ${p:+.2f} | {q}")

new_wins = [t for t in new_trades if t["result"] == "WIN"]
new_losses = [t for t in new_trades if t["result"] == "LOSS"]
new_pnl = sum(t.get("pnl", 0) for t in new_trades)
if new_wins or new_losses:
    nwr = len(new_wins) / (len(new_wins) + len(new_losses)) * 100
    print(f"\n  Yeni model WR: {len(new_wins)}W/{len(new_losses)}L = {nwr:.0f}%")
    print(f"  Yeni model PnL: ${new_pnl:+.2f}")

print()
print("=" * 70)
print("TRADE JOURNAL DENETIMI")
print("=" * 70)
journal = Path("data/trade_journal.jsonl")
if journal.exists():
    lines = journal.read_text().strip().split("\n")
    entries = [json.loads(l) for l in lines]
    attempts = [e for e in entries if e.get("action") == "ORDER_ATTEMPT"]
    results = [e for e in entries if e.get("action") == "ORDER_RESULT"]
    drifts = [e for e in entries if e.get("event") == "CAPITAL_DRIFT"]
    accepted = [r for r in results if r.get("result") == "ACCEPTED"]
    exceptions = [r for r in results if r.get("result") == "EXCEPTION"]
    print(f"  Toplam emir denemesi: {len(attempts)}")
    print(f"  ACCEPTED: {len(accepted)}")
    print(f"  EXCEPTION: {len(exceptions)}")
    print(f"  Capital drift olaylari: {len(drifts)}")
    for e in exceptions:
        err = e.get("error", "")[:70]
        q = e.get("question", "")[:40]
        print(f"    HATA: {q} -> {err}")
    for d in drifts:
        prev = d["prev_capital"]
        new = d["new_capital"]
        drift = d["drift"]
        print(f"    DRIFT: ${prev:.2f} -> ${new:.2f} (fark: ${drift:.2f})")

print()
print("=" * 70)
print("COIN BAZLI ANALIZ (tum zamanlar)")
print("=" * 70)
by_coin = defaultdict(lambda: {"w": 0, "l": 0, "n": 0, "pnl": 0.0})
for t in closed:
    q = t.get("question", "")
    coin = "OTHER"
    for c in ["Solana", "Bitcoin", "Ethereum", "XRP", "Dogecoin", "BNB", "Hyperliquid"]:
        if c in q:
            coin = c
            break
    r = t.get("result", "")
    if r == "WIN":
        by_coin[coin]["w"] += 1
    elif r == "LOSS":
        by_coin[coin]["l"] += 1
    else:
        by_coin[coin]["n"] += 1
    by_coin[coin]["pnl"] += t.get("pnl", 0)

for coin in sorted(by_coin.keys(), key=lambda x: by_coin[x]["pnl"], reverse=True):
    d = by_coin[coin]
    total = d["w"] + d["l"]
    wr = d["w"] / total * 100 if total > 0 else 0
    print(f"  {coin:12s}: {d['w']}W/{d['l']}L/{d['n']}N | WR={wr:5.1f}% | PnL=${d['pnl']:+.2f}")

print()
print("=" * 70)
print("SORUNLAR & AKSIYON PLANI")
print("=" * 70)
print(f"  Mevcut sermaye: ${data['capital']:.2f}")
print(f"  Acik pozisyon: {len(data['positions'])}")
print()
print("  [1] CLOB min size $1 — $0.33-0.35 fiyatli NO tokenlarinda")
print("      amount*price < $1 reddediliyor. FIX: min amount = ceil(1/price)")
print("  [2] NO tradeler NEUTRAL rejimde gecti — regime filtresi bypass edilmis")
print("      Bitcoin NO LOSS bundan kaynakli. INVESTIGATE.")
print("  [3] Entry window 120s cok dar — cogu sinyal zamanlamaya yetisemiyor")
print("      Solana gibi iyi sinyaller 'TOO_EARLY' yuzunden 2-3 kez atlanmis")
print("  [4] Sermaye $0.78'e dustukten sonra $1 min bet > sermaye")
print("      Bot artik islem yapamiyor. CLOB drift sonrasi $2.75 olacak")
